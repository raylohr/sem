"""Private disk-backed scan checkpoints, shared only through the media volume."""
import datetime
import fcntl
import gzip
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime


class ScanPause(Exception):
    pass


class ScanBusy(Exception):
    pass


def work_paths(batch_id):
    base = f'scan-work/{batch_id}'
    return [base + suffix for suffix in ('.sqlite3', '.sqlite3-wal', '.sqlite3-shm', '.lock')]


def stale_scan(batch):
    if batch.status != 'processing' or not batch.scan_target_id:
        return False
    value = batch.request_metadata.get('scan_progress', {}).get('heartbeat')
    stamp = parse_datetime(value) if isinstance(value, str) else None
    return bool(stamp and timezone.is_aware(stamp) and timezone.now() - stamp > datetime.timedelta(minutes=15))


class DiskRows:
    def __init__(self, work, table):
        self.work, self.table = work, table
        self.count = work.connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0]

    def __len__(self):
        return self.count

    def append(self, value):
        with self.work.connection:
            if self.table == 'records':
                self.work.connection.execute('INSERT INTO records(payload) VALUES (?)', (json.dumps(value, ensure_ascii=False),))
            else:
                meta = {key: item.isoformat() if key == 'fetched_at' else item for key, item in value.items() if key != 'body'}
                self.work.connection.execute('INSERT INTO documents(metadata,body) VALUES (?,?)',
                    (json.dumps(meta), gzip.compress(value['body'], compresslevel=1)))
        self.count += 1

    def __iter__(self):
        if self.table == 'records':
            cursor = self.work.connection.execute('SELECT payload FROM records ORDER BY id')
            for row in cursor:
                yield json.loads(row[0])
        else:
            for metadata, body in self.work.connection.execute('SELECT metadata,body FROM documents ORDER BY id'):
                value = json.loads(metadata)
                value['fetched_at'] = parse_datetime(value['fetched_at'])
                value['body'] = gzip.decompress(body)
                yield value


class ScanWork:
    def __init__(self, batch_id):
        self.paths = [Path(settings.MEDIA_ROOT) / path for path in work_paths(batch_id)]
        self.clean = False

    def __enter__(self):
        self.paths[0].parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.paths[-1], os.O_CREAT | os.O_RDWR, 0o600)
        self.lock = os.fdopen(descriptor, 'a+b')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise ScanBusy
        try:
            self.connection = sqlite3.connect(self.paths[0])
            self.paths[0].chmod(0o600)
            self.connection.execute('PRAGMA journal_mode=WAL')
            self.connection.execute('PRAGMA synchronous=NORMAL')
            self.connection.execute('PRAGMA cache_size=-2048')
            self.connection.executescript('''
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS frontier (id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
                    url TEXT NOT NULL, depth INTEGER NOT NULL, done INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(kind,url));
                CREATE INDEX IF NOT EXISTS next_frontier ON frontier(kind,done,id);
                CREATE TABLE IF NOT EXISTS sitemap_urls (url TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, metadata TEXT NOT NULL, body BLOB NOT NULL);
            ''')
            self.records, self.documents = DiskRows(self, 'records'), DiskRows(self, 'documents')
            self.started = self.get('started', time.time())
            self.set('started', self.started)
            return self
        except Exception:
            self.lock.close()
            raise

    def __exit__(self, *args):
        self.connection.close()
        if self.clean:
            for path in self.paths[:-1]:
                path.unlink(missing_ok=True)
            self.paths[-1].unlink(missing_ok=True)
        self.lock.close()

    def get(self, key, default=None):
        row = self.connection.execute('SELECT payload FROM metadata WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connection:
            self.connection.execute('INSERT OR REPLACE INTO metadata(key,payload) VALUES (?,?)', (key,json.dumps(value)))

    def add(self, kind, url, depth):
        with self.connection:
            cursor = self.connection.execute('INSERT OR IGNORE INTO frontier(kind,url,depth) VALUES (?,?,?)', (kind,url,depth))
            return bool(cursor.rowcount)

    def next(self, kind):
        return self.connection.execute('SELECT id,url,depth FROM frontier WHERE kind=? AND done=0 ORDER BY id LIMIT 1', (kind,)).fetchone()

    def finish(self, item_id, record=None):
        # Record and frontier advancement are one checkpoint: retries cannot duplicate pages.
        with self.connection:
            if record is not None:
                self.connection.execute('INSERT INTO records(payload) VALUES (?)', (json.dumps(record,ensure_ascii=False),))
            self.connection.execute('UPDATE frontier SET done=1 WHERE id=?', (item_id,))
        if record is not None:
            self.records.count += 1

    def count(self, kind, done=None):
        clause = ' AND done=?' if done is not None else ''
        args = (kind,done) if done is not None else (kind,)
        return self.connection.execute('SELECT count(*) FROM frontier WHERE kind=?'+clause,args).fetchone()[0]

    def sitemap_record(self, url, record):
        with self.connection:
            inserted = self.connection.execute('INSERT OR IGNORE INTO sitemap_urls(url) VALUES (?)', (url,)).rowcount
            if inserted:
                self.connection.execute('INSERT INTO records(payload) VALUES (?)', (json.dumps(record,ensure_ascii=False),))
        self.records.count += bool(inserted)

    def check_disk(self):
        if shutil.disk_usage(self.paths[0].parent).free < 1024**3:
            from .network import ScanBudgetExceeded
            raise ScanBudgetExceeded('Minder dan 1 GiB vrije schijfruimte; scan voortijdig gestopt.')
