from itertools import islice
from django.core.exceptions import PermissionDenied
from django.db import transaction
from projects.models import Project
from projects.permissions import can_manage, can_edit
from .models import ImportBatch, SourceRecord, BatchDeletionEvent, PendingFileDeletion


@transaction.atomic
def delete_batch(*, batch_id, project, actor):
    project = Project.objects.select_for_update().get(pk=project.pk)
    if not can_manage(actor, project):
        raise PermissionDenied
    batch = ImportBatch.objects.select_for_update().get(pk=batch_id, project=project)
    BatchDeletionEvent.objects.create(project=project, project_identifier=project.pk, batch_id=batch.id, actor=actor)
    if batch.file:
        PendingFileDeletion.objects.get_or_create(path=batch.file.name)
    if batch.scan_target_id:
        from scans.work import work_paths
        for path in work_paths(batch.pk):
            PendingFileDeletion.objects.get_or_create(path=path)
    batch.delete()
    from catalog.services import prune_urls
    prune_urls(project.pk)
    from .tasks import enqueue_cleanup
    transaction.on_commit(enqueue_cleanup)


@transaction.atomic
def publish_records(*, batch_id, records):
    """Worker entry point: lock, recheck access and publish a whole batch once."""
    project_id = ImportBatch.objects.values_list("project_id", flat=True).get(pk=batch_id)
    project = Project.objects.select_for_update().get(pk=project_id)
    batch = ImportBatch.objects.select_for_update().select_related("created_by").get(pk=batch_id)
    if not can_edit(batch.created_by, project):
        raise PermissionDenied
    if batch.status in (ImportBatch.Status.COMPLETE, ImportBatch.Status.INCOMPLETE):
        return batch.record_count
    if batch.status not in (ImportBatch.Status.PENDING, ImportBatch.Status.PROCESSING):
        raise ValueError("Batch cannot be published in this state")
    iterator = iter(records)
    count = 0
    while chunk := list(islice(iterator, 50)):
        SourceRecord.objects.bulk_create([SourceRecord(batch=batch, locator=item["locator"], data=item["data"]) for item in chunk], batch_size=50)
        count += len(chunk)
    batch.record_count = count
    batch.status = ImportBatch.Status.COMPLETE
    batch.save(update_fields=["record_count", "status"])
    from catalog.services import index_batch
    index_batch(batch.id)
    return batch.record_count
