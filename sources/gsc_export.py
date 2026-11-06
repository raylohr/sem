"""Read GSC Page indexing URL-detail reports in English or Dutch."""
import csv
import hashlib
import io
from pathlib import PurePosixPath
import zipfile
from datetime import date
from .screamingfrog import MAX_BYTES, MAX_ROWS
from integrations.url_validation import normalize_url

SHEET_NAMES = {'table': 'table', 'tabel': 'table', 'chart': 'chart', 'diagram': 'chart', 'metadata': 'metadata'}
ALIASES = {'Laatst gecrawld': 'Last crawled', 'Datum': 'Date',
           "Betreffende pagina's": 'Affected pages', 'Waarde': 'Value'}
NON_INDEXED_REASONS = {
    'pagina met omleiding', 'page with redirect',
    'alternatieve pagina met correcte canonieke tag', 'alternate page with proper canonical tag',
    'niet gevonden (404)', 'not found (404)',
    'gecrawld - momenteel niet geïndexeerd', 'crawled - currently not indexed',
    'gevonden - momenteel niet geïndexeerd', 'discovered - currently not indexed',
    'uitgesloten door tag noindex', "excluded by ‘noindex’ tag", "excluded by 'noindex' tag",
    'geblokkeerd vanwege verboden toegang (403)', 'blocked due to access forbidden (403)',
    'geblokkeerd vanwege ander 4xx-probleem', 'blocked due to other 4xx issue',
    'fout met omleiding', 'redirect error', 'serverfout (5xx)', 'server error (5xx)',
    'dubbele pagina, google heeft een andere canonieke pagina gekozen dan de gebruiker',
    'duplicate, google chose different canonical than user',
    'dubbele pagina zonder door de gebruiker geselecteerde canonieke versie',
    'duplicate without user-selected canonical',
    'geblokkeerd wegens ongeautoriseerd verzoek (401)', 'blocked due to unauthorized request (401)',
    'geblokkeerd door robots.txt', 'blocked by robots.txt',
}


def normalize_table(headers, rows):
    normalized = [ALIASES.get(header, header) for header in headers]
    if len(headers) != len(set(headers)) or len(normalized) != len(set(normalized)):
        raise ValueError('Dubbele kolommen in het GSC-rapport.')
    return normalized, [{ALIASES.get(key, key): value for key, value in row.items()} for row in rows]


def parse_gsc_tables(tables, *, checksum, filename='', file_format='zip'):
    if 'table' not in tables:
        raise ValueError('Dit is een GSC-indexeringsoverzicht zonder URL-lijst. Exporteer het detailrapport van een indexeringsreden of geïndexeerde pagina’s.')
    if not {'table', 'chart', 'metadata'}.issubset(tables):
        raise ValueError('GSC-export niet herkend. Verwacht een URL-detailrapport met Tabel/Table, Diagram/Chart en Metadata.')
    headers, original_rows = tables['table']
    normalized_headers, table = normalize_table(headers, original_rows)
    chart_headers, chart = normalize_table(*tables['chart'])
    meta_headers, metadata = normalize_table(*tables['metadata'])
    if not {'URL', 'Last crawled'}.issubset(normalized_headers) or not {'Date', 'Affected pages'}.issubset(chart_headers) or not {'Property', 'Value'}.issubset(meta_headers):
        raise ValueError('GSC-exporttype niet herkend. Verwacht een Page indexing URL-detailrapport.')
    if not table:
        raise ValueError('Het bestand bevat geen URLs.')
    reason_values = {row['Value'].strip() for row in metadata if row['Property'].strip().lower() in ('issue', 'probleem') and row['Value'].strip()}
    if len(reason_values) > 1:
        raise ValueError('Tegenstrijdige indexeringsredenen in GSC Metadata.')
    reason = next(iter(reason_values), '')
    status = 'not_indexed' if reason.lower() in NON_INDEXED_REASONS else 'unknown'
    if reason.lower() in ('geïndexeerd', 'indexed', 'pagina is geïndexeerd', 'page is indexed'):
        status = 'indexed'
    if not reason and '-coverage-valid-' in filename.lower():
        status = 'indexed'  # Filename hint only, confirmed by the user in the preview.
    seen, records, unknown_crawl_dates, invalid_urls = set(), [], 0, 0
    for number, (row, original) in enumerate(zip(table, original_rows), 2):
        if not normalize_url(row['URL']):
            invalid_urls += 1  # The shared project host filter excludes this row before staging.
        if row['URL'] in seen:
            raise ValueError(f'Tabel regel {number}: dubbel URL-adres.')
        seen.add(row['URL'])
        stamp = row['Last crawled'].strip()
        if stamp:
            try:
                crawl_date = date.fromisoformat(stamp[:10])
            except ValueError:
                raise ValueError(f'Tabel regel {number}: ongeldige crawl-datum.') from None
            if crawl_date == date(1970, 1, 1):
                stamp = ''
                unknown_crawl_dates += 1
            else:
                stamp = crawl_date.isoformat()
        # Original columns stay intact; canonical fields allow the shared URL views to work.
        record = {**original, 'URL': row['URL'], 'Last crawled': stamp,
                  'gsc_last_crawled_raw': row['Last crawled']}
        if reason:
            record.update(gsc_indexing_reason=reason, gsc_report_status=status)
        records.append(record)
    latest = None
    for point in chart:
        try:
            point_date = date.fromisoformat(point['Date'][:10]).isoformat()
            count = int(point['Affected pages'])
            if count < 0:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError('Ongeldige datum of telling in het GSC-diagram.') from None
        if latest is None or point_date > latest['date']:
            latest = {'date': point_date, 'count': count}
    summary = {'type': 'gsc_page_indexing', 'file_format': file_format, 'report_status': status,
               'indexing_reason': reason, 'reason_from_metadata': bool(reason),
               'chart': chart, 'metadata': metadata, 'columns': headers,
               'reported_total': latest['count'] if latest else None,
               'report_date': latest['date'] if latest else None,
               'unknown_crawl_dates': unknown_crawl_dates, 'invalid_url_rows': invalid_urls}
    return {'source': 'gsc', 'label': 'Google Search Console · ' + (reason or 'Page indexing'),
            'parser_version': 'gsc-coverage-v2', 'rows': records,
            'checksum': checksum, 'measured_at': None, 'summary': summary}


