"""Read the URL examples and context in an English GSC Coverage ZIP, without extraction."""
import csv
import hashlib
import io
from pathlib import PurePosixPath
import zipfile
from datetime import date
from .screamingfrog import MAX_BYTES, MAX_ROWS
from integrations.url_validation import normalize_url


def parse_gsc_zip(raw, filename=''):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > 20 or sum(e.file_size for e in entries) > MAX_BYTES:
                raise ValueError('ZIP bevat te veel bestanden of uitgepakte data (maximaal 20 MB).')
            files = {}
            for entry in entries:
                if entry.is_dir():
                    continue
                name = PurePosixPath(entry.filename).name
                if name in files or entry.flag_bits & 1:
                    raise ValueError('ZIP bevat dubbele namen of versleutelde bestanden.')
                files[name] = entry
            if not {'Table.csv', 'Chart.csv', 'Metadata.csv'}.issubset(files):
                raise ValueError('GSC-export niet herkend. Upload de originele ZIP van het Page indexing URL-detailrapport.')

            def read(name):
                data = archive.read(files[name])
                reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig'), newline=''), strict=True)
                headers = reader.fieldnames or []
                if len(headers) != len(set(headers)):
                    raise ValueError('Dubbele CSV-kolommen in de ZIP.')
                rows = list(reader)
                if len(rows) > MAX_ROWS or any(None in row or any(v is None for v in row.values()) for row in rows):
                    raise ValueError('Ongeldige CSV-rijen in de ZIP.')
                return headers, rows

            headers, table = read('Table.csv')
            chart_headers, chart = read('Chart.csv')
            meta_headers, metadata = read('Metadata.csv')
            if not {'URL', 'Last crawled'}.issubset(headers) or not {'Date', 'Affected pages'}.issubset(chart_headers) or not {'Property', 'Value'}.issubset(meta_headers):
                raise ValueError('GSC-exporttype niet herkend. Verwacht een Page indexing URL-detailrapport.')
            if not table:
                raise ValueError('Het bestand bevat geen URLs.')
            seen, records = set(), []
            for number, row in enumerate(table, 2):
                if not normalize_url(row['URL']):
                    raise ValueError(f'Table.csv regel {number}: ongeldig URL-adres.')
                if row['URL'] in seen:
                    raise ValueError(f'Table.csv regel {number}: dubbel URL-adres.')
                seen.add(row['URL'])
                if row['Last crawled']:
                    date.fromisoformat(row['Last crawled'])
                records.append(row)
            latest = None
            for point in chart:
                date.fromisoformat(point['Date'])
                count = int(point['Affected pages'])
                if count < 0:
                    raise ValueError('Ongeldig aantal in Chart.csv.')
                if latest is None or point['Date'] > latest['Date']:
                    latest = point
            # Filename is only a hint. The preview requires confirmation of the category.
            hint = 'indexed' if '-coverage-valid-' in filename.lower() else 'unknown'
            return {'source': 'gsc', 'label': 'Google Search Console · Page indexing',
                    'parser_version': 'gsc-coverage-zip-v1', 'rows': records,
                    'checksum': hashlib.sha256(raw).hexdigest(), 'measured_at': None,
                    'summary': {'type': 'gsc_page_indexing', 'report_status': hint,
                                'chart': chart, 'metadata': metadata, 'columns': headers,
                                'reported_total': int(latest['Affected pages']) if latest else None,
                                'report_date': latest['Date'] if latest else None}}
    except (zipfile.BadZipFile, UnicodeError, csv.Error, KeyError, RuntimeError, NotImplementedError, OverflowError):
        raise ValueError('ZIP kan niet worden gelezen. Upload de originele GSC CSV-ZIP-export.') from None
