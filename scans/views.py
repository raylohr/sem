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
from .models import ScanTarget
from .forms import TargetForm, ScanForm
from .network import normalize_url, ScanError
from .tasks import enqueue_scan


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
        config = {'scan': data['kind'], 'url': url, 'allowed_hosts': list(target.allowed_hosts),
            'max_urls': data.get('max_urls') or (1000 if data['kind'] == 'sitemap' else 50),
            'max_depth': data['max_depth'] if data.get('max_depth') is not None else 3,
            'duration': data.get('duration') or 120, 'respect_robots': True}
        batch = ImportBatch.objects.create(project=project, created_by=request.user, scan_target=target,
            source='crawler' if data['kind'] == 'crawl' else 'sitemap', method='scan',
            label=f"{data['kind']}: {target.name}", measured_at=timezone.now(), parser_version='scan-v1', request_metadata=config)
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
    batch = get_object_or_404(ImportBatch, pk=batch_id, project=project, status='complete')
    document = get_object_or_404(ScanDocument, batch=batch, index=index)
    response = HttpResponse(bytes(document.body), content_type='application/octet-stream')
    response['Content-Disposition'] = f'attachment; filename="scan-document-{index}.bin"'
    response['Cache-Control'] = 'private, no-store'
    return response
