import hashlib
from celery import shared_task
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.utils import timezone
from sources.models import ImportBatch
from .client import render
from scans.network import ScanError
from .models import BrowserRun, BrowserVisit
from .services import check_run, store_visit, finalize


@shared_task(ignore_result=True, acks_late=True, reject_on_worker_lost=True, soft_time_limit=120, time_limit=135)
def process_browser(run_id):
    lock = int(hashlib.sha256(str(run_id).encode()).hexdigest()[:15], 16)
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s)', [lock])
        acquired = cursor.fetchone()[0]
    if not acquired:
        return
    try:
        run = check_run(run_id)
        if run.batch.status in ('complete', 'incomplete', 'failed'):
            return
        ImportBatch.objects.filter(pk=run.batch_id).exclude(status='cancelled').update(status='processing')
        if not run.started_at:
            run.started_at = timezone.now()
            run.save(update_fields=['started_at'])
        if run.started_at and (timezone.now()-run.started_at).total_seconds() > run.config.get('duration', 7200):
            run.visits.filter(status__in=('pending', 'processing')).update(status='failed', measured_at=timezone.now(), errors=[{'code': 'run_budget', 'message': 'Totale tijdslimiet bereikt.'}])
            finalize(run_id)
            return
        visit = run.visits.filter(status__in=('pending', 'processing')).order_by('id').first()
        if visit:
            visit.status = 'processing'
            visit.save(update_fields=['status'])
            try:
                response = render(visit.url, {**run.config, 'scenario': visit.scenario}, check_access=lambda: check_run(run_id), batch_id=run.batch_id)
                store_visit(visit.pk, response)
            except PermissionDenied:
                raise
            except Exception as exc:
                BrowserVisit.objects.filter(pk=visit.pk).update(status='failed', measured_at=timezone.now(), errors=[{'code': type(exc).__name__, 'message': str(exc)[:255] if isinstance(exc, ScanError) else 'Browsermeting kon niet worden afgerond.'}])
        check_run(run_id)
        if run.visits.filter(status__in=('pending', 'processing')).exists():
            process_browser.apply_async(args=[str(run_id)], countdown=1)
        else:
            finalize(run_id)
    except (BrowserRun.DoesNotExist, ImportBatch.DoesNotExist):
        return
    except PermissionDenied:
        BrowserRun.objects.filter(pk=run_id, batch__status__in=('pending', 'processing')).update(completed_at=timezone.now())
        ImportBatch.objects.filter(browser_run__pk=run_id, status__in=('pending', 'processing')).update(status='failed', error='Toegang of scandoel ingetrokken.')
    except Exception:
        ImportBatch.objects.filter(browser_run__pk=run_id, status__in=('pending', 'processing')).update(status='failed', error='Browseronderzoek onderbroken; start opnieuw om de opgeslagen wachtrij te hervatten.')
    finally:
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_unlock(%s)', [lock])
