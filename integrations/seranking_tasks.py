"""Durable, permission-checked imports for the read-only Project API."""
from datetime import timedelta
from uuid import UUID, uuid4

from celery import shared_task
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from projects.models import Project
from projects.permissions import can_edit, can_manage
from sources.models import ImportBatch, SourceRecord
from .models import SERankingConnection, SERankingRefresh
from .seranking import ProjectClient, SERankingError, REPORTS, configured, fetch_report

RUN_TIMEOUT = timedelta(minutes=35)


def check_access(batch_id, token=None):
    batch = ImportBatch.objects.select_related('created_by', 'project', 'seranking_connection__owner').get(pk=batch_id)
    connection = batch.seranking_connection
    if (batch.source != 'seranking' or batch.method != 'api' or not connection or not connection.active
            or connection.project_id != batch.project_id or not can_edit(batch.created_by, batch.project)
            or not can_manage(connection.owner, batch.project)):
        raise PermissionDenied
    if batch.request_metadata.get('daily_refresh_id'):
        from .seranking_refresh import check_refresh
        check_refresh(batch.request_metadata['daily_refresh_id'], UUID(batch.request_metadata['daily_refresh_token']))
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
            if batch.request_metadata.get('daily_refresh_id') and batch.request_metadata['type'] != 'rankings':
                from .seranking_sync import content_digest
                digest = content_digest(row['data'] for row in accepted)
                previous = ImportBatch.objects.filter(seranking_connection_id=batch.seranking_connection_id,
                    source='seranking', status='complete', request_metadata__type=batch.request_metadata['type'])
                if batch.request_metadata['type'] == 'audit_pages':
                    previous = previous.filter(request_metadata__audit_id=batch.request_metadata['audit_id'])
                previous = previous.first()
                if previous and digest == (previous.request_metadata.get('content_digest') or
                        content_digest(previous.records.values_list('data', flat=True).iterator())):
                    outcome = {'status': 'unchanged', 'records': previous.record_count, 'batch_id': str(previous.pk)}
                    batch.delete()  # Automatic, unpublished attempt; no source observations were written.
                    return outcome
                batch.request_metadata = {**batch.request_metadata, 'content_digest': digest}
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
    """Recover requested imports and opted-in daily checks after interruptions."""
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
    for batch_id in ImportBatch.objects.filter(source='seranking', method='api', status='pending').exclude(request_metadata__has_key='daily_refresh_id').order_by('imported_at').values_list('pk', flat=True)[:100]:
        enqueue_seranking(batch_id)

    # A queued daily run survives a temporary broker outage or worker restart.
    expired = SERankingRefresh.objects.filter(status='processing', started_at__lt=now-RUN_TIMEOUT)
    expired.update(status='failed', finished_at=now,
                   results={'connection': {'status': 'unavailable', 'message': 'Dagelijkse controle onderbroken. Volgende dag wordt opnieuw gecontroleerd.'}})
    for refresh_id in SERankingRefresh.objects.filter(status='pending', connection__active=True,
            connection__daily_enabled=True).values_list('pk', flat=True)[:100]:
        enqueue_seranking_refresh(refresh_id)


@shared_task(ignore_result=True, time_limit=1800, soft_time_limit=1740)
def process_seranking_refresh(refresh_id):
    from .seranking_refresh import run_daily_refresh
    run_daily_refresh(refresh_id)


def enqueue_seranking_refresh(refresh_id):
    try:
        process_seranking_refresh.delay(str(refresh_id))
    except Exception:
        pass  # The minute dispatcher retries the durable pending run.


@shared_task(ignore_result=True)
def schedule_daily_seranking():
    """Once per Amsterdam calendar day, at or after 08:00, per opted-in connection."""
    if not configured() or timezone.localtime().hour < 8:
        return
    today = timezone.localdate()
    for connection_id in SERankingConnection.objects.filter(active=True, daily_enabled=True).values_list('pk', flat=True):
        with transaction.atomic():
            current = SERankingConnection.objects.get(pk=connection_id)
            Project.objects.select_for_update().get(pk=current.project_id)
            connection = SERankingConnection.objects.select_for_update().select_related('owner', 'project').get(pk=connection_id)
            if not connection.active or not connection.daily_enabled or not can_manage(connection.owner, connection.project):
                continue
            refresh, created = SERankingRefresh.objects.get_or_create(connection=connection, check_date=today)
            if refresh.status == 'pending':
                transaction.on_commit(lambda refresh_id=refresh.pk: enqueue_seranking_refresh(refresh_id))
