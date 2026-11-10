"""Daily availability checks; import changed reports through the existing worker."""
from uuid import uuid4

from django.core.exceptions import PermissionDenied
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from projects.models import Project
from projects.permissions import can_manage
from sources.models import ImportBatch
from .models import SERankingConnection, SERankingRefresh
from .seranking import ProjectClient, REPORTS, SERankingError, site_audits, verify_connection
from .seranking_history import history_bounds, missing_periods


def check_refresh(refresh_id, token):
    refresh = SERankingRefresh.objects.select_related('connection__owner', 'connection__project').get(pk=refresh_id)
    connection = refresh.connection
    if (refresh.status != 'processing' or refresh.run_token != token or not connection.active
            or not connection.daily_enabled or not can_manage(connection.owner, connection.project)):
        raise PermissionDenied
    return refresh


def set_daily_schedule(*, actor, project_id, connection_id, enabled):
    from .seranking_tasks import enqueue_seranking_refresh
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=project_id)
        if not can_manage(actor, project):
            raise PermissionDenied
        connection = SERankingConnection.objects.select_for_update().get(pk=connection_id, project=project, active=True)
        if enabled and not can_manage(connection.owner, project):
            raise PermissionDenied
        was_enabled = connection.daily_enabled
        connection.daily_enabled = enabled
        connection.save(update_fields=['daily_enabled'])
        if enabled:
            refresh, created = SERankingRefresh.objects.get_or_create(connection=connection, check_date=timezone.localdate())
            if refresh.status in ('cancelled', 'failed') or not was_enabled and refresh.status in ('complete', 'partial'):
                refresh.status = 'pending'
                refresh.run_token = None
                refresh.started_at = refresh.finished_at = None
                refresh.results = {}
                refresh.save(update_fields=['status', 'run_token', 'started_at', 'finished_at', 'results'])
            if refresh.status == 'pending':
                transaction.on_commit(lambda: enqueue_seranking_refresh(refresh.pk))
        else:
            cancel_daily_refreshes(connection)
    return connection


def cancel_daily_refreshes(connection):
    SERankingRefresh.objects.filter(connection=connection, status__in=('pending', 'processing')).update(
        status='cancelled', finished_at=timezone.now())
    ImportBatch.objects.filter(seranking_connection=connection, source='seranking',
        status__in=('pending', 'processing'), request_metadata__has_key='daily_refresh_id').update(status='cancelled')


def run_daily_refresh(refresh_id):
    from .seranking_tasks import process_seranking
    token = None
    try:
        refresh = SERankingRefresh.objects.select_related('connection').get(pk=refresh_id)
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=refresh.connection.project_id)
            connection = SERankingConnection.objects.select_for_update().select_related('owner', 'project').get(pk=refresh.connection_id)
            refresh = SERankingRefresh.objects.select_for_update().get(pk=refresh_id)
            if refresh.status != 'pending':
                return
            if not connection.active or not connection.daily_enabled or not can_manage(connection.owner, connection.project):
                refresh.status = 'cancelled'
                refresh.finished_at = timezone.now()
                refresh.save(update_fields=['status', 'finished_at'])
                return
            token = uuid4()
            refresh.status = 'processing'
            refresh.run_token = token
            refresh.started_at = timezone.now()
            refresh.save(update_fields=['status', 'run_token', 'started_at'])
        client = ProjectClient()
        guard = lambda: check_refresh(refresh_id, token)
        guard()
        verify_connection(client, connection)
        results = {}

        def import_report(report, *, start=None, end=None, extra=None):
            guard()
            label, scope = REPORTS[report]
            with transaction.atomic():
                Project.objects.select_for_update().get(pk=connection.project_id)
                guard()
                batch = ImportBatch.objects.create(project=connection.project, created_by=connection.owner,
                    source='seranking', method='api', seranking_connection=connection,
                    label='SE Ranking · '+label, period_start=start, period_end=end,
                    parser_version='seranking-project-v2', request_metadata={
                        'type': report, 'record_scope': scope, 'site_id': connection.site_id,
                        'site_name': connection.site_name, 'report_label': label, 'api_scope': 'project-only',
                        'daily_refresh_id': str(refresh_id), 'daily_refresh_token': str(token), **(extra or {})})
            outcome = process_seranking(str(batch.pk))
            if isinstance(outcome, dict) and outcome.get('status') == 'unchanged':
                return outcome
            batch.refresh_from_db()
            if batch.status != 'complete':
                outcome = {'status': 'unavailable', 'message': batch.error or 'Rapport niet voltooid.'}
                if batch.status in ('failed', 'cancelled') and not batch.records.exists():
                    batch.delete()  # The durable refresh result retains the error, without daily failed-import clutter.
                return outcome
            return {'status': 'updated', 'records': batch.record_count, 'batch_id': str(batch.pk)}

        for report in REPORTS:
            if report not in ('rankings', 'audit_pages'):
                results[report] = import_report(report)
        try:
            guard()
            checkpoints = client.get('checkpoints', site_id=connection.site_id)
            start, end = history_bounds(checkpoints)
            covered = list(ImportBatch.objects.filter(seranking_connection=connection, source='seranking',
                request_metadata__type='rankings', status='complete', period_start__isnull=False,
                period_end__isnull=False).values_list('period_start', 'period_end'))
            imports = [import_report('rankings', start=left, end=right, extra={'history_checkpoints': checkpoints})
                       for left, right in missing_periods(start, end, covered)]
            results['rankings'] = {'status': 'unchanged' if not imports else
                'unavailable' if any(item['status'] == 'unavailable' for item in imports) else 'updated',
                'latest_date': end.isoformat(), 'imports': imports}
        except (SERankingError, CommandError) as error:
            results['rankings'] = {'status': 'unavailable', 'message': str(error)}
        try:
            guard()
            imports = []
            for audit in site_audits(client, connection, guard):
                stamp, audit_id = audit.get('last_update'), int(audit['id'])
                previous = ImportBatch.objects.filter(seranking_connection=connection, source='seranking',
                    request_metadata__type='audit_pages', request_metadata__audit_id=audit_id, status='complete')
                if stamp and (previous.filter(request_metadata__audit_date=stamp).exists()
                              or previous.filter(records__data__date=stamp).exists()):
                    continue
                imports.append(import_report('audit_pages', extra={'audit_id': audit_id, 'audit_date': stamp}))
            results['audit_pages'] = {'status': 'unchanged' if not imports else
                'unavailable' if any(item['status'] == 'unavailable' for item in imports) else 'updated', 'imports': imports}
        except (SERankingError, ValueError, KeyError):
            results['audit_pages'] = {'status': 'unavailable', 'message': 'Auditgegevens konden niet worden opgehaald.'}
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=connection.project_id)
            guard()
            SERankingRefresh.objects.filter(pk=refresh_id, status='processing', run_token=token).update(
                status='partial' if any(item['status'] == 'unavailable' for item in results.values()) else 'complete',
                finished_at=timezone.now(), results=results)
    except SERankingRefresh.DoesNotExist:
        return
    except PermissionDenied:
        SERankingRefresh.objects.filter(pk=refresh_id, status__in=('pending', 'processing'), run_token=token).update(
            status='cancelled', finished_at=timezone.now())
    except Exception as error:
        message = str(error) if isinstance(error, SERankingError) else 'Dagelijkse SE Ranking-controle mislukt.'
        SERankingRefresh.objects.filter(pk=refresh_id, status='processing', run_token=token).update(
            status='failed', finished_at=timezone.now(), results={'connection': {'status': 'unavailable', 'message': message}})
