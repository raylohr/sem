import json
from urllib.parse import urljoin
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from projects.models import Project
from projects.permissions import can_edit, can_manage
from sources.models import ImportBatch
from sources.views import visible_project, batch_data
from .models import ScanTarget, ScanDocument
from .forms import TargetForm, ScanForm
from .network import normalize_url, ScanError
from .tasks import enqueue_scan
from .limits import scan_budget


def target_data(target):
    return {'id': str(target.id), 'name': target.name, 'url': target.url, 'kind': target.kind,
            'allowed_hosts': target.allowed_hosts, 'active': target.active}


def json_body(request):
    data = json.loads(request.body)
    if not isinstance(data, dict):
        raise ValueError
    return data


@login_required
@require_GET
def targets(request, project_id):
    project = visible_project(request, project_id)
    return JsonResponse({'targets': [target_data(t) for t in project.scan_targets.all()]})


@login_required
@require_POST
def target_create(request, project_id):
    with transaction.atomic():
        project = get_object_or_404(Project.objects.visible_to(request.user).select_for_update(of=('self',)), pk=project_id)
        if not can_manage(request.user, project):
            return JsonResponse({'error': 'Geen beheerrechten.'}, status=403)
        try:
            form = TargetForm(json_body(request))
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Ongeldige JSON.'}, status=400)
        if not form.is_valid():
            return JsonResponse({'errors': form.errors.get_json_data()}, status=400)
        target = form.save(commit=False)
        target.project, target.created_by = project, request.user
        target.save()
        return JsonResponse(target_data(target), status=201)


@login_required
@require_POST
def target_deactivate(request, project_id, target_id):
    with transaction.atomic():
        project = get_object_or_404(Project.objects.visible_to(request.user).select_for_update(of=('self',)), pk=project_id)
        if not can_manage(request.user, project):
            return JsonResponse({'error': 'Geen beheerrechten.'}, status=403)
        target = get_object_or_404(ScanTarget, pk=target_id, project=project)
        target.active = False
        target.save(update_fields=['active'])
    return JsonResponse({'deactivated': True})


@login_required
@require_POST
def start(request, project_id, target_id):
    with transaction.atomic():
        project = get_object_or_404(Project.objects.visible_to(request.user).select_for_update(of=('self',)), pk=project_id)
        if not can_edit(request.user, project):
            return JsonResponse({'error': 'Geen bewerkrechten.'}, status=403)
        target = get_object_or_404(ScanTarget, pk=target_id, project=project, active=True)
        try:
            form = ScanForm(json_body(request))
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Ongeldige JSON.'}, status=400)
        if not form.is_valid():
            return JsonResponse({'errors': form.errors.get_json_data()}, status=400)
        data = form.cleaned_data
        try:
            url = normalize_url(data.get('url') or (urljoin(target.url, '/sitemap.xml') if data['kind'] == 'sitemap' else target.url), target.allowed_hosts)
        except ScanError as exc:
            return JsonResponse({'error': str(exc)}, status=400)
        max_urls = data.get('max_urls') or 100000
        duration, transfer = scan_budget(data['kind'], max_urls)
        config = {'scan': data['kind'], 'url': url, 'allowed_hosts': list(target.allowed_hosts),
            'max_urls': max_urls,
            'max_depth': data['max_depth'] if data.get('max_depth') is not None else 99,
            'duration': data.get('duration') or duration, 'max_transfer': transfer, 'respect_robots': data.get('robots_mode') == 'respect',
            'robots_mode': data.get('robots_mode') or 'ignore',
            'concurrency': data.get('concurrency') or 8,
            'requests_per_second': data.get('requests_per_second') or 12}
        batch = ImportBatch.objects.create(project=project, created_by=request.user, scan_target=target,
            source='semscan', method='scan',
            label=f"{data['kind']}: {target.name}", measured_at=timezone.now(), parser_version='scan-v3', request_metadata=config)
        transaction.on_commit(lambda: enqueue_scan(batch.id))
    return JsonResponse(batch_data(batch), status=202)


