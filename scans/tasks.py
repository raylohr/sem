from celery import shared_task
from django.core.exceptions import PermissionDenied
from django.db import transaction
from projects.models import Project
from projects.permissions import can_edit
from sources.models import ImportBatch
from sources.services import publish_records
from .models import ScanTarget, ScanDocument
from .network import SafeClient, ScanError
from .runners import crawl, sitemap


class ScanCancelled(PermissionDenied):
    pass


def check_scan(batch_id):
    batch = ImportBatch.objects.select_related('created_by', 'project', 'scan_target').get(pk=batch_id)
    if batch.status == 'cancelled':
        raise ScanCancelled
    if not can_edit(batch.created_by, batch.project):
        raise PermissionDenied
    target = batch.scan_target
    metadata = batch.request_metadata
    if not target or not target.active or target.project_id != batch.project_id:
        raise PermissionDenied
    if not set(metadata.get('allowed_hosts', [])).issubset(set(target.allowed_hosts)):
        raise PermissionDenied
    return batch


@shared_task(ignore_result=True)
def process_scan(batch_id):
    try:
        batch = check_scan(batch_id)
        if batch.status == 'complete':
            return
        ImportBatch.objects.filter(pk=batch_id).exclude(status__in=['complete', 'cancelled']).update(status='processing', error='')
        config = batch.request_metadata
        client = SafeClient(config['allowed_hosts'], check_access=lambda: check_scan(batch_id), duration=config['duration'])
        runner = crawl if config['scan'] == 'crawl' else sitemap
        records, warnings = runner(client, url=config['url'], max_urls=config['max_urls'], max_depth=config['max_depth'])
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=batch.project_id)
            batch = ImportBatch.objects.select_for_update().get(pk=batch_id)
            check_scan(batch_id)
            if batch.status == 'complete':
                return
            publish_records(batch_id=batch_id, records=records)
            ScanDocument.objects.bulk_create([ScanDocument(batch=batch, index=index, **document) for index, document in enumerate(client.documents)], batch_size=5)
            batch.warnings = warnings
            batch.save(update_fields=['warnings'])
    except (ImportBatch.DoesNotExist, ScanCancelled):
        return
    except PermissionDenied:
        ImportBatch.objects.filter(pk=batch_id).exclude(status__in=['complete', 'cancelled']).update(status='failed', error='Projecttoegang of scandoel ingetrokken.')
    except ScanError as exc:
        ImportBatch.objects.filter(pk=batch_id).exclude(status__in=['complete', 'cancelled']).update(status='failed', error=str(exc))
    except Exception:
        ImportBatch.objects.filter(pk=batch_id).exclude(status__in=['complete', 'cancelled']).update(status='failed', error='Scan mislukt; probeer opnieuw.')


def enqueue_scan(batch_id):
    try:
        process_scan.delay(str(batch_id))
    except Exception:
        pass  # Pending batches remain visible and can be retried.
