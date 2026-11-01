import logging
from celery import shared_task
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.db import transaction
from projects.models import Project
from projects.permissions import can_edit
from .models import ImportBatch, PendingFileDeletion
from .parsers import parse_file, ImportFormatError, MAX_BYTES
from .services import publish_records

logger = logging.getLogger(__name__)


@shared_task(ignore_result=True)
def process_upload(batch_id):
    try:
        project_id = ImportBatch.objects.values_list("project_id", flat=True).get(pk=batch_id)
        with transaction.atomic():
            project = Project.objects.select_for_update().get(pk=project_id)
            batch = ImportBatch.objects.select_for_update().select_related("created_by").get(pk=batch_id)
            if batch.status == ImportBatch.Status.COMPLETE:
                return
            if not can_edit(batch.created_by, project):
                raise PermissionDenied
            if not batch.file:
                raise ImportFormatError("Bronbestand ontbreekt.")
            batch.status = ImportBatch.Status.PROCESSING
            batch.save(update_fields=["status"])
            # Reading while locked prevents a simultaneous deletion from removing the file mid-read.
            with batch.file.open("rb") as stream:
                content = stream.read(MAX_BYTES + 1)
        parsed = parse_file(content, batch.original_filename)
        publish_records(batch_id=batch_id, records=parsed["records"])
    except ImportBatch.DoesNotExist:
        return  # Deleted batches must never be recreated by a late task.
    except PermissionDenied:
        ImportBatch.objects.filter(pk=batch_id).exclude(status="complete").update(status="failed", error="Projecttoegang ingetrokken.")
    except ImportFormatError as exc:
        ImportBatch.objects.filter(pk=batch_id).exclude(status="complete").update(status="failed", error=str(exc))
    except Exception:
        logger.exception("Upload processing failed for batch %s", batch_id)
        ImportBatch.objects.filter(pk=batch_id).exclude(status="complete").update(status="failed", error="Verwerking mislukt; probeer opnieuw.")


@shared_task(ignore_result=True, autoretry_for=(OSError,), retry_backoff=True, max_retries=5)
def cleanup_files():
    for item in PendingFileDeletion.objects.all().iterator():
        default_storage.delete(item.path)
        item.delete()


def enqueue_upload(batch_id):
    try:
        process_upload.delay(str(batch_id))
    except Exception:
        # Pending batches can be resubmitted from the retry endpoint; don't pretend upload failed.
        logger.exception("Could not enqueue batch %s", batch_id)


def enqueue_cleanup():
    try:
        cleanup_files.delay()
    except Exception:
        logger.exception("File cleanup queued in database; broker unavailable")
