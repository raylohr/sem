from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from core.ui import project_context
from sources.models import ImportBatch
from .forms import BrowserForm
from .models import BrowserRun, BrowserVisit, BrowserArtifact
from .services import create_run, check_run
from .tasks import process_browser


def enqueue(run):
    try:
        process_browser.delay(str(run.pk))
    except Exception:
        ImportBatch.objects.filter(pk=run.batch_id, status='pending').update(status='failed', error='Browseronderzoek kon niet worden ingepland.')


@never_cache
@login_required
@require_GET
def overview(request, project_id):
    context = project_context(request, project_id, 'assessments')
    runs = BrowserRun.objects.filter(batch__project=context['project']).select_related('batch').order_by('-batch__imported_at')
    sources = context['project'].import_batches.filter(source='semscan', status__in=('complete', 'incomplete'), request_metadata__scan='crawl', scan_target__active=True)
    context.update(page=Paginator(runs, 25).get_page(request.GET.get('page')), sources=sources)
    return render(request, 'browser_scans/overview.html', context)


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def start(request, project_id, batch_id):
    context = project_context(request, project_id, 'assessments')
    if not context['can_edit']:
        raise PermissionDenied
    source = get_object_or_404(ImportBatch, pk=batch_id, project=context['project'], status__in=('complete', 'incomplete'), request_metadata__scan='crawl', scan_target__active=True)
    form = BrowserForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        run = create_run(source, request.user, form.cleaned_data)
        enqueue(run)
        return redirect('ui:browser:run', project_id=project_id, run_id=run.pk)
    context.update(form=form, source=source)
    return render(request, 'browser_scans/start.html', context, status=400 if request.method == 'POST' else 200)


@never_cache
@login_required
@require_GET
def run_detail(request, project_id, run_id):
    context = project_context(request, project_id, 'assessments')
    run = get_object_or_404(BrowserRun.objects.select_related('batch', 'source_batch'), pk=run_id, batch__project=context['project'])
    context.update(run=run, batch=run.batch, page=Paginator(run.visits.order_by('id'), 25).get_page(request.GET.get('page')),
        analyses=run.batch.analysis_runs.filter(status='complete').select_related('definition'))
    return render(request, 'browser_scans/run.html', context)


@never_cache
@login_required
@require_GET
def visit_detail(request, project_id, visit_id):
    context = project_context(request, project_id, 'assessments')
    visit = get_object_or_404(BrowserVisit.objects.select_related('run__batch'), pk=visit_id, run__batch__project=context['project'], run__batch__status__in=('complete', 'incomplete'))
    context.update(visit=visit, page=Paginator(visit.resources.order_by('position'), 50).get_page(request.GET.get('page')),
        storage=Paginator(visit.storage_observations.order_by('phase','kind','name','id'),100).get_page(request.GET.get('storage_page')),
        issues=visit.accessibility_issues.all(), artifacts=visit.artifacts.defer('body'), screenshot=visit.artifacts.filter(kind='screenshot').defer('body').first())
    return render(request, 'browser_scans/visit.html', context)


@never_cache
@login_required
@require_GET
def artifact(request, project_id, artifact_id):
    context = project_context(request, project_id, 'assessments')
    item = get_object_or_404(BrowserArtifact, pk=artifact_id, visit__run__batch__project=context['project'], visit__run__batch__status__in=('complete', 'incomplete'))
    inline = item.kind == 'screenshot' and request.GET.get('inline') == '1'
    response = HttpResponse(bytes(item.body), content_type='image/png' if inline else 'application/octet-stream')
    response['Content-Disposition'] = f'{"inline" if inline else "attachment"}; filename="browser-{item.pk}-{item.kind}.{"png" if item.kind == "screenshot" else "bin"}"'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return response


@never_cache
@login_required
@require_POST
def retry(request, project_id, run_id):
    context = project_context(request, project_id, 'assessments')
    if not context['can_edit']:
        raise PermissionDenied
    run = get_object_or_404(BrowserRun, pk=run_id, batch__project=context['project'], batch__status='failed')
    check_run(run.pk)
    with transaction.atomic():
        batch = ImportBatch.objects.select_for_update().get(pk=run.batch_id)
        if batch.status != 'failed':
            return redirect('ui:browser:run', project_id=project_id, run_id=run.pk)
        batch.status, batch.error = 'pending', ''
        batch.save(update_fields=['status', 'error'])
    enqueue(run)
    return redirect('ui:browser:run', project_id=project_id, run_id=run.pk)
