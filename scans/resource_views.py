import json
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404,render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from core.ui import project_context
from sources.models import ImportBatch
from .models import ResourceMeasurement


@never_cache
@login_required
@require_GET
def inventory(request,project_id,batch_id):
    context=project_context(request,project_id,'scans')
    batch=get_object_or_404(ImportBatch,project=context['project'],pk=batch_id)
    rows=ResourceMeasurement.objects.filter(batch=batch).order_by('purpose','url')
    purpose=request.GET.get('purpose','');outcome=request.GET.get('outcome','')
    if purpose in ('resource','external'):rows=rows.filter(purpose=purpose)
    if outcome:rows=rows.filter(outcome=outcome[:32])
    query=request.GET.get('q','').strip()[:250]
    if query:rows=rows.filter(url__icontains=query)
    if request.GET.get('download')=='1':
        def output():
            yield json.dumps({'type':'resource_scan','batch_id':str(batch.pk),'coverage':batch.request_metadata.get('resource_scan',{})},ensure_ascii=False)+'\n'
            for row in rows.iterator(chunk_size=100):
                yield json.dumps({'type':'resource','id':str(row.pk),'url':row.url,'purpose':row.purpose,'outcome':row.outcome,'http_status':row.http_status,
                    'final_url':row.final_url,'measured_at':row.measured_at.isoformat() if row.measured_at else None,'headers':row.headers,'measurements':row.measurements,'evidence':row.evidence},ensure_ascii=False)+'\n'
                for reference in row.references.all().iterator(chunk_size=500):
                    yield json.dumps({'type':'reference','resource_id':str(row.pk),**reference.details},ensure_ascii=False)+'\n'
        response=StreamingHttpResponse(output(),content_type='application/x-ndjson')
        response['Content-Disposition']='attachment; filename="resource-evidence.jsonl"';return response
    context.update(batch=batch,page=Paginator(rows,50).get_page(request.GET.get('page')),purpose=purpose,outcome=outcome,query=query,
                   coverage=batch.request_metadata.get('resource_scan',{}))
    return render(request,'ui/resources.html',context)


@never_cache
@login_required
@require_GET
def detail(request,project_id,batch_id,resource_id):
    context=project_context(request,project_id,'scans')
    resource=get_object_or_404(ResourceMeasurement,pk=resource_id,batch_id=batch_id,batch__project=context['project'])
    context.update(resource=resource,batch=resource.batch,page=Paginator(resource.references.order_by('source_url','id'),50).get_page(request.GET.get('page')))
    return render(request,'ui/resource.html',context)
