from django.contrib.postgres.aggregates import ArrayAgg
from django.db.models import Count, Exists, Max, Min, OuterRef, Q
from .models import ProjectURL,URLObservation


def selected_observations(project,filters):
    query=URLObservation.objects.filter(url__project=project,batch__project=project,batch__status__in=('complete','incomplete'),scope=filters.get('scope') or 'page')
    if filters.get('source'):
        query=query.filter(batch__source=filters['source'])
    if filters.get('batch'):
        query=query.filter(batch=filters['batch'])
    if filters.get('http'):
        query=query.filter(facts__http_status=filters['http'])
    if filters.get('indexability')=='yes':
        query=query.filter(facts__is_indexable=True)
    elif filters.get('indexability')=='no':
        query=query.filter(facts__is_indexable=False)
    elif filters.get('indexability')=='unknown':
        query=query.exclude(facts__has_key='is_indexable')
    if filters.get('signal')=='http_error':
        query=query.filter(facts__http_status__gte=400)
    elif filters.get('signal') in ('missing_title','robots_blocked'):
        query=query.filter(**{'facts__'+filters['signal']:True})
    if filters.get('content')=='html':
        query=query.filter(facts__content_type__icontains='html')
    elif filters.get('content')=='image':
        query=query.filter(facts__content_type__istartswith='image/')
    elif filters.get('content')=='other':
        query=query.filter(facts__has_key='content_type').exclude(facts__content_type__icontains='html').exclude(facts__content_type__istartswith='image/')
    date_query=Q()
    if filters.get('date_from'):
        date_query &= Q(measured_date__gte=filters['date_from'])
    if filters.get('date_to'):
        date_query &= Q(measured_date__lte=filters['date_to'])
    if filters.get('date_from') or filters.get('date_to'):
        if filters.get('include_unknown'):
            date_query |= Q(measured_date__isnull=True)
        query=query.filter(date_query)
    if filters.get('q'):
        query=query.filter(url__normalized_url__icontains=filters['q'])
    if filters.get('host'):
        host=filters['host'].lower().rstrip('.')
        try:
            host=host.encode('idna').decode('ascii')
        except UnicodeError:
            return query.none()
        query=query.filter(url__host=host)
    return query


def url_overview(project,filters):
    observations=selected_observations(project,filters)
    # Correlated existence avoids multiplying rows through joins when multiple filters apply.
    urls=ProjectURL.objects.filter(project=project).filter(Exists(observations.filter(url_id=OuterRef('pk'))))
    return urls.order_by("normalized_url","id"),observations


def summarize_urls(items,observations):
    """Aggregate only the current page instead of grouping the entire catalog."""
    items=list(items)
    if not items:
        return items
    rows=observations.filter(url_id__in=[item.pk for item in items]).values('url_id').annotate(
        observation_count=Count('id'),
        sources=ArrayAgg('batch__source',distinct=True),
        http_signals=ArrayAgg('facts__http_status',distinct=True,filter=Q(facts__has_key='http_status')),
        indexability_signals=ArrayAgg('facts__is_indexable',distinct=True,filter=Q(facts__has_key='is_indexable')),
        first_measurement=Min('measured_date'),
        last_measurement=Max('measured_date'),
        unknown_dates=Count('id',filter=Q(measured_date__isnull=True)),
    )
    summaries={row['url_id']:row for row in rows}
    for item in items:
        row=summaries.get(item.pk,{})
        for key in ('observation_count','sources','http_signals','indexability_signals','first_measurement','last_measurement','unknown_dates'):
            setattr(item,key,row.get(key))
    return items
