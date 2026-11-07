"""Screaming Frog tabular reports, detected by columns rather than filenames."""
import csv
import hashlib
import io
from collections import Counter
from urllib.parse import urlsplit

from django.utils import timezone
from .screamingfrog import (MAX_BYTES, MAX_ROWS, REQUIRED, canonical_headers, canonical_row, parse_export)


REPORTS = {
    'internal_all': ('Internal · All', 'url'),
    'response_codes': ('Response Codes · All', 'url'),
    'external_all': ('External · All', 'external'),
    'hreflang': ('Hreflang · All', 'url'),
    'page_titles': ('Page Titles · All', 'url'),
    'indexability_summary': ('Indexability · Summary', 'summary'),
    'issues_overview': ('Issues Overview', 'summary'),
    'sitemap_inlinks': ('URLs in Sitemap · Inlinks', 'url'),
    'crawl_archive': ('Crawl Archive · URLs', 'url'),
}


def report_type(headers):
    fields = set(canonical_headers(headers))
    if REQUIRED <= fields:
        return 'internal_all'
    if {'Source', 'Destination', 'Type', 'Link Path', 'Link Position'} <= fields:
        return 'sitemap_inlinks'
    if {'Issue Name', 'Issue Type', 'Issue Priority', 'URLs', 'Description', 'How To Fix'} <= fields:
        return 'issues_overview'
    if {'Path 1', 'Path 2', 'Path 3', 'URLs', 'Indexable', 'Non-Indexable'} <= fields:
        return 'indexability_summary'
    if {'Address', 'Occurrences', 'HTML hreflang 1', 'HTML hreflang 1 URL', 'Indexability'} <= fields:
        return 'hreflang'
    if {'Address', 'Occurrences', 'Title 1', 'Title 1 Length', 'Title 1 Pixel Width', 'Indexability'} <= fields:
        return 'page_titles'
    if {'Address', 'Content Type', 'Status Code', 'Indexability', 'Response Time', 'Redirect URL'} <= fields:
        return 'response_codes'
    if {'Address', 'Content Type', 'Status Code', 'Status', 'Crawl Depth', 'Inlinks'} <= fields and 'Indexability' not in fields:
        return 'external_all'
    return None


def valid_url(value):
    try:
        parts = urlsplit(value)
        parts.port
        return (parts.scheme in ('http', 'https') and parts.hostname and
                not parts.username and not parts.password and not any(c.isspace() for c in value))
    except (ValueError, TypeError):
        return False


def parse_table(headers, values, *, checksum, file_format='csv', worksheet=None):
    if len(headers) > 512:
        raise ValueError('Maximaal 512 kolommen per rapport.')
    kind = report_type(headers)
    if not kind:
        raise ValueError('Bestand niet herkend als ondersteund Screaming Frog-rapport.')
    columns = canonical_headers(headers)
    if len(columns) != len(set(columns)):
        raise ValueError('Meerdere kolommen verwijzen naar dezelfde Engelse kolom.')
    if kind == 'internal_all':
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(headers)
        for row in values:
            writer.writerow(row)
            if stream.tell() > MAX_BYTES:
                raise ValueError('Rapport bevat te veel tabeldata (maximaal 20 MB).')
        result = parse_export(io.BytesIO(stream.getvalue().encode('utf-8')))
    else:
        rows, seen, size = [], set(), 0
        for line, values_row in enumerate(values, 2):
            if not any(values_row):
                continue
            if len(rows) >= MAX_ROWS:
                raise ValueError('Maximaal 100.000 rijen per import.')
            if len(values_row) != len(headers):
                raise ValueError(f'Regel {line}: het aantal kolommen klopt niet.')
            size += sum(len(value) for value in values_row)
            if size > MAX_BYTES:
                raise ValueError('Rapport bevat te veel tabeldata (maximaal 20 MB).')
            row = canonical_row(dict(zip(headers, values_row)))
            if kind == 'sitemap_inlinks':
                for key in ('Source', 'Destination'):
                    if not valid_url(row[key]):
                        raise ValueError(f'Regel {line}: ongeldige {key}-URL.')
                row['Address'] = row['Destination']
            elif REPORTS[kind][1] != 'summary':
                if not valid_url(row['Address']):
                    raise ValueError(f'Regel {line}: ongeldig URL-adres.')
                if row['Address'] in seen:
                    raise ValueError(f'Regel {line}: dubbel URL-adres in het bestand.')
                seen.add(row['Address'])
            rows.append(row)
        result = {'rows': rows, 'measured_at': None, 'summary': {
            'columns': columns, 'original_columns': headers,
            'hosts': dict(Counter(urlsplit(row['Address']).hostname for row in rows if 'Address' in row)),
            'html_count': sum(row.get('Content Type', '').lower().startswith(('text/html', 'application/xhtml+xml')) for row in rows),
            'timezone': str(timezone.get_current_timezone()),
        }}
    label, scope = REPORTS[kind]
    result['summary'].update(type=kind, report_label=label, record_scope=scope,
                             allow_empty=kind != 'internal_all', file_format=file_format)
    if worksheet is not None:
        result['summary']['worksheet'] = worksheet
    return dict(result, checksum=checksum, source='sf', label='Screaming Frog · ' + label,
                parser_version='sf-reports-v2')


def parse_csv(raw):
    try:
        content = raw.decode('utf-8-sig')
        delimiter = ';' if content.partition('\n')[0].count(';') > content.partition('\n')[0].count(',') else ','
        reader = csv.reader(io.StringIO(content, newline=''), delimiter=delimiter, strict=True)
        headers = next(reader, [])
        return parse_table(headers, reader, checksum=hashlib.sha256(raw).hexdigest())
    except UnicodeDecodeError:
        raise ValueError('Gebruik een CSV-export met UTF-8-codering.') from None
    except csv.Error:
        raise ValueError('Ongeldige CSV. Exporteer het bestand opnieuw vanuit Screaming Frog.') from None
