"""Build a source-driven URL detail without copying or fixing source schemas."""
from collections import defaultdict
from django.db.models import Q
from django.http import Http404
from integrations.url_validation import normalize_url
from projects.hosts import allows_url
from .models import SourceRecord
from .url_inventory import URL_FIELDS, DEFAULT_SOURCES, observation_date, project_inventory


def data_fields(data, prefix=''):
    fields = []
    if isinstance(data, dict) and data:
        for key, value in data.items():
            fields.extend(data_fields(value, f'{prefix} → {key}' if prefix else str(key)))
    elif isinstance(data, list) and data:
        for index, value in enumerate(data, 1):
            fields.extend(data_fields(value, f'{prefix} [{index}]'))
    else:
        if data is None or data == '':
            value = '—'
        elif isinstance(data, bool):
            value = 'Ja' if data else 'Nee'
        else:
            value = str(data)
        fields.append({'label': prefix, 'value': value,
                       'link': data if isinstance(data, str) and normalize_url(data) else None})
    return fields


def url_context(project, url):
    if not isinstance(url, str) or len(url) > 8192 or not allows_url(project, url):
        raise Http404('URL valt buiten dit project.')
    _, inventory = project_inventory(project, target_url=url)
    if not inventory:
        raise Http404('Geen gepubliceerde brongegevens voor deze URL.')
    match = Q()
    for key in URL_FIELDS:
        match |= Q(**{f'data__{key}': url})
    # Fetch batch context once: chart/metadata can be large and should not be repeated by a join.
    batches = {batch.pk: batch for batch in project.import_batches.analysable().select_related('created_by')}
    records = SourceRecord.objects.filter(match, batch_id__in=batches).order_by('id')
    groups = defaultdict(list)
    for record in records:
        primary_url = next((record.data.get(key) for key in URL_FIELDS if record.data.get(key)), None)
        if primary_url != url:
            continue
        batch = batches[record.batch_id]
        stamp, fallback = observation_date(record.data, batch)
        fields = data_fields(record.data)
        groups[batch.source].append({
            'record': record, 'batch': batch, 'date': stamp, 'fallback': fallback,
            'rank': (stamp, not fallback, batch.imported_at, record.pk),
            'columns': [fields[::2], fields[1::2]], 'field_count': len(fields),
        })
    order = {key: index for index, key in enumerate(DEFAULT_SOURCES)}
    sources = []
    for source in sorted(groups, key=lambda key: (order.get(key, len(order)), key)):
        observations = sorted(groups[source], key=lambda item: item['rank'], reverse=True)
        sources.append({'key': source, 'label': observations[0]['batch'].get_source_display(),
                        'observations': observations, 'count': len(observations),
                        'latest_date': observations[0]['date']})
    return {'url_item': inventory[0], 'url_sources': sources,
            'observation_count': sum(group['count'] for group in sources)}
