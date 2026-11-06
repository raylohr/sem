"""Read-only URL inventory across published imports, with per-source observations."""
from datetime import date, datetime
from urllib.parse import urlsplit

from django.utils import timezone
from integrations.url_validation import normalize_url
from projects.hosts import allows_url
from .models import SourceRecord

DEFAULT_SOURCES = {'ahrefs': 'Ahrefs', 'gsc': 'GSC', 'sf': 'SF'}
URL_FIELDS = ('url', 'Address', 'URL', 'Target URL', 'Page URL')


def as_date(value):
    if isinstance(value, datetime):
        return value.date()
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


def project_inventory(project):
    batches = {batch.pk: batch for batch in project.import_batches.filter(
        status__in=('complete', 'incomplete')).only(
            'id', 'source', 'measured_at', 'period_end', 'imported_at')}
    columns = dict(DEFAULT_SOURCES)
    rows = {}
    records = SourceRecord.objects.filter(batch_id__in=batches).order_by('id').values_list('id', 'batch_id', 'data')
    for record_id, batch_id, data in records.iterator(chunk_size=1000):
        url = next((data.get(key) for key in URL_FIELDS if data.get(key)), None)
        if not isinstance(url, str) or not normalize_url(url) or not allows_url(project, url):
            continue
        batch = batches[batch_id]
        source = batch.source
        columns.setdefault(source, source.replace('_', ' ').capitalize())
        stamp, fallback = observation_date(data, batch)
        if url not in rows:
            parts = urlsplit(url)
            rows[url] = {'url': url, 'path': parts.path or '/',
                         'display_path': (parts.path or '/') + ('?' + parts.query if parts.query else ''),
                         'host': parts.netloc, 'observations': {}}
        current = rows[url]['observations'].get(source)
        # Prefer a dated source observation over an import-date fallback on a tie.
        rank = (stamp, not fallback, batch.imported_at, record_id)
        if current is None or rank > current['rank']:
            rows[url]['observations'][source] = {
                'date': stamp, 'fallback': fallback, 'batch_id': batch_id,
                'record_id': record_id, 'rank': rank}
    ordered_columns = [{'key': key, 'label': label} for key, label in columns.items()]
    ordered_rows = sorted(rows.values(), key=lambda row: (row['path'], row['url']))
    for row in ordered_rows:
        row['cells'] = [row['observations'].get(column['key']) for column in ordered_columns]
    return ordered_columns, ordered_rows