def parse_gsc_zip(raw, filename=''):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > 20 or sum(e.file_size for e in entries) > MAX_BYTES:
                raise ValueError('ZIP bevat te veel bestanden of uitgepakte data (maximaal 20 MB).')
            tables = {}
            for entry in entries:
                if entry.is_dir():
                    continue
                path = PurePosixPath(entry.filename)
                kind = SHEET_NAMES.get(path.stem.lower()) if path.suffix.lower() == '.csv' else None
                if kind is None:
                    continue
                if kind in tables or entry.flag_bits & 1:
                    raise ValueError('ZIP bevat dubbele rapporten of versleutelde bestanden.')
                reader = csv.DictReader(io.StringIO(archive.read(entry).decode('utf-8-sig'), newline=''), strict=True)
                headers = reader.fieldnames or []
                rows = list(reader)
                if len(rows) > MAX_ROWS or any(None in row or any(v is None for v in row.values()) for row in rows):
                    raise ValueError('Ongeldige CSV-rijen in de ZIP.')
                tables[kind] = (headers, rows)
            if not tables:
                raise ValueError('GSC-export niet herkend. Upload de originele GSC CSV-ZIP-export.')
            return parse_gsc_tables(tables, checksum=hashlib.sha256(raw).hexdigest(), filename=filename)
    except (zipfile.BadZipFile, UnicodeError, csv.Error, KeyError, RuntimeError, NotImplementedError, OverflowError):
        raise ValueError('ZIP kan niet worden gelezen. Upload de originele GSC CSV-ZIP-export.') from None


def parse_gsc_csv(raw, filename=''):
    """Recognize the standalone URL table from a GSC Page indexing export."""
    try:
        encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
        text = raw.decode(encoding)
        first = text.partition('\n')[0]
        delimiter = max((',', ';', '\t'), key=first.count)
        reader = csv.DictReader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True)
        headers = reader.fieldnames or []
        normalized_headers = {ALIASES.get(header, header) for header in headers}
        if not {'URL', 'Last crawled'}.issubset(normalized_headers):
            return None
        rows = []
        for line, row in enumerate(reader, 2):
            if len(rows) >= MAX_ROWS:
                raise ValueError('Maximaal 100.000 URL-rijen per import.')
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f'Regel {line}: het aantal kolommen klopt niet.')
            rows.append(row)
        tables = {'table': (headers, rows), 'chart': (['Date', 'Affected pages'], []),
                  'metadata': (['Property', 'Value'], [])}
        result = parse_gsc_tables(tables, checksum=hashlib.sha256(raw).hexdigest(),
                                  filename=filename, file_format='csv')
        result['summary']['missing_report_context'] = True
        return result
    except UnicodeError:
        raise ValueError('Gebruik een CSV-bestand met UTF-8- of UTF-16-codering.') from None
    except csv.Error:
        raise ValueError('Ongeldige GSC-CSV. Exporteer de URL-tabel opnieuw.') from None
