from celery import shared_task
from django.core.exceptions import PermissionDenied
from django.db import transaction
from projects.models import Project
from projects.permissions import can_edit, can_manage
from sources.models import ImportBatch
from sources.services import publish_records
from .models import GSCConnection, APIResponsePage
from .gsc import authorized_session, fetch_search_analytics, ConnectorError


def check_batch_access(batch_id):
    batch = ImportBatch.objects.select_related("created_by", "project", "gsc_connection__owner").get(pk=batch_id)
    connection = batch.gsc_connection
    if not connection or not connection.active or connection.project_id != batch.project_id:
        raise PermissionDenied
    if not can_edit(batch.created_by, batch.project) or not can_manage(connection.owner, batch.project):
        raise PermissionDenied
    return batch


@shared_task(ignore_result=True)
def process_gsc(batch_id):
    try:
        batch = check_batch_access(batch_id)
        if batch.status == "complete":
            return
        ImportBatch.objects.filter(pk=batch_id).exclude(status="complete").update(status="processing", error="")
        with authorized_session(batch.gsc_connection) as session:
            records, pages, warnings = fetch_search_analytics(session,
                property_url=batch.request_metadata["property"], start=batch.period_start, end=batch.period_end,
                check_access=lambda: check_batch_access(batch_id))
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=batch.project_id)
            batch = ImportBatch.objects.select_for_update().get(pk=batch_id)
            check_batch_access(batch_id)
            if batch.status == "complete":
                return
            publish_records(batch_id=batch_id, records=records)
            APIResponsePage.objects.bulk_create([APIResponsePage(batch=batch, **p) for p in pages], batch_size=10)
            batch.warnings = warnings
            batch.save(update_fields=["warnings"])
    except ImportBatch.DoesNotExist:
        return
    except PermissionDenied:
        ImportBatch.objects.filter(pk=batch_id).exclude(status="complete").update(status="failed", error="Projecttoegang of bronverbinding ingetrokken.")
    except ConnectorError as exc:
        ImportBatch.objects.filter(pk=batch_id).exclude(status="complete").update(status="failed", error=str(exc))
    except Exception:
        # Never log Google's exception payload: it may contain OAuth credentials.
        ImportBatch.objects.filter(pk=batch_id).exclude(status="complete").update(status="failed", error="GSC-import mislukt; probeer opnieuw.")


def enqueue_gsc(batch_id):
    try:
        process_gsc.delay(str(batch_id))
    except Exception:
        # Pending state is retained; the retry endpoint can enqueue again.
        pass
