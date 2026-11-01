from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404,render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from core.ui import project_context
from sources.models import ImportBatch
from .forms import URLFilterForm
from .models import ProjectURL,URLObservation
from .queries import url_overview,summarize_urls

SOURCE_LABELS=dict(ImportBatch.Source.choices)
FACT_LABELS={
 'http_status':'Gemeten HTTP-status','final_http_status':'HTTP-status na redirects','title':'Titel','missing_title':'Titel ontbreekt',
 'is_indexable':'Technisch indexeerbaar','canonical':'Canonical','content_type':'Contenttype','language':'Taal','word_count':'Woorden',
 'robots_blocked':'Geblokkeerd door robots.txt','scan_error':'Scanfout','gsc_indexing_reason':'GSC-indexeringsreden',
 'gsc_last_crawled':'Laatst gecrawld volgens Google','gsc_clicks':'GSC-klikken','gsc_impressions':'GSC-vertoningen',
 'gsc_ctr':'GSC-CTR (fractie)','gsc_position':'GSC-positie','gsc_date_timezone':'Tijdzone GSC-meetdatum',
 'ahrefs_organic_traffic_estimate':'Ahrefs geschat organisch verkeer','ahrefs_referring_domains':'Ahrefs verwijzende domeinen',
 'ahrefs_backlinks':'Ahrefs backlinks','ahrefs_dofollow_backlinks':'Ahrefs dofollow-backlinks',
 'ahrefs_keywords_top3':'Ahrefs keywords top 3','ahrefs_keywords_top10':'Ahrefs keywords top 10',
 'ahrefs_page_rating':'Ahrefs Page Rating','ahrefs_url_rating':'Ahrefs URL Rating',
 'sitemap_lastmod':'Lastmod volgens sitemap','sitemap_url':'Bron-sitemap','page_fetched':'Pagina opgehaald',
 'audit_title':'Titel uit samengestelde audit','audit_page_type':'Paginatype volgens audit','audit_finding':'Auditbevinding (uittreksel)',
 'audit_action':'Aanbevolen actie (uittreksel)','audit_priority':'Auditprioriteit','audit_evidence':'Bewijsstatus volgens audit',
 'audit_gsc_status':'GSC-status overgenomen in audit','audit_reported_http':'HTTP-status overgenomen in audit',
 'ahrefs_subtree_organic_traffic_estimate':'Ahrefs geschat verkeer voor pad/substructuur',
 'ahrefs_subtree_referring_domains':'Ahrefs verwijzende domeinen voor pad/substructuur',
 'ahrefs_subtree_organic_keywords':'Ahrefs keywords voor pad/substructuur',
 'source_timestamp_without_timezone':'Oorspronkelijk tijdstip (tijdzone onbekend)',
}


def present_url(item):
    item.source_labels=[SOURCE_LABELS.get(source,source) for source in sorted(item.sources or [])]
    item.http_signals=sorted(item.http_signals or [])
    signals=set(item.indexability_signals or [])
    item.indexability_label='Verschillend per meting' if len(signals)>1 else ('Ja' if signals=={True} else ('Nee' if signals=={False} else 'Onbekend'))
    return item


def overview_context(request,project_id):
    context=project_context(request,project_id,'urls')
    form=URLFilterForm(request.GET or None,project=context['project'])
    valid=form.is_valid() if form.is_bound else True
    filters=form.cleaned_data if form.is_bound and valid else {}
    urls,observations=url_overview(context['project'],filters)
    if not valid:
        urls=urls.none();observations=observations.none()
    page=Paginator(urls,25).get_page(request.GET.get('page'))
    page.object_list=[present_url(item) for item in summarize_urls(page.object_list,observations)]
    query=request.GET.copy();query.pop('page',None)
    context.update(form=form,page=page,url_count=page.paginator.count,observation_count=observations.count(),
        scope=filters.get('scope') or 'page',filter_query=query.urlencode(),valid=valid)
    return context


@never_cache
@login_required
@require_GET
def overview(request,project_id):
    context=overview_context(request,project_id)
    return render(request,'catalog/overview.html',context,status=200 if context['valid'] else 400)


@never_cache
@login_required
@require_GET
def api_overview(request,project_id):
    context=overview_context(request,project_id)
    if not context['valid']:
        return JsonResponse({'errors':context['form'].errors.get_json_data()},status=400)
    rows=[]
    for item in context['page']:
        rows.append({'id':str(item.pk),'url':item.normalized_url,'host':item.host,'sources':item.sources,
            'http_signals':item.http_signals,'indexability_signals':item.indexability_signals or [],
            'observation_count':item.observation_count,'first_measurement':item.first_measurement,
            'last_measurement':item.last_measurement,'unknown_dates':item.unknown_dates})
    return JsonResponse({'urls':rows,'total':context['url_count'],'observation_count':context['observation_count'],
        'page':context['page'].number,'scope':context['scope']})


@never_cache
@login_required
@require_GET
def detail(request,project_id,url_id):
    context=project_context(request,project_id,'urls')
    item=get_object_or_404(ProjectURL.objects.filter(observations__batch__status='complete').distinct(),pk=url_id,project=context['project'])
    observations=item.observations.filter(batch__project=context['project'],batch__status='complete').select_related('batch__created_by').order_by(F('measured_date').desc(nulls_last=True),'-batch__imported_at','-id')
    page=Paginator(observations,25).get_page(request.GET.get('page'))
    for observation in page.object_list:
        observation.fact_items=[(FACT_LABELS.get(key,key),('Ja' if value else 'Nee') if isinstance(value,bool) else value) for key,value in observation.facts.items()]
    context.update(url=item,page=page)
    return render(request,'catalog/detail.html',context)


@never_cache
@login_required
@require_GET
def api_detail(request,project_id,url_id):
    context=project_context(request,project_id,'urls')
    item=get_object_or_404(ProjectURL.objects.filter(observations__batch__status='complete').distinct(),pk=url_id,project=context['project'])
    observations=item.observations.filter(batch__project=context['project'],batch__status='complete').select_related('batch__created_by').order_by(F('measured_date').desc(nulls_last=True),'-batch__imported_at','-id')
    page=Paginator(observations,25).get_page(request.GET.get('page'))
    rows=[]
    for observation in page:
        rows.append({'original_url':observation.original_url,'scope':observation.scope,'measured_date':observation.measured_date,
            'measured_at':observation.measured_at,'source':observation.batch.source,'batch_id':str(observation.batch_id),
            'user_id':observation.batch.created_by_id,'username':observation.batch.created_by.get_username(),
            'imported_at':observation.batch.imported_at,'period_start':observation.batch.period_start,'period_end':observation.batch.period_end,
            'source_record_id':observation.source_record_id,'facts':observation.facts})
    return JsonResponse({'id':str(item.pk),'url':item.normalized_url,'observations':rows,'total':page.paginator.count,'page':page.number})
