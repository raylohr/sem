import csv
import hashlib
import io
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse,StreamingHttpResponse
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404,render
from django.utils import timezone
from django.views.decorators.http import require_GET,require_POST
from projects.models import Project
from projects.permissions import can_edit
from sources.models import ImportBatch
from sources.views import batch_data
from core.ui import project_context
from .forms import ScanForm
from .models import ScanRetryTarget
from .network import normalize_url,ScanError
from .reliability import TRANSIENT
from .scope import host_scope
from .views import json_body
from .tasks import enqueue_scan


@login_required
@require_POST
def retry_downloads(request,project_id,batch_id):
    with transaction.atomic():
        project=get_object_or_404(Project.objects.visible_to(request.user).select_for_update(of=('self',)),pk=project_id)
        if not can_edit(request.user,project):return JsonResponse({'error':'Geen bewerkrechten.'},status=403)
        parent=get_object_or_404(ImportBatch.objects.select_related('scan_target'),project=project,pk=batch_id,status__in=('complete','incomplete'))
        target=parent.scan_target
        if parent.scan_kind!='crawl' or not target or not target.active or target.project_id!=project.pk:
            return JsonResponse({'error':'Geen actief crawldoel.'},status=400)
        existing=ImportBatch.objects.filter(project=project,request_metadata__retry_of_batch=str(parent.pk),status__in=('pending','processing')).first()
        if existing:return JsonResponse(batch_data(existing),status=202)
        try:form=ScanForm({'kind':'crawl',**json_body(request)})
        except (ValueError,TypeError):return JsonResponse({'error':'Ongeldige JSON.'},status=400)
        if not form.is_valid():return JsonResponse({'errors':form.errors.get_json_data()},status=400)
        settings=form.cleaned_data;scope=host_scope(target)
        query=parent.records.filter(data__error_code__in=sorted(TRANSIENT|{'interrupted','retry_exhausted','budget_exhausted'})).order_by('id')
        batch=None;buffer=[];count=skipped=0
        for source in query.iterator(chunk_size=100):
            if count>=100000:break
            try:url=normalize_url(source.data.get('url'),scope['allowed_hosts'])
            except ScanError:skipped+=1;continue
            if batch is None:
                batch=ImportBatch.objects.create(project=project,created_by=request.user,scan_target=target,source='semscan',method='scan',
                    label='Herdownload: '+parent.label[:170],measured_at=timezone.now(),parser_version='scan-v3',request_metadata={
                    **scope,'scan':'crawl','url':url,'retry_of_batch':str(parent.pk),'reliability_version':'page-retry-v1','retry_only':True,
                    'discover_sitemaps':False,'max_urls':100000,'max_depth':0,'duration':604800,'max_transfer':25*1024*1024,
                    'concurrency':1,'requests_per_second':1,'respect_robots':parent.request_metadata.get('respect_robots',True),
                    **{key:settings.get(key) or default for key,default in [('connect_timeout',10),('read_timeout',30),('max_attempts',3)]}})
            buffer.append(ScanRetryTarget(batch=batch,source_record=source,url=url,url_hash=hashlib.sha256(url.encode()).hexdigest(),
                snapshot={'depth':source.data.get('depth'),'measured_at':source.data.get('measured_at'),'original_error':source.data.get('error_code')}))
            count+=1
            if len(buffer)>=100:ScanRetryTarget.objects.bulk_create(buffer,ignore_conflicts=True);buffer=[]
        if batch is None:return JsonResponse({'error':'Geen tijdelijke downloadfouten binnen de huidige hosttoestemming om opnieuw op te halen.'},status=400)
        ScanRetryTarget.objects.bulk_create(buffer,ignore_conflicts=True)
        count=batch.retry_targets.count()
        batch.request_metadata.update(max_urls=count,duration=min(604800,max(120,count*batch.request_metadata['max_attempts']*180)),
                                      max_transfer=max(25*1024*1024,count*batch.request_metadata['max_attempts']*5*1024*1024),skipped_retry_urls=skipped)
        batch.save(update_fields=['request_metadata'])
        transaction.on_commit(lambda:enqueue_scan(batch.pk))
    return JsonResponse(batch_data(batch),status=202)


@login_required
@require_GET
def coverage(request,project_id,batch_id):
    context=project_context(request,project_id,'scans')
    batch=get_object_or_404(ImportBatch,project=context['project'],pk=batch_id)
    rows=batch.crawl_url_coverage.all()
    source=request.GET.get('source','');outcome=request.GET.get('outcome','')
    if source=='sitemap_only':rows=rows.filter(in_sitemap=True,in_links=False,is_start=False)
    elif source=='both':rows=rows.filter(in_sitemap=True,in_links=True)
    elif source=='links_only':rows=rows.filter(in_links=True,in_sitemap=False)
    if outcome in ('not_visited','success','http_error','download_error','blocked','response','resource_only'):rows=rows.filter(outcome=outcome)
    if request.GET.get('download')=='1':
        def output():
            stream=io.StringIO();writer=csv.writer(stream)
            writer.writerow(['URL','Sitemap','Interne link','Ingepland','Afgehandeld','Uitkomst','HTTP','Linkdiepte'])
            yield stream.getvalue();stream.seek(0);stream.truncate(0)
            for row in rows.order_by('id').iterator(chunk_size=500):
                writer.writerow([row.url,row.in_sitemap,row.in_links,row.queued,row.attempted,row.outcome,row.http_status,row.depth]);yield stream.getvalue();stream.seek(0);stream.truncate(0)
        response=StreamingHttpResponse(output(),content_type='text/csv; charset=utf-8')
        response['Content-Disposition']='attachment; filename="crawl-dekking.csv"';return response
    context.update(batch=batch,page=Paginator(rows.order_by('id'),50).get_page(request.GET.get('page')),source=source,outcome=outcome,
        followups=ImportBatch.objects.filter(project=context['project'],request_metadata__retry_of_batch=str(batch.pk)).order_by('-imported_at')[:20])
    return render(request,'ui/crawl_coverage.html',context)


@login_required
@require_GET
def attempts(request,project_id,batch_id):
    context=project_context(request,project_id,'scans')
    batch=get_object_or_404(ImportBatch,project=context['project'],pk=batch_id)
    rows=batch.crawl_attempts.all()
    url=request.GET.get('url','')
    if url:rows=rows.filter(url_hash=hashlib.sha256(url.encode()).hexdigest())
    if request.GET.get('download')=='1':
        def output():
            stream=io.StringIO();writer=csv.writer(stream)
            writer.writerow(['URL','Poging','Start','Einde','Status','HTTP','Fout','Connect timeout','Read timeout'])
            yield stream.getvalue();stream.seek(0);stream.truncate(0)
            for row in rows.order_by('url_hash','ordinal').iterator(chunk_size=500):
                writer.writerow([row.url,row.ordinal,row.started_at.isoformat(),row.finished_at.isoformat() if row.finished_at else '',row.state,row.http_status,row.error_code,row.details.get('connect_timeout'),row.details.get('read_timeout')])
                yield stream.getvalue();stream.seek(0);stream.truncate(0)
        response=StreamingHttpResponse(output(),content_type='text/csv; charset=utf-8');response['Content-Disposition']='attachment; filename="downloadpogingen.csv"';return response
    context.update(batch=batch,page=Paginator(rows.order_by('url_hash','ordinal'),50).get_page(request.GET.get('page')),url_filter=url)
    return render(request,'ui/crawl_attempts.html',context)
