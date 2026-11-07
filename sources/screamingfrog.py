"""Detection and validation of Screaming Frog Internal → All CSV exports."""
import csv
import hashlib
import io
import json
from collections import Counter
from datetime import datetime, timezone as datetime_timezone
from urllib.parse import urlsplit

from django.utils import timezone

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 100000
REQUIRED = {'Address', 'Content Type', 'Status Code', 'Indexability',
            'Indexability Status', 'Title 1', 'Canonical Link Element 1',
            'Crawl Depth', 'Inlinks', 'Outlinks', 'Crawl Timestamp'}


# English export headers are the canonical storage schema. Unknown columns survive.
DUTCH_HEADERS = {
    'Adres': 'Address', 'Inhoudstype': 'Content Type', 'Statuscode': 'Status Code',
    'Indexeerbaarheid': 'Indexability', 'Indexeerbaarheidsstatus': 'Indexability Status',
    'Titel 1': 'Title 1', 'Lengte van titel 1': 'Title 1 Length',
    'Pixelbreedte van titel 1': 'Title 1 Pixel Width',
    'Lengte van meta description 1': 'Meta Description 1 Length',
    'Pixelbreedte van meta description 1': 'Meta Description 1 Pixel Width',
    'Meta description 1': 'Meta Description 1', 'Meta keywords 1': 'Meta Keywords 1',
    'Lengte van meta keywords 1': 'Meta Keywords 1 Length',
    'Lengte van H1-1': 'H1-1 Length', 'Lengte van H2-1': 'H2-1 Length',
    'Lengte van H2-2': 'H2-2 Length', 'Meta robots 1': 'Meta Robots 1',
    'Meta refresh 1': 'Meta Refresh 1', 'Canonical-linkelement 1': 'Canonical Link Element 1',
    'amphtml-linkelement': 'amphtml Link Element', 'Grootte (bytes)': 'Size (bytes)',
    'Overgedragen (bytes)': 'Transferred (bytes)',
    'Totaal overgedragen (bytes)': 'Total Transferred (bytes)',
    'CO2-beoordeling': 'CO2 Rating', 'Aantal woorden': 'Word Count',
    'Aantal zinnen': 'Sentence Count', 'Gemiddeld aantal woorden per zin': 'Average Words Per Sentence',
    'Flesch Reading Ease-score': 'Flesch Reading Ease Score', 'Leesbaarheid': 'Readability',
    'Tekstverhouding': 'Text Ratio', 'Crawldiepte': 'Crawl Depth', 'Mapdiepte': 'Folder Depth',
    'Unieke inlinks': 'Unique Inlinks', 'Unieke JS-inlinks': 'Unique JS Inlinks',
    '% van totaal': '% of Total', 'Unieke outlinks': 'Unique Outlinks',
    'Unieke JS-outlinks': 'Unique JS Outlinks', 'Externe outlinks': 'External Outlinks',
    'Unieke externe outlinks': 'Unique External Outlinks',
    'Unieke externe JS-outlinks': 'Unique External JS Outlinks',
    'Dichtstbijzijnde bijna-duplicaat': 'Closest Near Duplicate Match',
    'Aantal bijna-duplicaten': 'No. Near Duplicates', 'Spelfouten': 'Spelling Errors',
    'Grammaticafouten': 'Grammar Errors', 'Reactietijd': 'Response Time',
    'Omleidings-URL': 'Redirect URL', 'Type omleiding': 'Redirect Type',
    'Taal': 'Language', 'HTTP-versie': 'HTTP Version',
    'Alternatieve mobiele link': 'Mobile Alternate Link',
    'Dichtstbijzijnde semantisch vergelijkbare adres': 'Closest Semantically Similar Address',
    'Score voor semantische gelijkenis': 'Semantic Similarity Score',
    'Aantal semantisch vergelijkbaar': 'No. Semantically Similar',
    'Score voor semantische relevantie': 'Semantic Relevance Score',
    'URL-gecodeerd adres': 'URL Encoded Address', 'Crawltijdstempel': 'Crawl Timestamp',
}
VALUE_ALIASES = {
    'Indexability': {'Indexeerbaar': 'Indexable', 'Niet-indexeerbaar': 'Non-Indexable'},
    'Indexability Status': {'Omgeleid': 'Redirected', 'Gecanonicaliseerd': 'Canonicalised',
                           'Clientfout': 'Client Error', 'Serverfout': 'Server Error'},
    'Redirect Type': {'HTTP-omleiding': 'HTTP Redirect',
                      'JavaScript-omleiding': 'JavaScript Redirect'},
}


