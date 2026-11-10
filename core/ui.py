from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, render, redirect
from django.core.paginator import Paginator
from django.views.decorators.http import require_GET, require_http_methods
from django.views.decorators.cache import never_cache
from projects.models import Project
from projects.permissions import can_edit, can_manage, role_for
from sources.models import ImportBatch
from integrations.gsc import configured
from sources.inventory_query import snapshot
from sources.inventory_index import Inventory, InventoryPending
from sources.inventory_query import DatabaseRows, save_view_preference


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
@require_http_methods(["GET", "POST"])
@snapshot
def project_home(request, project_id):
    context=project_context(request,project_id,'overview')
    project=context['project']
    context.update(stats=project.import_batches.aggregate(records=Sum('record_count')),
        recent=project.import_batches.select_related('created_by')[:8])
    from django.http import QueryDict
    from django.urls import reverse
    from sources.configurable_inventory import selected_columns,ColumnConfigurationForm,legacy_params,build_dataset_table
    from sources.models import InventoryPreference
    try:
        inventory = Inventory(project)
    except InventoryPending as error:
        context['inventory_message'] = str(error)
        return render(request, 'ui/inventory_pending.html', context, status=202)
    source_columns, rows = inventory.source_columns(), DatabaseRows(inventory)
    catalog = inventory.catalog
    columns = selected_columns(project, request.user, catalog, rows)
    form = ColumnConfigurationForm(request.POST if request.method == 'POST' else None, catalog=catalog, columns=columns)
    if request.method == 'POST':
        previous = QueryDict(request.POST.get('return_state', '')[:20000])
        if request.POST.get('columns_action') == 'reset':
            InventoryPreference.objects.filter(project=project, user=request.user).delete()
            available = selected_columns(project, request.user, catalog, rows)
            _, new_state = build_dataset_table(rows, available, previous, catalog)
            return redirect(reverse('ui:project', args=[project.pk]) + '?' + new_state['url_state_query'] + '#url-inventory')
        if request.POST.get('columns_action') == 'save' and form.is_valid():
            configuration = form.configuration(rows)
            InventoryPreference.objects.update_or_create(project=project, user=request.user, defaults={'columns':configuration})
            available = selected_columns(project, request.user, catalog, rows)
            _, new_state = build_dataset_table(rows, available, previous, catalog)
            return redirect(reverse('ui:project', args=[project.pk]) + '?' + new_state['url_state_query'] + '#url-inventory')
    total = len(rows)
    from sources.models import ProjectViewPreference
    preference = ProjectViewPreference.objects.filter(project=project,user=request.user).first()
    explicit = any(key == 'inventory_state' or key in ('url_q','sort','traffic_sort','content_filter','index_filter') or key.startswith(('a_','v_','lo_','hi_','q_','m_')) for key in request.GET)
    incoming = request.GET.copy() if explicit else QueryDict(preference.inventory_query if preference else '',mutable=True)
    if not explicit:
        for key in ('filter_field','edit_filter','url_page'):
            if key in request.GET:incoming[key]=request.GET[key]
    params = legacy_params(incoming, columns, rows)
    rows, controls = build_dataset_table(rows, columns, params, catalog)
    if preference is None or preference.inventory_query != controls['url_state_query']:
        save_view_preference(project, request.user, {'inventory_query': controls['url_state_query']})
    context.update(controls)
    context['inventory_metrics'] = controls['inventory_metrics']
    context.update(url_columns=source_columns, url_total=total, column_form=form,
                   field_catalog_json=[{key: value for key, value in field.items() if key not in ('known_counts', 'choice_samples')} for field in catalog.values()],
                   inventory_page=Paginator(rows,100).get_page(request.GET.get('url_page')))
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
        resource = request.GET.get('resource', 'html' if item.source == 'sf' and item.request_metadata.get('type', 'internal_all') == 'internal_all' else 'all') if item.source in ('sf', 'ahrefs') else 'all'
        if resource != 'all':
            field = 'Content Type' if item.source == 'sf' else 'Content type'
            rows = rows.filter(Q(**{f'data__{field}__istartswith': 'text/html'}) | Q(**{f'data__{field}__istartswith': 'application/xhtml+xml'}))
        query = request.GET.get('q', '').strip()[:500]
        if query:
            if item.request_metadata.get('record_scope') == 'summary':
                rows = rows.filter(data__icontains=query)
            else:
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
        if item.source == 'sf' and item.request_metadata.get('type', 'internal_all') != 'internal_all':
            columns = item.request_metadata.get('columns', [])
            for record in page.object_list:
                record.table_values = [record.data.get(column, '') for column in columns]
            context.update(report_table=True, report_columns=columns)
    if item.source == 'seranking' and item.status in ('complete', 'incomplete'):
        rows = item.records.order_by('id')
        query = request.GET.get('q', '').strip()[:500]
        if query:
            rows = rows.filter(data__icontains=query)
        page = Paginator(rows, 50).get_page(request.GET.get('page'))
        from integrations.seranking import REPORT_COLUMNS
        columns = REPORT_COLUMNS.get(item.request_metadata.get('type'), ())
        for record in page.object_list:
            record.table_values = [record.data.get(column, '') for column in columns]
        context.update(url_page=page, query=query, report_table=True, report_columns=columns)
    context.update(batch=item, records=item.records.order_by('id')[:10] if item.status in ('complete','incomplete') else [])
    return render(request,'ui/batch.html',context)


