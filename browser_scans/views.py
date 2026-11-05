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
from .models import BrowserRun, BrowserVisit, BrowserArtifact,DiscoveredLink
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
    form = BrowserForm(request.POST if request.method == 'POST' else None, initial={key:source.request_metadata.get(key,1) for key in ('requests_per_second','concurrency')})
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
        discovered=Paginator(DiscoveredLink.objects.filter(visit__run=run).select_related('visit','followup').order_by('id'),50).get_page(request.GET.get('link_page')),analyses=run.batch.analysis_runs.filter(status='complete').select_related('definition'))
    return render(request, 'browser_scans/run.html', context)


@never_cache
@login_required
@require_GET
def visit_detail(request, project_id, visit_id):
    context = project_context(request, project_id, 'assessments')
    visit = get_object_or_404(BrowserVisit.objects.select_related('run__batch'), pk=visit_id, run__batch__project=context['project'], run__batch__status__in=('complete', 'incomplete'))
    from assessments.models import PageMeasurement
    original=PageMeasurement.objects.filter(batch_id=visit.run.source_batch_id,batch__project=context['project'],final_url__in=[visit.url,visit.final_url]).order_by('-measured_at','-id').first()
    context.update(original=original,visit=visit, page=Paginator(visit.resources.order_by('position'), 50).get_page(request.GET.get('page')),
        links=Paginator(visit.discovered_links.order_by('id'),50).get_page(request.GET.get('link_page')),storage=Paginator(visit.storage_observations.order_by('phase','kind','name','id'),100).get_page(request.GET.get('storage_page')),
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


@never_cache
@login_required
@require_GET
def export(request,project_id,run_id):
    import json
    from django.http import StreamingHttpResponse
    context=project_context(request,project_id,'assessments')
    run=get_object_or_404(BrowserRun,pk=run_id,batch__project=context['project'],batch__status__in=('complete','incomplete'))
    def rows():
        yield json.dumps({'type':'browser_run','id':str(run.pk),'source_batch_id':str(run.source_batch_id) if run.source_batch_id else None,'config':run.config,'coverage':run.coverage,'engine':run.engine},ensure_ascii=False)+'\n'
        for visit in run.visits.order_by('id').iterator(chunk_size=10):
            yield json.dumps({'type':'browser_visit','id':visit.pk,'url':visit.url,'final_url':visit.final_url,'scenario':visit.scenario,'status':visit.status,
                'measured_at':visit.measured_at.isoformat() if visit.measured_at else None,'server_facts':visit.server_facts,'rendered_facts':visit.rendered_facts,
                'differences':visit.differences,'coverage':visit.coverage,'metrics':visit.metrics},ensure_ascii=False)+'\n'
            for artifact in visit.artifacts.defer('body').order_by('id'):
                yield json.dumps({'type':'artifact','visit_id':visit.pk,'id':artifact.pk,'kind':artifact.kind,'bytes':artifact.size,'sha256':artifact.checksum})+'\n'
        for link in DiscoveredLink.objects.filter(visit__run=run).order_by('id').iterator(chunk_size=200):
            yield json.dumps({'type':'discovered_link','visit_id':link.visit_id,'url':link.url,'phase':link.phase,'state':link.state,'details':link.details,'followup_batch_id':str(link.followup_id) if link.followup_id else None},ensure_ascii=False)+'\n'
    response=StreamingHttpResponse(rows(),content_type='application/x-ndjson')
    response['Content-Disposition']='attachment; filename="browser-evidence.jsonl"'
    return response