@login_required
@require_POST
def cancel(request, project_id, batch_id):
    with transaction.atomic():
        project = get_object_or_404(Project.objects.visible_to(request.user).select_for_update(of=('self',)), pk=project_id)
        if not can_edit(request.user, project):
            return JsonResponse({'error': 'Geen bewerkrechten.'}, status=403)
        batch = get_object_or_404(ImportBatch.objects.select_for_update(), pk=batch_id, project=project, scan_target__isnull=False)
        if batch.status not in ('pending', 'processing'):
            return JsonResponse({'error': 'Scan is niet actief.'}, status=409)
        batch.status = 'cancelled'
        batch.request_metadata = {**batch.request_metadata, 'cancelled_by': request.user.pk, 'cancelled_at': timezone.now().isoformat()}
        batch.save(update_fields=['status', 'request_metadata'])
    return JsonResponse({'cancelled': True})


@login_required
@require_GET
def document_download(request, project_id, batch_id, index):
    from django.http import HttpResponse
    from .models import ScanDocument
    project = visible_project(request, project_id)
    batch = get_object_or_404(ImportBatch, pk=batch_id, project=project, status__in=('complete','incomplete'))
    document = get_object_or_404(ScanDocument, batch=batch, index=index)
    response = HttpResponse(bytes(document.body), content_type='application/octet-stream')
    response['Content-Disposition'] = f'attachment; filename="scan-document-{index}.bin"'
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_GET
def progress(request, project_id, batch_id):
    """Project-scoped read-only telemetry, including while the worker owns its lock."""
    import time
    from django.db.models import Count, Max
    from django.utils.cache import add_never_cache_headers
    from .telemetry import read_checkpoint, INTERVAL, WINDOW
    project = visible_project(request, project_id)
    batch = get_object_or_404(ImportBatch,pk=batch_id,project=project,scan_target__isnull=False)
    active = batch.status in ('pending','processing')
    stored = batch.request_metadata.get('scan_progress',{})
    live = read_checkpoint(batch.pk) if active else None
    if live is None:
        live = stored.get('live')
    if live is None:
        # Historical scans predate rollups. Read only status/time columns, never bodies.
        docs = ScanDocument.objects.filter(batch=batch)
        last = docs.aggregate(last=Max('fetched_at'))['last'] if not active else None
        end = int(last.timestamp() if last else time.time())//INTERVAL*INTERVAL
        totals = {str(row['status']):row['n'] for row in docs.values('status').annotate(n=Count('id'))} if not active else {}
        buckets = {}
        if not active:
            from datetime import datetime, timezone as dt_timezone
            for status,stamp in docs.filter(fetched_at__gte=datetime.fromtimestamp(end-WINDOW,dt_timezone.utc)).values_list('status','fetched_at').iterator(chunk_size=500):
                key=(int(stamp.timestamp())//INTERVAL*INTERVAL,str(status))
                buckets[key]=buckets.get(key,0)+1
        live={'records':stored.get('records',batch.record_count),'documents':stored.get('documents',sum(totals.values())),
              'pending':0,'errors':batch.request_metadata.get('scan_outcome',{}).get('request_failures',0),
              'updated_at':last.timestamp() if last else None,'interval_seconds':INTERVAL,'window_seconds':WINDOW,
              'end':end,'totals':totals,'series':[{'time':t,'status':status,'count':n} for (t,status),n in sorted(buckets.items())]}
    if active:
        live={**live,'end':int(time.time())//INTERVAL*INTERVAL}
    response=JsonResponse({**live,'status':batch.status,'status_label':batch.get_status_display(),
        'active':active,'max_urls':batch.request_metadata.get('max_urls'),'available':bool(live.get('updated_at')),
        'poll_seconds':3,'scan_kind':batch.scan_kind})
    add_never_cache_headers(response)
    return response
