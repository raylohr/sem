"""Small transactional rollups; web requests never scan raw crawl bodies."""
import json
import math
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from django.conf import settings
from django.utils.dateparse import parse_datetime
from .work import work_paths

INTERVAL = 3
WINDOW = 300


def counter(connection, key, amount=1):
    connection.execute('INSERT INTO live_counts(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=value+excluded.value',(key,amount))
    connection.execute("INSERT OR REPLACE INTO live_counts(key,value) VALUES ('updated_at',?)",(time.time(),))


def point(connection, status, stamp):
    if isinstance(stamp,str): stamp=parse_datetime(stamp)
    seconds=stamp.timestamp() if isinstance(stamp,datetime) else float(stamp or time.time())
    bucket=int(seconds//INTERVAL)*INTERVAL
    connection.execute('INSERT INTO live_series(bucket,status,n) VALUES (?,?,1) ON CONFLICT(bucket,status) DO UPDATE SET n=n+1',(bucket,status))
    connection.execute('INSERT INTO live_totals(status,n) VALUES (?,1) ON CONFLICT(status) DO UPDATE SET n=n+1',(status,))
    connection.execute('DELETE FROM live_series WHERE bucket<?',(int(time.time())-WINDOW-INTERVAL,))


def record(connection, value):
    counter(connection,'records')
    data=value.get('data',{})
    if data.get('error') and not data.get('robots_blocked'):
        counter(connection,'errors')
        point(connection,-1,data.get('measured_at'))


def document(connection, metadata):
    counter(connection,'documents')
    point(connection,int(metadata['status']),metadata['fetched_at'])


def initialize(work):
    c=work.connection
    c.executescript('''CREATE TABLE IF NOT EXISTS live_counts(key TEXT PRIMARY KEY,value REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS live_series(bucket INTEGER,status INTEGER,n INTEGER NOT NULL,PRIMARY KEY(bucket,status));
        CREATE TABLE IF NOT EXISTS live_totals(status INTEGER PRIMARY KEY,n INTEGER NOT NULL);''')
    if c.execute("SELECT 1 FROM live_counts WHERE key='initialized'").fetchone(): return
    with c:
        # Upgrade an existing checkpoint once, without reading document bodies.
        for (metadata,) in c.execute('SELECT metadata FROM documents'):
            document(c,json.loads(metadata))
        for (payload,) in c.execute("SELECT payload FROM records WHERE json_extract(payload,'$.data.error') IS NOT NULL"):
            record(c,json.loads(payload))
        for key,value in [('records',len(work.records)),('documents',len(work.documents)),
                          ('pending',c.execute('SELECT count(*) FROM frontier WHERE done=0').fetchone()[0]),('initialized',1)]:
            c.execute('INSERT OR REPLACE INTO live_counts(key,value) VALUES (?,?)',(key,value))


def snapshot(connection, end=None):
    end=int(end or time.time())//INTERVAL*INTERVAL
    start=end-WINDOW
    stats=dict(connection.execute('SELECT key,value FROM live_counts'))
    series=[{'time':bucket,'status':str(status) if status!=-1 else 'download_error','count':n}
            for bucket,status,n in connection.execute('SELECT bucket,status,n FROM live_series WHERE bucket>=? AND bucket<=? ORDER BY bucket,status',(start,end))]
    totals={str(status) if status!=-1 else 'download_error':n for status,n in connection.execute('SELECT status,n FROM live_totals')}
    row=connection.execute("SELECT payload FROM metadata WHERE key='started'").fetchone()
    started_at=json.loads(row[0]) if row else None
    return {'started_at':started_at,'records':int(stats.get('records',0)),'documents':int(stats.get('documents',0)),
            'pending':int(stats.get('pending',0)),'errors':int(stats.get('errors',0)),
            'updated_at':stats.get('updated_at'),'interval_seconds':INTERVAL,
            'window_seconds':WINDOW,'end':end,'series':series,'totals':totals}


def read_checkpoint(batch_id):
    path=Path(settings.MEDIA_ROOT)/work_paths(batch_id)[0]
    if not path.exists(): return None
    try:
        c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.2)
        try:
            c.execute('PRAGMA query_only=ON')
            c.execute('BEGIN')
            return snapshot(c)
        finally: c.close()
    except sqlite3.Error:
        # Initial creation, publication/cleanup and transient locks are normal.
        return None


def estimate_remaining(*, records, pending, started_at, now, active, max_urls=None):
    """Mean throughput since actual worker start, for the currently known queue."""
    if not isinstance(started_at,(int,float)) or not math.isfinite(started_at):
        return {'elapsed_seconds':None,'urls_per_second':None,'eta_seconds':None}
    elapsed=max(0,now-started_at)
    rate=records/elapsed if elapsed>0 and records>0 else None
    remaining=max(0,pending)
    if isinstance(max_urls,int) and max_urls>0:
        remaining=min(remaining,max(0,max_urls-records))
    eta=math.ceil(remaining/rate) if active and rate else None
    return {'elapsed_seconds':int(elapsed),'urls_per_second':rate,'eta_seconds':eta}
