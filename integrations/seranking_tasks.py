from celery import shared_task
from django.core.exceptions import PermissionDenied
from django.db import transaction
from projects.models import Project
from projects.permissions import can_edit, can_manage
from sources.models import ImportBatch, SourceRecord
from .seranking import ProjectClient, SERankingError, REPORTS, fetch_report


def check_access(batch_id):
    batch = ImportBatch.objects.select_related('created_by', 'project', 'seranking_connection__owner').get(pk=batch_id)
    connection = batch.seranking_connection
    if (batch.source != 'seranking' or batch.method != 'api' or not connection or not connection.active
            or connection.project_id != batch.project_id or not can_edit(batch.created_by, batch.project)
            or not can_manage(connection.owner, batch.project)):
        raise PermissionDenied
    return batch


@shared_task(ignore_result=True, time_limit=1800, soft_time_limit=1740)
def process_seranking(batch_id):
    try:
        batch = check_access(batch_id)
        # Atomic claim prevents duplicate workers publishing the same snapshot.
        if not ImportBatch.objects.filter(pk=batch_id, status='pending').update(status='processing', error=''):
            return
        records, warnings = fetch_report(ProjectClient(), batch, lambda: check_access(batch_id))
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=batch.project_id)
            batch = ImportBatch.objects.select_for_update().get(pk=batch_id)
            connection = batch.seranking_connection
            type(connection).objects.select_for_update().get(pk=connection.pk)
            check_access(batch_id)
            if batch.status != 'processing':
                return
            scope = REPORTS[batch.request_metadata['type']][1]
            excluded, unlinked, accepted = 0, 0, []
            from projects.hosts import allows_url
            for record in records:
                url = record['data'].get('url')
                if scope == 'summary' or allows_url(batch.project, url):
                    accepted.append(record)
                elif not url and batch.request_metadata['type'] == 'rankings':
                    # Retain unlinked keyword history without inventing a ranking URL.
                    accepted.append(record)
                    unlinked += 1
                else:
                    excluded += 1
            SourceRecord.objects.bulk_create([SourceRecord(batch=batch, **row) for row in accepted], batch_size=100)
            batch.record_count = len(accepted)
            batch.request_metadata = {**batch.request_metadata, 'host_filter': {**batch.project.host_rules,
                'excluded_rows': excluded, 'unlinked_rows': unlinked, 'accepted_rows': len(accepted)}}
            batch.warnings = warnings + ([f'{excluded} URL-rijen buiten de projecthosts overgeslagen.'] if excluded else [])
            batch.status = 'complete'
            batch.save(update_fields=['record_count', 'status', 'request_metadata', 'warnings'])
            from sources.snapshots import classify
            classify(batch)
    except ImportBatch.DoesNotExist:
        return
    except PermissionDenied:
        ImportBatch.objects.filter(pk=batch_id, status__in=('pending', 'processing')).update(status='failed', error='Projecttoegang of SE Ranking-koppeling ingetrokken.')
    except SERankingError as exc:
        ImportBatch.objects.filter(pk=batch_id, status='processing').update(status='failed', error=str(exc))
    except Exception:
        # Exception strings and provider bodies may contain credentials: never log them.
        ImportBatch.objects.filter(pk=batch_id, status='processing').update(status='failed', error='SE Ranking-import mislukt. Probeer opnieuw.')


def enqueue_seranking(batch_id):
    try:
        process_seranking.delay(str(batch_id))
    except Exception:
        pass  # Durable pending batch can be requeued from its detail page.
