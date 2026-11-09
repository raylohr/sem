"""Recognize Ahrefs Site Audit CSV exports by their columns."""
import csv
import hashlib
import io
import re
from datetime import date

from integrations.url_validation import normalize_url
from .screamingfrog import MAX_ROWS

BASE_COLUMNS = {'PR', 'URL', 'Title', 'Content type', 'Is rendered page',
                'HTTP status code', 'Organic traffic', 'Depth', 'Is indexable page'}
TITLE_COLUMNS = {'Title length', 'No. of titles', 'Title patch', 'Title patch status'}


def parse_ahrefs(raw, filename=''):
    """Return None for other CSV schemas; never treat a filename as source proof."""
    try:
        encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
        text = raw.decode(encoding)
    except UnicodeError:
        raise ValueError('Gebruik een CSV-bestand met UTF-8- of UTF-16-codering.') from None
    try:
        first = text.partition('\n')[0]
        delimiter = max((',', ';', '\t'), key=first.count)
        reader = csv.DictReader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True)
        headers = reader.fieldnames or []
        if not BASE_COLUMNS.issubset(headers):
            if {'UR', 'Page URL', 'Referring domains'}.issubset(headers):
                raise ValueError('Dit is een Ahrefs Site Explorer-export. Ondersteund zijn nu Site Audit-pagina-exports en ontbrekende title-tags.')
            return None
        if len(headers) != len(set(headers)):
            raise ValueError('Dubbele CSV-kolommen in de Ahrefs-export.')
        if TITLE_COLUMNS.issubset(headers):
            kind, label = 'ahrefs_site_audit_missing_titles', 'Site Audit · Ontbrekende of lege title-tags'
        elif {'No. of all inlinks', 'First found at'}.issubset(headers):
            kind, label = 'ahrefs_site_audit_pages', 'Site Audit · Pagina’s'
        else:
            raise ValueError('Ahrefs Site Audit-rapport niet herkend. Exporteer het pagina-overzicht of het rapport met ontbrekende title-tags.')
        rows = []
        for line, row in enumerate(reader, 2):
            if len(rows) >= MAX_ROWS:
                raise ValueError('Maximaal 100.000 URL-rijen per import.')
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f'Regel {line}: het aantal kolommen klopt niet.')
            if not normalize_url(row['URL']):
                raise ValueError(f'Regel {line}: ongeldig URL-adres.')
            rows.append(row)
    except csv.Error:
        raise ValueError('Ongeldige CSV. Exporteer het bestand opnieuw vanuit Ahrefs Site Audit.') from None
    if not rows:
        raise ValueError('Het bestand bevat geen URLs.')
    suggested_date = None
    matches = re.findall(r'(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)', filename)
    if matches:
        try:
            suggested_date = date.fromisoformat(matches[-1]).isoformat()
        except ValueError:
            pass
    return {'source': 'ahrefs', 'label': 'Ahrefs · ' + label,
            'parser_version': 'ahrefs-site-audit-csv-v1', 'rows': rows,
            'checksum': hashlib.sha256(raw).hexdigest(), 'measured_at': None,
            'summary': {'type': kind, 'report_label': label, 'columns': headers,
                        'suggested_date': suggested_date}}