DUTCH_HEADERS.update({
    'Voorkomens': 'Occurrences', 'Bron': 'Source', 'Bestemming': 'Destination',
    'Padtype': 'Path Type', 'Linkpad': 'Link Path', 'Linkpositie': 'Link Position',
    'Linkoorsprong': 'Link Origin', 'Pad 1': 'Path 1', 'Pad 2': 'Path 2', 'Pad 3': 'Path 3',
    "URL's": 'URLs', 'Indexeerbaar': 'Indexable', 'Niet-indexeerbaar': 'Non-Indexable',
    'Probleemnaam': 'Issue Name', 'Probleemtype': 'Issue Type',
    'Probleemprioriteit': 'Issue Priority', 'Beschrijving': 'Description',
    'Hoe op te lossen': 'How To Fix', 'Help-URL': 'Help URL',
})
VALUE_ALIASES.update({
    'Issue Type': {'Waarschuwing': 'Warning', 'Fout': 'Issue', 'Kans': 'Opportunity'},
    'Issue Priority': {'Laag': 'Low', 'Gemiddeld': 'Medium', 'Hoog': 'High'},
    'Status': {'Geblokkeerd door robots.txt': 'Blocked by robots.txt'},
})
VALUE_ALIASES['Indexability Status']['Geblokkeerd door robots.txt'] = 'Blocked by robots.txt'


def canonical_headers(headers):
    return [DUTCH_HEADERS.get(header, header) for header in headers]


def canonical_row(row, *, normalize_time=False):
    result = {}
    for key, value in row.items():
        key = DUTCH_HEADERS.get(key, key)
        if key in result:
            raise ValueError('Meerdere kolommen verwijzen naar dezelfde Engelse kolom.')
        value = VALUE_ALIASES.get(key, {}).get(value, value)
        if normalize_time and key == 'Crawl Timestamp' and value:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            value = dt.astimezone(datetime_timezone.utc).isoformat()
        result[key] = value
    return result


def content_checksum(rows, report_type="internal_all"):
    """Versioned, order-independent fingerprint of all stored export values."""
    encoded = sorted(json.dumps(canonical_row(row, normalize_time=True), sort_keys=True, ensure_ascii=False,
                                separators=(',', ':')) for row in rows)
    payload = json.dumps(['sf-internal-content-v1' if report_type == 'internal_all' else 'sf-report-content-v1:' + report_type, encoded], ensure_ascii=False,
                         separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def parse_export(upload):
    raw = upload.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Het bestand is te groot. Maximaal 20 MB.')
    try:
        content = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('Gebruik een CSV-export met UTF-8-codering.') from None
    delimiter = ';' if content.partition('\n')[0].count(';') > content.partition('\n')[0].count(',') else ','
    reader = csv.DictReader(io.StringIO(content, newline=''), delimiter=delimiter, strict=True)
    try:
        original_headers = reader.fieldnames or []
        headers = canonical_headers(original_headers)
        reader.fieldnames = headers
        if len(headers) != len(set(headers)) or not REQUIRED.issubset(headers):
            raise ValueError('Bestand niet herkend. Exporteer in Screaming Frog: Internal → All → Export (CSV, XLS of XLSX).')
        rows, urls, hosts, timestamps = [], set(), Counter(), []
        html_count = 0
        for line, row in enumerate(reader, 2):
            if len(rows) >= MAX_ROWS:
                raise ValueError('Maximaal 100.000 URLs per import.')
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f'Regel {line}: het aantal kolommen klopt niet.')
            address = row['Address']
            try:
                parsed = urlsplit(address)
                valid = parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password
                parsed.port
            except ValueError:
                valid = False
            if not valid or any(c.isspace() for c in address):
                raise ValueError(f'Regel {line}: ongeldig URL-adres.')
            if address in urls:
                raise ValueError(f'Regel {line}: dubbel URL-adres in het bestand.')
            urls.add(address)
            hosts[parsed.hostname] += 1
            html_count += row['Content Type'].lower().startswith(('text/html', 'application/xhtml+xml'))
            stamp = row['Crawl Timestamp'].strip()
            if stamp:
                try:
                    dt = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
                    if timezone.is_naive(dt):
                        dt = timezone.make_aware(dt)
                    timestamps.append(dt)
                except ValueError:
                    raise ValueError(f'Regel {line}: ongeldige crawl-tijd.') from None
            rows.append(canonical_row(row))
    except csv.Error:
        raise ValueError('Ongeldige CSV. Exporteer het bestand opnieuw vanuit Screaming Frog.') from None
    if not rows:
        raise ValueError('Het bestand bevat geen URLs.')
    return {'rows': rows, 'checksum': hashlib.sha256(raw).hexdigest(),
            'summary': {'type': 'internal_all', 'html_count': html_count,
                        'hosts': dict(hosts), 'columns': headers, 'original_columns': original_headers,
                        'crawl_start': min(timestamps).isoformat() if timestamps else None,
                        'crawl_end': max(timestamps).isoformat() if timestamps else None,
                        'timezone': str(timezone.get_current_timezone())},
            'measured_at': min(timestamps) if timestamps else None}
