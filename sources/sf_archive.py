"""Read URL snapshots from Screaming Frog's SQLite-backed crawl archives.

Only fixed scalar URL fields are read. Serialized Java objects, page bodies,
credentials in spiderconfig, and executable content are never loaded.
"""
import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
import zipfile
from collections import Counter
from datetime import datetime, timezone as utc_timezone
from pathlib import Path
from urllib.parse import urlsplit

from django.utils import timezone
from .screamingfrog import MAX_BYTES, MAX_ROWS
from .sf_reports import REPORTS, valid_url

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_FILES = 2000
URL_COLUMNS = {
    'encoded_url': 'Address', 'response_msg': 'Status', 'response_code': 'Status Code',
    'content_type': 'Content Type', 'crawl_depth': 'Crawl Depth',
    'md5sum': 'Hash', 'page_size': 'Size (bytes)', 'last_modified_date': 'Last Modified',
    'word_count': 'Word Count', 'sentence_count': 'Sentence Count',
    'avg_words_per_sentence': 'Average Words Per Sentence',
    'readability_score': 'Flesch Reading Ease Score', 'text_to_html_ratio': 'Text Ratio',
    'num_internal_outlinks': 'Outlinks', 'num_external_outlinks': 'External Outlinks',
    'num_unique_internal_outlinks': 'Unique Outlinks',
    'num_unique_external_outlinks': 'Unique External Outlinks',
    'title_1': 'Title 1', 'title_2': 'Title 2', 'h1_1': 'H1-1', 'h1_2': 'H1-2',
    'h2_1': 'H2-1', 'h2_2': 'H2-2', 'lang_code': 'Language',
    'page_transfer_size': 'Transferred (bytes)', 'total_page_transfer_size': 'Total Transferred (bytes)',
    'co2': 'CO2 (mg)', 'blocked_by_robots_txt': 'Blocked by robots.txt',
    'is_redirect': 'Is Redirect', 'is_canonicalised': 'Is Canonicalised',
}


def is_crawl_archive(names):
    return 'DbSeoSpiderFileKey' in names and any(
        name.startswith('results_') and name.endswith('/sql') for name in names)


def parse_archive(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_FILES or sum(e.file_size for e in entries) > MAX_ARCHIVE_BYTES:
                raise ValueError('Crawlarchief bevat te veel bestanden of uitgepakte data (maximaal 128 MB).')
            names = archive.namelist()
            databases = [n for n in names if n.startswith('results_') and n.endswith('/sql')]
            if not is_crawl_archive(names) or len(databases) != 1 or len(names) != len(set(names)):
                raise ValueError('Crawlarchief niet herkend. Verwacht één Screaming Frog SQLite-crawl.')
            # WAL contents must be checkpointed by SEO Spider before exporting.
            if any(e.filename.endswith('/sql-wal') and e.file_size for e in entries):
                raise ValueError('Crawlarchief bevat een actieve database. Sla de crawl opnieuw op vanuit Screaming Frog.')
            with tempfile.TemporaryDirectory(prefix='sem-sf-') as folder:
                database = Path(folder) / 'crawl.sqlite'
                with archive.open(databases[0]) as source, database.open('wb') as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                if database.stat().st_size > MAX_ARCHIVE_BYTES:
                    raise ValueError('Crawldatabase is te groot.')
                return read_database(database, hashlib.sha256(raw).hexdigest())
    except ValueError:
        raise
    except (zipfile.BadZipFile, sqlite3.Error, OSError, RuntimeError, OverflowError, TypeError, UnicodeDecodeError) as exc:
        raise ValueError('Crawlarchief kan niet worden gelezen. Exporteer opnieuw vanuit Screaming Frog.') from exc


def read_database(path, checksum):
    connection = sqlite3.connect(f'{path.as_uri()}?mode=ro&immutable=1', uri=True)
    try:
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('PRAGMA query_only=ON')
        # Bound query work even for malformed or unusually complex databases.
        steps = 0
        def progress():
            nonlocal steps
            steps += 1
            return steps > 20000
        connection.set_progress_handler(progress, 1000)
        table = connection.execute("SELECT type FROM sqlite_master WHERE name='urls'").fetchone()
        if table != ('table',):
            raise ValueError('Crawldatabase mist de verwachte urls-tabel.')
        fields = {row[1] for row in connection.execute('PRAGMA table_info(urls)')}
        if not {'encoded_url', 'is_internal', 'response_code', 'content_type', 'timestamp'} <= fields:
            raise ValueError('Deze versie van de Screaming Frog-crawldatabase wordt niet ondersteund.')
        columns = [key for key in URL_COLUMNS if key in fields]
        columns += [key for key in ('timestamp', 'is_internal', 'response_time_ms') if key in fields]
        connection.row_factory = sqlite3.Row
        query = 'SELECT ' + ','.join('"' + key + '"' for key in columns) + ' FROM urls LIMIT ?'
        rows, timestamps, seen, size = [], [], set(), 0
        for source in connection.execute(query, (MAX_ROWS + 1,)):
            if len(rows) >= MAX_ROWS:
                raise ValueError('Maximaal 100.000 URLs per crawlarchief.')
            row = {label: '' if source[key] is None else str(source[key]) for key, label in URL_COLUMNS.items() if key in columns}
            if not valid_url(row['Address']) or row['Address'] in seen:
                raise ValueError('Crawldatabase bevat een ongeldig of dubbel URL-adres.')
            seen.add(row['Address'])
            row['Internal'] = 'true' if source['is_internal'] else 'false'
            if 'response_time_ms' in columns:
                row['Response Time'] = str(source['response_time_ms'] / 1000) if source['response_time_ms'] is not None else ''
            stamp = source['timestamp']
            row['Crawl Timestamp'] = ''
            if stamp:
                dt = datetime.fromtimestamp(stamp / 1000, utc_timezone.utc)
                row['Crawl Timestamp'] = dt.isoformat()
                timestamps.append(dt)
            size += len(json.dumps(row, ensure_ascii=False).encode('utf-8'))
            if size > MAX_BYTES:
                raise ValueError('Crawlarchief bevat te veel URL-data (maximaal 20 MB).')
            rows.append(row)
        if not rows:
            raise ValueError('Crawlarchief bevat geen URLs.')
        label, scope = REPORTS['crawl_archive']
        return {'rows': rows, 'checksum': checksum, 'source': 'sf',
                'label': 'Screaming Frog · ' + label, 'parser_version': 'sf-sqlite-archive-v1',
                'measured_at': min(timestamps) if timestamps else None,
                'summary': {'type': 'crawl_archive', 'report_label': label, 'record_scope': scope,
                            'columns': list(rows[0]), 'original_columns': columns, 'file_format': 'dbseospider.zip',
                            'timezone': str(timezone.get_current_timezone()),
                            'crawl_start': min(timestamps).isoformat() if timestamps else None,
                            'crawl_end': max(timestamps).isoformat() if timestamps else None,
                            'hosts': dict(Counter(urlsplit(row['Address']).hostname for row in rows)),
                            'html_count': sum(row['Content Type'].lower().startswith(('text/html', 'application/xhtml+xml')) for row in rows),
                            'archive_snapshot': True}}
    finally:
        connection.close()