@never_cache
@login_required
@require_GET
def connections(request, project_id):
    context=project_context(request,project_id,'connections')
    from integrations.seranking import configured as seranking_configured, report_options
    from datetime import timedelta
    from django.utils import timezone
    today = timezone.localdate()
    from integrations.seranking import REPORTS
    se_connections = list(context['project'].seranking_connections.all())
    for connection in se_connections:
        connection.latest_refresh = connection.refreshes.first()
        if connection.latest_refresh:
            connection.latest_results = [{'label': REPORTS.get(key, ('Koppeling', ''))[0], **value}
                for key, value in connection.latest_refresh.results.items()]
    context.update(connections=context['project'].gsc_connections.defer('encrypted_refresh_token'),gsc_configured=configured(),
        seranking_configured=seranking_configured(), seranking_reports=report_options(),
        seranking_connections=se_connections,
        seranking_start=(today-timedelta(days=7)).isoformat(), seranking_end=today.isoformat())
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


@never_cache
@login_required
@require_GET
@snapshot
def performance(request, project_id):
    context=project_context(request,project_id,'performance')
    try:
        inventory = Inventory(context['project'])
    except InventoryPending as error:
        context['inventory_message'] = str(error)
        return render(request, 'ui/inventory_pending.html', context, status=202)
    from sources.models import ProjectViewPreference
    preference=ProjectViewPreference.objects.filter(project=context['project'],user=request.user).first()
    options=dict(preference.performance) if preference else {}
    for key in ('x','x_scale','y_scale','trend'):
        if key in request.GET:options[key]=request.GET[key]
    from sources.performance import indexed_scatter
    context.update(indexed_scatter(inventory, options.get('x', '')))
    for axis in ('x','y'):
        context[axis+'_scale'] = 'linear' if options.get(axis+'_scale') == 'linear' else 'log'
    context['performance_trend'] = options.get('trend','1') != '0'
    saved={'x':context['performance_x']['id'] if context['performance_x'] else '',
           'x_scale':context['x_scale'],'y_scale':context['y_scale'],'trend':'1' if context['performance_trend'] else '0'}
    if preference is None or preference.performance != saved:
        save_view_preference(context['project'], request.user, {'performance': saved})
    return render(request,'ui/performance.html',context)


@never_cache
@login_required
@require_http_methods(['POST'])
def save_performance_preferences(request, project_id):
    from django.http import JsonResponse
    from sources.models import ProjectViewPreference
    import re
    project=project_context(request,project_id,'performance')['project']
    options={key:request.POST.get(key,'') for key in ('x','x_scale','y_scale','trend')}
    if (options['x'] and not re.fullmatch(r'[a-f0-9]{64}',options['x'])) or any(options[key] not in ('log','linear') for key in ('x_scale','y_scale')) or options['trend'] not in ('0','1'):
        return JsonResponse({'error':'Ongeldige grafiekinstellingen.'},status=400)
    ProjectViewPreference.objects.update_or_create(project=project,user=request.user,defaults={'performance':options})
    return JsonResponse({'saved':True})
