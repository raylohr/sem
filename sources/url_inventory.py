"""Read-only URL inventory across published imports, with per-source observations."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from django.utils import timezone
from django.db.models import Q
from integrations.url_validation import normalize_url
from projects.hosts import allows_url
from .models import SourceRecord
from .content_types import content_badge

DEFAULT_SOURCES = {'ahrefs': 'Ahrefs', 'gsc': 'GSC', 'sf': 'SF', 'seranking': 'SE Ranking', 'playwright': 'Playwright'}
URL_FIELDS = ('url', 'Address', 'URL', 'Target URL', 'Page URL')


def as_date(value):
    if isinstance(value, datetime):
        return timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    return None


def observation_date(data, batch):
    # Performance dates and crawl observations belong to the source's calendar day.
    for key in ('date', 'Crawl Timestamp', 'Last crawled', 'last_seen', 'Last seen', 'measured_at'):
        stamp = as_date(data.get(key))
        if stamp:
            return stamp, False
    for stamp in (batch.measured_at, batch.period_end):
        if stamp:
            return as_date(stamp), False
    return timezone.localtime(batch.imported_at).date(), True


def project_inventory(project, *, include_data=False, target_url=None, include_fields=False, _records=None, _batches=None):
    fields = ['id', 'source', 'measured_at', 'period_end', 'imported_at', 'request_metadata']
    batches = _batches if _batches is not None else {batch.pk: batch for batch in project.import_batches.filter(
        status__in=('complete', 'incomplete')).only(*fields)
        if batch.request_metadata.get('record_scope') not in ('summary', 'external')}
    columns = {}
    rows = {}
    field_cache = {}
    records = SourceRecord.objects.filter(batch_id__in=batches)
    if target_url is not None:
        match = Q()
        for key in URL_FIELDS:
            match |= Q(**{f'data__{key}': target_url})
        records = records.filter(match)
    records = records.order_by('id').values_list('id', 'batch_id', 'data')
    for record_id, batch_id, data in (_records if _records is not None else records.iterator(chunk_size=1000)):
        url = next((data.get(key) for key in URL_FIELDS if data.get(key)), None)
        if (target_url is not None and url != target_url) or not isinstance(url, str) or not normalize_url(url) or not allows_url(project, url):
            continue
        batch = batches[batch_id]
        source = batch.source
        columns.setdefault(source, DEFAULT_SOURCES.get(source, source.replace('_', ' ').capitalize()))
        stamp, fallback = observation_date(data, batch)
        if url not in rows:
            parts = urlsplit(url)
            rows[url] = {'url': url, 'path': parts.path or '/',
                         'display_path': (parts.path or '/') + ('?' + parts.query if parts.query else ''),
                         'host': parts.netloc, 'observations': {}, 'title': '', 'title_rank': None}
        current = rows[url]['observations'].get(source)
        # Prefer a dated source observation over an import-date fallback on a tie.
        rank = (stamp, not fallback, batch.imported_at, record_id)
        if include_fields:
            from .column_fields import record_fields
            values = rows[url].setdefault('field_values', {})
            for field, value in record_fields(source, batch.request_metadata, data, field_cache):
                previous = values.get(field['id'])
                if previous is None or rank > previous['rank']:
                    values[field['id']] = {'field': field, 'value': value, 'date': stamp,
                        'fallback': fallback, 'rank': rank, 'batch_id': batch_id, 'record_id': record_id}
        title = data.get('Title 1') if source == 'sf' else data.get('Title') if source == 'ahrefs' else data.get('title') if source == 'seranking' else None
        title_rank = (2 if source == 'sf' else 1 if source == 'ahrefs' else 0, *rank)
        if isinstance(title, str) and title.strip() and (rows[url]['title_rank'] is None or title_rank > rows[url]['title_rank']):
            rows[url].update(title=title.strip(), title_rank=title_rank,
                             title_source=DEFAULT_SOURCES[source], title_date=stamp,
                             title_source_name=batch.get_source_display())
        if source == 'gsc':
            status = data.get('gsc_report_status') or batch.request_metadata.get('report_status')
            if isinstance(status, str) and status.strip() and (rows[url].get('index_rank') is None or rank > rows[url]['index_rank']):
                rows[url].update(index_status=status.strip(), index_rank=rank, index_date=stamp,
                                 index_reason=data.get('gsc_indexing_reason') or batch.request_metadata.get('indexing_reason', ''))
        if source == 'sf' and ('Content Type' in data or 'Status Code' in data):
            if rows[url].get('sf_response_rank') is None or rank > rows[url]['sf_response_rank']:
                rows[url]['sf_response_rank'] = rank
                rows[url]['sf_response_badge'] = content_badge(data, source)
                if current is not None:
                    current['badge'] = rows[url]['sf_response_badge']
        if current is None or rank > current['rank']:
            rows[url]['observations'][source] = {
                'date': stamp, 'fallback': fallback, 'batch_id': batch_id,
                'record_id': record_id, 'rank': rank}
            if source == 'sf':
                rows[url]['observations'][source]['badge'] = rows[url].get('sf_response_badge', content_badge({}, source))
            if source == 'ahrefs':
                traffic = None
                try:
                    value = Decimal(str(data.get('Organic traffic', '')))
                    if value.is_finite() and value >= 0:
                        traffic = value
                except InvalidOperation:
                    pass
                rows[url]['observations'][source].update(is_ahrefs=True, traffic=traffic)
            if include_data:
                rows[url]['observations'][source].update(data=data, metadata=batch.request_metadata)
    source_order = {key: index for index, key in enumerate(DEFAULT_SOURCES)}
    ordered_columns = [{'key': key, 'label': columns[key]} for key in sorted(
        columns, key=lambda key: (source_order.get(key, len(source_order)), key))]
    ordered_rows = sorted(rows.values(), key=lambda row: (row['path'], row['url']))
    for row in ordered_rows:
        if 'gsc' in row['observations']:
            row['observations']['gsc'].update(is_gsc=True, index_status=row.get('index_status', 'unknown'),
                                              index_date=row.get('index_date'), index_reason=row.get('index_reason', ''))
        row['cells'] = [row['observations'].get(column['key']) for column in ordered_columns]
    return ordered_columns, ordered_rows
