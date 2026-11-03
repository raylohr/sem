import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from core.ui import project_context
from projects.models import Project
from projects.permissions import can_edit
from sources.models import ImportBatch
from .models import AnalysisRun, Finding, ReprocessingJob, PageMeasurement, LinkObservation
from .tasks import process_reprocessing


@never_cache
@login_required
@require_GET
def overview(request, project_id):
    context = project_context(request, project_id, 'assessments')
    project = context['project']
    runs = AnalysisRun.objects.filter(batch__project=project, status='complete', batch__status__in=('complete', 'incomplete')).select_related('batch', 'definition').order_by('-started_at')
    batches = ImportBatch.objects.filter(project=project, source__in=('semscan', 'crawler'), status__in=('complete', 'incomplete')).filter(request_metadata__scan='crawl')
    # Legacy source names may have no scan metadata.
    legacy = ImportBatch.objects.filter(project=project, source='crawler', status__in=('complete', 'incomplete'))
    context.update(page=Paginator(runs, 25).get_page(request.GET.get('page')), batches=(batches | legacy).order_by('-imported_at'),
                   jobs=ReprocessingJob.objects.filter(batch__project=project).select_related('batch').order_by('-created_at')[:10])
    return render(request, 'assessments/overview.html', context)


@never_cache
@login_required
@require_POST
def reprocess(request, project_id, batch_id=None):
    context = project_context(request, project_id, 'assessments')
    if not context['can_edit']:
        raise PermissionDenied
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=context['project'].pk)
        if not can_edit(request.user, project):
            raise PermissionDenied
        if batch_id is None:
            import uuid
            try:
                batch_id = uuid.UUID(request.POST.get('batch', ''))
            except (ValueError, TypeError, AttributeError):
                from django.http import Http404
                raise Http404
        batch = get_object_or_404(ImportBatch, pk=batch_id, project=project, status__in=('complete', 'incomplete'))
        if batch.scan_kind != 'crawl' or batch.source not in ('semscan', 'crawler'):
            raise PermissionDenied
        job, created = ReprocessingJob.objects.get_or_create(batch=batch, status__in=('pending', 'processing'), defaults={'actor': request.user, 'status': 'pending'})
    if created:
        try:
            process_reprocessing.delay(str(job.pk))
        except Exception:
            ReprocessingJob.objects.filter(pk=job.pk, status='pending').update(status='failed', error='Heranalyse kon niet worden ingepland.')
            messages.error(request, 'Heranalyse kon niet worden ingepland. Probeer opnieuw.')
        else:
            messages.success(request, 'Heranalyse ingepland. Vernieuw deze pagina om het resultaat te bekijken.')
    else:
        messages.info(request, 'Voor deze crawl is al een heranalyse ingepland.')
    return redirect('ui:assessments:overview', project_id=project_id)


def run_context(request, project_id, run_id):
    context = project_context(request, project_id, 'assessments')
    run = get_object_or_404(AnalysisRun.objects.select_related('batch', 'definition'), pk=run_id, batch__project=context['project'], batch__status__in=('complete', 'incomplete'), status='complete')
    return context, run


@never_cache
@login_required
@require_GET
def run_detail(request, project_id, run_id):
    context, run = run_context(request, project_id, run_id)
    findings = Finding.objects.filter(result__run=run).select_related('result__source_record')
    rule = request.GET.get('rule', '')[:80]
    severity = request.GET.get('severity', '')[:16]
    query = request.GET.get('q', '').strip()[:250]
    if rule:
        findings = findings.filter(rule=rule)
    if severity:
        findings = findings.filter(severity=severity)
    if query:
        findings = findings.filter(result__source_record__data__url__icontains=query)
    filters = request.GET.copy()
    filters.pop('page', None)
    context.update(run=run, page=Paginator(findings.order_by('rule', 'id'), 25).get_page(request.GET.get('page')),
        filter_query=filters.urlencode(), rule=rule, severity=severity, query=query,
        outcomes=run.results.values('outcome').annotate(total=Count('id')).order_by('outcome'))
    return render(request, 'assessments/run.html', context)


@never_cache
@login_required
@require_GET
def finding_detail(request, project_id, finding_id):
    context = project_context(request, project_id, 'assessments')
    finding = get_object_or_404(Finding.objects.select_related('result__run__batch', 'result__run__definition'), pk=finding_id,
        result__run__batch__project=context['project'], result__run__batch__status__in=('complete', 'incomplete'), result__run__status='complete')
    context.update(finding=finding, evidence=finding.evidence.select_related('source_record__batch', 'document').defer('source_record__data', 'document__body'))
    return render(request, 'assessments/finding.html', context)


@never_cache
@login_required
@require_GET
def export(request, project_id, run_id):
    _, run = run_context(request, project_id, run_id)
    # Paginated UI; complete export streams records without building the entire run in RAM.
    from django.http import StreamingHttpResponse
    def rows():
        yield json.dumps({'type': 'analysis', 'run_id': str(run.pk), 'batch_id': str(run.batch_id),
            'definition': run.definition.key, 'rule_version': run.definition.version,
            'extractor_version': run.extractor_version, 'coverage': run.coverage, 'summary': run.summary}, ensure_ascii=False) + '\n'
        for result in run.results.order_by('id').iterator(chunk_size=100):
            yield json.dumps({'type': 'result', 'source_record_id': result.source_record_id, 'outcome': result.outcome, 'answer': result.answer}, ensure_ascii=False) + '\n'
        for finding in Finding.objects.filter(result__run=run).select_related('result').prefetch_related('evidence').order_by('id').iterator(chunk_size=100):
            yield json.dumps({'type': 'finding', 'id': finding.pk, 'source_record_id': finding.result.source_record_id,
                'rule': finding.rule, 'severity': finding.severity, 'title': finding.title,
                'recommendation': finding.recommendation, 'details': finding.details,
                'evidence': [{'source_record_id': item.source_record_id, 'document_id': item.document_id, 'browser_artifact_id': item.browser_artifact_id, 'role': item.role, 'snapshot': item.snapshot} for item in finding.evidence.all()]}, ensure_ascii=False) + '\n'
    response = StreamingHttpResponse(rows(), content_type='application/x-ndjson; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="assessment-{run.pk}.ndjson"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@never_cache
@login_required
@require_GET
def page_detail(request, project_id, page_id):
    from django.db.models import OuterRef, Subquery
    context = project_context(request, project_id, 'assessments')
    page = get_object_or_404(PageMeasurement.objects.select_related('batch', 'source_record', 'url'), pk=page_id,
        batch__project=context['project'], url__project=context['project'], batch__status__in=('complete', 'incomplete'))
    incoming = request.GET.get('direction') == 'in'
    if incoming:
        links = LinkObservation.objects.filter(source__batch=page.batch, destination_hash=page.url.url_hash).select_related('source__url')
    else:
        targets = PageMeasurement.objects.filter(batch=page.batch, url__project=context['project'], url__url_hash=OuterRef('destination_hash')).order_by('source_record_id')
        if page.batch.request_metadata.get('privacy_enabled'):
            targets = targets.filter(facts__privacy_scenario=OuterRef('source__facts__privacy_scenario'))
        links = page.links.annotate(target_status=Subquery(targets.values('final_http_status')[:1]))
    context.update(measurement=page, incoming=incoming, page=Paginator(links.order_by('id'), 50).get_page(request.GET.get('page')))
    return render(request, 'assessments/page.html', context)
