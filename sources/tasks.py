from celery import shared_task
from django.db.models import F, Q
from .models import InventoryState
from .inventory_index import rebuild, InventoryChanged


@shared_task(ignore_result=True, time_limit=3600, soft_time_limit=3540)
def rebuild_inventory(project_id):
    state = InventoryState.objects.filter(project_id=project_id).first()
    if state is None or state.generation is not None and state.revision == state.built_revision:
        return
    try:
        rebuild(project_id)
    except InventoryChanged:
        pass  # Next sweep retries from current sources.


@shared_task(ignore_result=True)
def rebuild_pending_inventories():
    pending = InventoryState.objects.filter(Q(generation__isnull=True) | ~Q(revision=F('built_revision')))
    for project_id in pending.values_list('project_id', flat=True).iterator():
        # Expiry prevents obsolete sweep messages accumulating behind long jobs.
        rebuild_inventory.apply_async(args=[str(project_id)], expires=60)


@shared_task(bind=True, ignore_result=True, time_limit=3600, soft_time_limit=3540, max_retries=120)
def build_inventory_export(self, export_id):
    from django.conf import settings
    from django.core.files import File
    from django.http import QueryDict
    from django.utils import timezone
    from projects.models import Project
    from .models import InventoryExport
    from .excel_inventory import build_inventory_workbook
    from .export_ui import save_progress
    from .inventory_query import snapshot
    from .inventory_index import InventoryPending
    job = InventoryExport.objects.select_related('user', 'project').filter(pk=export_id).first()
    if job is None or job.state == 'ready': return
    key = f'url-export:{job.user_id}:{job.project_id}:{job.token}'
    if not Project.objects.visible_to(job.user).filter(pk=job.project_id).exists():
        InventoryExport.objects.filter(pk=job.pk).update(state='failed')
        save_progress(key, state='failed', message='Projecttoegang ingetrokken.')
        return
    claimed = InventoryExport.objects.filter(pk=job.pk, state__in=('queued', 'waiting')).update(state='building', updated_at=timezone.now())
    if not claimed: return
    try:
        @snapshot
        def build():
            params = QueryDict(job.query)
            return build_inventory_workbook(job.project, job.user,
                absolute_url=lambda path: settings.PUBLIC_BASE_URL.rstrip('/') + path,
                filter_params=params if params.get('scope') == 'filtered' else None,
                progress=lambda **data: save_progress(key, **{**data, 'state': 'building'}))
        with build() as output:
            # Reclaim a deterministic artifact left by an interrupted attempt.
            job.file.storage.delete(f'private-exports/{job.pk}.xlsx')
            job.file.save(f'{job.pk}.xlsx', File(output), save=False)
        job.state = 'ready'
        job.save(update_fields=['file', 'state'])
        save_progress(key, percent=100, state='ready', message='Excelbestand gereed.')
    except InventoryPending as error:
        InventoryExport.objects.filter(pk=job.pk).update(state='waiting', updated_at=timezone.now())
        if self.request.retries >= self.max_retries:
            InventoryExport.objects.filter(pk=job.pk).update(state='failed')
            save_progress(key, state='failed', message='Het overzicht is nog niet beschikbaar. Start de export opnieuw.')
            return
        raise self.retry(exc=error, countdown=15)
    except Exception:
        if job.file: job.file.delete(save=False)
        InventoryExport.objects.filter(pk=job.pk).update(state='failed')
        save_progress(key, state='failed', message='De export is mislukt. Start een nieuwe export.')
        raise


@shared_task(ignore_result=True)
def cleanup_inventory_exports():
    from datetime import timedelta
    from django.utils import timezone
    from .models import InventoryExport
    for job in InventoryExport.objects.filter(created_at__lt=timezone.now() - timedelta(days=1)).iterator():
        job.file.storage.delete(job.file.name or f'private-exports/{job.pk}.xlsx')
        job.delete()


def enqueue_inventory_export(export_id):
    try:
        build_inventory_export.apply_async(args=[str(export_id)], expires=60)
    except Exception:
        # The queued database job is durable; beat retries broker failures.
        pass


@shared_task(ignore_result=True)
def dispatch_inventory_exports():
    from datetime import timedelta
    from django.utils import timezone
    from .models import InventoryExport
    now = timezone.now()
    InventoryExport.objects.filter(state__in=('building', 'waiting'),
        updated_at__lt=now-timedelta(hours=1)).update(state='queued', updated_at=now)
    for job_id in InventoryExport.objects.filter(state='queued', created_at__gte=now-timedelta(days=1)).values_list('pk', flat=True).iterator():
        enqueue_inventory_export(job_id)
