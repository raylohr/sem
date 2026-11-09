"""Durable, permission-checked imports for the read-only Project API."""
from datetime import timedelta
from uuid import uuid4

from celery import shared_task
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from projects.models import Project
from projects.permissions import can_edit, can_manage
from sources.models import ImportBatch, SourceRecord
from .models import SERankingConnection
from .seranking import ProjectClient, SERankingError, REPORTS, configured, fetch_report

RUN_TIMEOUT = timedelta(minutes=35)


def check_access(batch_id, token=None):
    batch = ImportBatch.objects.select_related('created_by', 'project', 'seranking_connection__owner').get(pk=batch_id)
    connection = batch.seranking_connection
    if (batch.source != 'seranking' or batch.method != 'api' or not connection or not connection.active
            or connection.project_id != batch.project_id or not can_edit(batch.created_by, batch.project)
            or not can_manage(connection.owner, batch.project)):
        raise PermissionDenied
    if token and (batch.status != 'processing' or batch.request_metadata.get('worker_run', {}).get('token') != token):
        raise PermissionDenied
    return batch


def fail_run(batch_id, token, message):
    query = ImportBatch.objects.filter(pk=batch_id, source='seranking', method='api')
    if token:
        query = query.filter(status='processing', request_metadata__worker_run__token=token)
    else:
        query = query.filter(status='pending')
    query.update(status='failed', error=message)


@shared_task(ignore_result=True, time_limit=1800, soft_time_limit=1740)
def process_seranking(batch_id):
    token = None
    try:
        # Lock ordering matches retry, disconnect and project deletion.
        project_id = ImportBatch.objects.values_list('project_id', flat=True).get(pk=batch_id)
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=project_id)
            batch = ImportBatch.objects.select_for_update().get(pk=batch_id)
            if batch.status != 'pending':
                return
            batch = check_access(batch_id)
            token = uuid4().hex
            batch.request_metadata = {**batch.request_metadata, 'worker_run': {
                'token': token, 'started_at': timezone.now().isoformat()}}
            batch.status = 'processing'
            batch.error = ''
            batch.save(update_fields=['status', 'error', 'request_metadata'])
        records, warnings = fetch_report(ProjectClient(), batch, lambda: check_access(batch_id, token))
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=project_id)
            batch = ImportBatch.objects.select_for_update().get(pk=batch_id)
            SERankingConnection.objects.select_for_update().get(pk=batch.seranking_connection_id)
            batch = check_access(batch_id, token)
            scope = REPORTS[batch.request_metadata['type']][1]
            excluded, unlinked, accepted = 0, 0, []
            from projects.hosts import allows_url
            for record in records:
                url = record['data'].get('url')
                if scope == 'summary' or allows_url(batch.project, url):
                    accepted.append(record)
                elif not url and batch.request_metadata['type'] == 'rankings':
                    accepted.append(record)
                    unlinked += 1
                else:
                    excluded += 1
            SourceRecord.objects.bulk_create([SourceRecord(batch=batch, **row) for row in accepted], batch_size=100)
            batch.record_count = len(accepted)
            batch.request_metadata = {**batch.request_metadata, 'record_scope': scope,
                'worker_run': {**batch.request_metadata['worker_run'], 'finished_at': timezone.now().isoformat()},
                'host_filter': {**batch.project.host_rules, 'excluded_rows': excluded,
                                'unlinked_rows': unlinked, 'accepted_rows': len(accepted)}}
            batch.warnings = warnings + ([f'{excluded} URL-rijen buiten de projecthosts overgeslagen.'] if excluded else [])
            batch.status = 'complete'
            batch.save(update_fields=['record_count', 'status', 'request_metadata', 'warnings'])
    except ImportBatch.DoesNotExist:
        return
    except PermissionDenied:
        fail_run(batch_id, token, 'Projecttoegang of SE Ranking-koppeling ingetrokken.')
    except SERankingError as exc:
        fail_run(batch_id, token, str(exc))
    except Exception:
        # Never include credentials, provider bodies or exception strings.
        fail_run(batch_id, token, 'SE Ranking-import mislukt. Probeer opnieuw.')


def enqueue_seranking(batch_id):
    try:
        process_seranking.delay(str(batch_id))
    except Exception:
        pass  # Beat resubmits durable pending batches after broker outages.


@shared_task(ignore_result=True)
def dispatch_seranking_imports():
    """Recover already requested imports; never create or schedule new reports."""
    now = timezone.now()
    for batch in ImportBatch.objects.filter(source='seranking', method='api', status='processing').iterator():
        run = batch.request_metadata.get('worker_run', {})
        try:
            started = parse_datetime(run.get('started_at', '')) or batch.imported_at
            if timezone.is_naive(started):
                started = timezone.make_aware(started)
        except (ValueError, TypeError):
            started = batch.imported_at
        if now - started > RUN_TIMEOUT:
            query = ImportBatch.objects.filter(pk=batch.pk, status='processing')
            if run.get('token'):
                query = query.filter(request_metadata__worker_run__token=run['token'])
            else:
                query = query.filter(request_metadata=batch.request_metadata)
            query.update(status='failed', error='SE Ranking-import onderbroken of verlopen. Start de import opnieuw.')
    if not configured():
        return
    for batch_id in ImportBatch.objects.filter(source='seranking', method='api', status='pending').order_by('imported_at').values_list('pk', flat=True)[:100]:
        enqueue_seranking(batch_id)
