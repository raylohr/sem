"""Detection and validation of Screaming Frog Internal → All CSV exports."""
import csv
import hashlib
import io
from collections import Counter
from datetime import datetime
from urllib.parse import urlsplit

from django.utils import timezone

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 100000
REQUIRED = {'Address', 'Content Type', 'Status Code', 'Indexability',
            'Indexability Status', 'Title 1', 'Canonical Link Element 1',
            'Crawl Depth', 'Inlinks', 'Outlinks', 'Crawl Timestamp'}


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
        headers = reader.fieldnames or []
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
            rows.append(row)
    except csv.Error:
        raise ValueError('Ongeldige CSV. Exporteer het bestand opnieuw vanuit Screaming Frog.') from None
    if not rows:
        raise ValueError('Het bestand bevat geen URLs.')
    return {'rows': rows, 'checksum': hashlib.sha256(raw).hexdigest(),
            'summary': {'type': 'internal_all', 'html_count': html_count,
                        'hosts': dict(hosts), 'columns': headers,
                        'crawl_start': min(timestamps).isoformat() if timestamps else None,
                        'crawl_end': max(timestamps).isoformat() if timestamps else None,
                        'timezone': str(timezone.get_current_timezone())},
            'measured_at': min(timestamps) if timestamps else None}
