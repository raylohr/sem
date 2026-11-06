from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, render
from django.core.paginator import Paginator
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache
from projects.models import Project
from projects.permissions import can_edit, can_manage, role_for
from sources.models import ImportBatch
from integrations.gsc import configured


def project_context(request, project_id, active):
    project = get_object_or_404(Project.objects.visible_to(request.user), pk=project_id)
    return {'project': project, 'active': active, 'role': role_for(request.user, project),
            'can_edit': can_edit(request.user, project), 'can_manage': can_manage(request.user, project)}


@never_cache
@login_required
@require_GET
def dashboard(request):
    projects = Project.objects.visible_to(request.user).annotate(
        batch_count=Count('import_batches', distinct=True),
        active_count=Count('import_batches', filter=Q(import_batches__status__in=['pending','processing']),distinct=True))
    count = projects.count()
    query = request.GET.get('q', '').strip()[:200]
    if query:
        projects = projects.filter(name__icontains=query)
    return render(request, 'ui/dashboard.html', {'projects':projects,'project_count':count,'query':query,'active':'projects'})


@never_cache
@login_required
@require_GET
def project_home(request, project_id):
    context=project_context(request,project_id,'overview')
    project=context['project']
    context.update(stats=project.import_batches.aggregate(records=Sum('record_count')),
        recent=project.import_batches.select_related('created_by')[:8])
    from sources.url_inventory import project_inventory
    columns, rows = project_inventory(project)
    query = request.GET.get('url_q', '').strip()[:500]
    total = len(rows)
    if query:
        rows = [row for row in rows if query.casefold() in row['url'].casefold()]
    context.update(url_columns=columns, url_total=total, url_query=query,
                   inventory_page=Paginator(rows, 100).get_page(request.GET.get('url_page')))
    return render(request,'ui/project.html',context)


@never_cache
@login_required
@require_GET
def sources(request, project_id):
    context=project_context(request,project_id,'sources')
    batches=context['project'].import_batches.select_related('created_by')
    source=request.GET.get('source','')
    status=request.GET.get('status','')
    if source in ImportBatch.Source.values:
        batches=batches.filter(source=source)
    if status in ImportBatch.Status.values:
        batches=batches.filter(status=status)
    context.update(page=Paginator(batches,25).get_page(request.GET.get('page')),source=source,status=status,
        source_choices=ImportBatch.Source.choices,status_choices=ImportBatch.Status.choices)
    return render(request,'ui/sources.html',context)


@never_cache
@login_required
@require_GET
def batch(request, project_id, batch_id):
    context=project_context(request,project_id,'sources')
    item=get_object_or_404(ImportBatch.objects.select_related('created_by'),pk=batch_id,project=context['project'])
    if item.method == 'file' and item.status == 'complete':
        rows = item.records.order_by('id')
        resource = request.GET.get('resource', 'html' if item.source == 'sf' else 'all') if item.source in ('sf', 'ahrefs') else 'all'
        if resource != 'all':
            field = 'Content Type' if item.source == 'sf' else 'Content type'
            rows = rows.filter(Q(**{f'data__{field}__istartswith': 'text/html'}) | Q(**{f'data__{field}__istartswith': 'application/xhtml+xml'}))
        query = request.GET.get('q', '').strip()[:500]
        if query:
            rows = rows.filter(**{('data__Address__icontains' if item.source == 'sf' else 'data__URL__icontains'): query})
        page = Paginator(rows, 50).get_page(request.GET.get('page'))
        for record in page.object_list:
            record.url_address = record.data.get('Address') or record.data.get('URL')
            record.last_crawled = record.data.get('Last crawled', '')
            record.indexing_reason = record.data.get('gsc_indexing_reason') or item.request_metadata.get('indexing_reason', '')
            record.sf_status = record.data.get('Status Code', record.data.get('HTTP status code', ''))
            record.sf_indexability = record.data.get('Indexability', {'true': 'Indexable', 'false': 'Non-Indexable'}.get(record.data.get('Is indexable page'), ''))
            record.sf_title = record.data.get('Title 1', record.data.get('Title', ''))
            record.page_rating = record.data.get('PR', '')
            record.organic_traffic = record.data.get('Organic traffic', '')
        context.update(url_page=page, resource=resource, query=query)
    context.update(batch=item, records=item.records.order_by('id')[:10] if item.status in ('complete','incomplete') else [])
    return render(request,'ui/batch.html',context)


@never_cache
@login_required
@require_GET
def connections(request, project_id):
    context=project_context(request,project_id,'connections')
    context.update(connections=context['project'].gsc_connections.defer('encrypted_refresh_token'),gsc_configured=configured())
    return render(request,'ui/connections.html',context)


@never_cache
@login_required
@require_GET
def source_record(request,project_id,batch_id,record_id):
    from sources.models import SourceRecord
    context=project_context(request,project_id,'sources')
    batch=get_object_or_404(ImportBatch.objects.select_related('created_by'),pk=batch_id,project=context['project'],status__in=('complete','incomplete'))
    record=get_object_or_404(SourceRecord,pk=record_id,batch=batch)
    context.update(batch=batch,record=record)
    return render(request,'ui/source_record.html',context)


@never_cache
@login_required
@require_GET
def url_detail(request, project_id):
    from sources.url_detail import url_context
    context = project_context(request, project_id, 'overview')
    context.update(url_context(context['project'], request.GET.get('url')))
    return render(request, 'ui/url_detail.html', context)
