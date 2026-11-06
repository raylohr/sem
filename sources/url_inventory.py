"""Read-only URL inventory across published imports, with per-source observations."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from django.utils import timezone
from integrations.url_validation import normalize_url
from projects.hosts import allows_url
from .models import SourceRecord
from .content_types import content_badge

DEFAULT_SOURCES = {'ahrefs': 'Ahrefs', 'gsc': 'GSC', 'sf': 'SF'}
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


def project_inventory(project, *, include_data=False):
    fields = ['id', 'source', 'measured_at', 'period_end', 'imported_at']
    if include_data:
        fields.append('request_metadata')
    batches = {batch.pk: batch for batch in project.import_batches.filter(
        status__in=('complete', 'incomplete')).only(*fields)}
    columns = {}
    rows = {}
    records = SourceRecord.objects.filter(batch_id__in=batches).order_by('id').values_list('id', 'batch_id', 'data')
    for record_id, batch_id, data in records.iterator(chunk_size=1000):
        url = next((data.get(key) for key in URL_FIELDS if data.get(key)), None)
        if not isinstance(url, str) or not normalize_url(url) or not allows_url(project, url):
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
        title = data.get('Title 1') or data.get('Title')
        if isinstance(title, str) and title.strip() and (rows[url]['title_rank'] is None or rank > rows[url]['title_rank']):
            rows[url].update(title=title.strip(), title_rank=rank)
        if current is None or rank > current['rank']:
            rows[url]['observations'][source] = {
                'date': stamp, 'fallback': fallback, 'batch_id': batch_id,
                'record_id': record_id, 'rank': rank}
            if source == 'sf':
                rows[url]['observations'][source]['badge'] = content_badge(data, source)
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
        row['cells'] = [row['observations'].get(column['key']) for column in ordered_columns]
    return ordered_columns, ordered_rows
