import json
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404,render,redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.db import transaction
from django.db.models import Count
from core.ui import project_context
from sources.models import ImportBatch
from .models import EnrichmentRun, TechnologyDetection, Supplier, CustomerDeclaration, PolicyDocument
from .forms import SupplierForm, DeclarationForm
from .services import schedule_enrichment, update_supplier


@never_cache
@login_required
@require_GET
def overview(request,project_id):
    context=project_context(request,project_id,'intelligence')
    runs=EnrichmentRun.objects.filter(batch__project=context['project']).select_related('batch').order_by('-id')
    context.update(page=Paginator(runs,25).get_page(request.GET.get('page')),batches=context['project'].import_batches.filter(request_metadata__scan='browser',status__in=('complete','incomplete')),
        declarations=CustomerDeclaration.objects.filter(project=context['project']).select_related('technology','actor').order_by('-created_at')[:50])
    return render(request,'site_intelligence/overview.html',context)


@never_cache
@login_required
@require_POST
def start(request,project_id,batch_id):
    context=project_context(request,project_id,'intelligence')
    if not context['can_edit']:raise PermissionDenied
    batch=get_object_or_404(ImportBatch,pk=batch_id,project=context['project'],request_metadata__scan='browser',status__in=('complete','incomplete'))
    run=schedule_enrichment(batch.pk,request.user.pk,fresh=True)
    return redirect('ui:intelligence:run',project_id=project_id,run_id=run.pk)


@never_cache
@login_required
@require_POST
def retry(request,project_id,run_id):
    context=project_context(request,project_id,'intelligence')
    if not context['can_edit']:raise PermissionDenied
    run=get_object_or_404(EnrichmentRun,pk=run_id,batch__project=context['project'],status='failed')
    run.status='pending';run.error='';run.started_at=None;run.save(update_fields=['status','error','started_at'])
    from .tasks import enrich
    try:enrich.delay(run.pk)
    except Exception:
        run.status='failed';run.error='Verrijking kon niet worden ingepland.';run.save(update_fields=['status','error'])
    return redirect('ui:intelligence:run',project_id=project_id,run_id=run.pk)


@never_cache
@login_required
@require_GET
def run_detail(request,project_id,run_id):
    context=project_context(request,project_id,'intelligence')
    run=get_object_or_404(EnrichmentRun.objects.select_related('batch'),pk=run_id,batch__project=context['project'])
    detections=run.detections.select_related('technology','rule','visit').order_by('technology__name','id')
    category=request.GET.get('category','')[:40]
    if category:detections=detections.filter(technology__category=category)
    context.update(run=run,page=Paginator(detections,50).get_page(request.GET.get('page')),category=category,
        categories=run.detections.values('technology__category').annotate(total=Count('id')).order_by('technology__category'),
        domains=run.domains.order_by('hostname'),policies=run.policies.defer('body','text'),
        technologies=run.detections.values('technology__name','technology__category').annotate(signals=Count('id')).order_by('technology__name'))
    return render(request,'site_intelligence/run.html',context)


@never_cache
@login_required
@require_GET
def detection_detail(request,project_id,detection_id):
    context=project_context(request,project_id,'intelligence')
    detection=get_object_or_404(TechnologyDetection.objects.select_related('technology','rule','visit','run__batch'),pk=detection_id,run__batch__project=context['project'])
    context.update(detection=detection,evidence=detection.evidence.select_related('source_record','artifact','resource','domain').defer('source_record__data','artifact__body'))
    return render(request,'site_intelligence/detection.html',context)


@never_cache
@login_required
@require_GET
def registry(request,project_id):
    context=project_context(request,project_id,'intelligence')
    context.update(suppliers=Supplier.objects.order_by('name'))
    return render(request,'site_intelligence/registry.html',context)


@never_cache
@login_required
@require_http_methods(['GET','POST'])
def supplier_edit(request,project_id,supplier_id):
    context=project_context(request,project_id,'intelligence')
    if not request.user.is_superuser:raise PermissionDenied
    supplier=get_object_or_404(Supplier,pk=supplier_id)
    form=SupplierForm(request.POST if request.method=='POST' else None,instance=supplier)
    if request.method=='POST' and form.is_valid():
        update_supplier(supplier.pk,request.user,form.cleaned_data)
        return redirect('ui:intelligence:registry',project_id=project_id)
    context.update(form=form,supplier=supplier)
    return render(request,'site_intelligence/edit.html',context,status=400 if request.method=='POST' else 200)


@never_cache
@login_required
@require_http_methods(['GET','POST'])
def declaration(request,project_id):
    context=project_context(request,project_id,'intelligence')
    if not context['can_edit']:raise PermissionDenied
    form=DeclarationForm(request.POST if request.method=='POST' else None)
    if request.method=='POST' and form.is_valid():
        item=form.save(commit=False);item.project=context['project'];item.actor=request.user;item.save()
        return redirect('ui:intelligence:overview',project_id=project_id)
    context.update(form=form)
    return render(request,'site_intelligence/declaration.html',context,status=400 if request.method=='POST' else 200)


@never_cache
@login_required
@require_GET
def policy_download(request,project_id,policy_id):
    context=project_context(request,project_id,'intelligence')
    policy=get_object_or_404(PolicyDocument,pk=policy_id,run__batch__project=context['project'])
    response=HttpResponse(bytes(policy.body),content_type='application/octet-stream')
    response['Content-Disposition']=f'attachment; filename="policy-{policy.pk}.bin"'
    response['X-Content-Type-Options']='nosniff'
    response['Content-Security-Policy']="default-src 'none'; sandbox"
    return response


@never_cache
@login_required
@require_GET
def export(request,project_id,run_id):
    context=project_context(request,project_id,'intelligence')
    run=get_object_or_404(EnrichmentRun,pk=run_id,batch__project=context['project'])
    def rows():
        yield json.dumps({'type':'enrichment','id':run.pk,'version':run.version,'coverage':run.coverage,'european_profile':run.european_profile})+'\n'
        for detection in run.detections.select_related('technology','rule').prefetch_related('evidence').iterator(chunk_size=100):
            yield json.dumps({'type':'technology','id':detection.pk,'product':detection.technology.name,'category':detection.technology.category,'state':detection.state,'version':detection.version,'phase':detection.phase,'rule':detection.rule.key,'rule_version':detection.rule.version,'supplier_snapshot':detection.supplier_snapshot,'evidence':[item.snapshot for item in detection.evidence.all()]},ensure_ascii=False)+'\n'
        for domain in run.domains.iterator():
            yield json.dumps({'type':'domain','hostname':domain.hostname,'measured_at':domain.measured_at.isoformat(),'records':domain.records,'network':domain.network,'coverage':domain.coverage})+'\n'
        for policy in run.policies.defer('body').iterator():
            yield json.dumps({'type':'policy','url':policy.url,'measured_at':policy.measured_at.isoformat(),'status':policy.status,'checksum':policy.checksum,'truncated':policy.truncated})+'\n'
        for item in CustomerDeclaration.objects.filter(project=context['project']).select_related('technology').iterator():
            yield json.dumps({'type':'customer_declaration','technology':item.technology.name,'kind':item.kind,'statement':item.statement,'created_at':item.created_at.isoformat(),'actor':item.actor_id},ensure_ascii=False)+'\n'
        from browser_scans.models import StorageObservation
        for item in StorageObservation.objects.filter(visit__run__batch=run.batch).select_related('visit').iterator(chunk_size=100):
            yield json.dumps({'type':'storage','visit':item.visit_id,'scenario':item.visit.scenario,'phase':item.phase,'kind':item.kind,'name':item.name,'origin':item.origin,'metadata':item.metadata})+'\n'
    response=StreamingHttpResponse(rows(),content_type='application/x-ndjson; charset=utf-8')
    response['Content-Disposition']=f'attachment; filename="intelligence-{run.pk}.ndjson"'
    return response
