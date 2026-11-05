"""Bounded, independent live telemetry. No URLs, bodies, credentials or database locks.

Each writer uses its own short WAL transaction; readers never create a file.
Telemetry survives crawl publication and can be read during analysis transactions.
"""
import json
import logging
import math
import re
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from django.conf import settings

WINDOW=300
PHASES={'pages':'Pagina’s bezoeken','files':'Bestanden controleren','external':'Externe links controleren',
        'analysis':'Paginaresultaten analyseren','browser':'Browseronderzoek','browser_analysis':'Browserresultaten analyseren','enrichment':'Technologie & privacy onderzoeken'}
SCHEMA='''CREATE TABLE IF NOT EXISTS phases(name TEXT PRIMARY KEY,status TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0,total INTEGER,started REAL,finished REAL,updated REAL,baseline INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,phase TEXT NOT NULL,host TEXT NOT NULL,started REAL NOT NULL,state TEXT NOT NULL,due REAL);
CREATE TABLE IF NOT EXISTS series(bucket INTEGER,phase TEXT,status TEXT,outcome TEXT,n INTEGER NOT NULL,PRIMARY KEY(bucket,phase,status,outcome));
CREATE TABLE IF NOT EXISTS totals(phase TEXT,status TEXT,outcome TEXT,n INTEGER NOT NULL,PRIMARY KEY(phase,status,outcome));'''


def paths(batch_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',str(batch_id)):raise ValueError('Invalid telemetry identifier')
    base='scan-activity/'+str(batch_id)+'.sqlite3'
    return [base,base+'-wal',base+'-shm']


def write(batch_id,operation,*,create=True):
    if not batch_id:return
    connection=None
    try:
        path=Path(settings.MEDIA_ROOT)/paths(batch_id)[0]
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        if not create and not path.exists():return
        connection=sqlite3.connect(path if create else path.as_uri()+'?mode=rw',timeout=.2,uri=not create)
        path.chmod(0o600)
        connection.execute('PRAGMA journal_mode=WAL')
        connection.executescript(SCHEMA)
        if 'baseline' not in {r[1] for r in connection.execute('PRAGMA table_info(phases)')}:connection.execute('ALTER TABLE phases ADD COLUMN baseline INTEGER NOT NULL DEFAULT 0')
        with connection:operation(connection)
    except (sqlite3.Error,OSError,ValueError):
        logging.getLogger(__name__).warning('Live progress temporarily unavailable',exc_info=False)
    finally:
        if connection:connection.close()


def phase(batch_id,name,*,status='running',done=None,total=None):
    if name not in PHASES:return
    now=time.time()
    def update(c):
        c.execute('INSERT OR IGNORE INTO phases(name,status,started,updated,baseline) VALUES (?,?,?,?,?)',(name,status,now,now,max(0,int(done or 0))))
        c.execute("UPDATE phases SET started=? WHERE name=? AND status='queued' AND ?='running'",(now,name,status))
        c.execute('UPDATE phases SET status=?,updated=?,finished=? WHERE name=?',(status,now,now if status in ('complete','partial','failed','skipped') else None,name))
        if done is not None:c.execute('UPDATE phases SET done=? WHERE name=?',(max(0,int(done)),name))
        if total is not None:c.execute('UPDATE phases SET total=? WHERE name=?',(max(0,int(total)),name))
        if status in ('complete','partial','failed','skipped'):c.execute('DELETE FROM requests WHERE phase=?',(name,))
    write(batch_id,update)


def outcome(status=None,error=None):
    if error:return 'unknown'
    if status in (401,403,429,451):return 'blocked'
    if isinstance(status,int) and 200<=status<400:return 'success'
    if isinstance(status,int) and 400<=status<600:return 'http_error'
    return 'unknown'


def start(batch_id,name,url):
    if not batch_id:return None
    identifier=uuid.uuid4().hex;now=time.time();host=(urlsplit(url).hostname or '')[:253]
    def update(c):
        c.execute('INSERT OR IGNORE INTO phases(name,status,started,updated) VALUES (?,\'running\',?,?)',(name,now,now))
        c.execute("DELETE FROM requests WHERE phase=? AND host=? AND state='retrying'",(name,host))
        c.execute('INSERT INTO requests VALUES (?,?,?,?,?,NULL)',(identifier,name,host,now,'request'))
        c.execute("UPDATE phases SET status='running',updated=?,finished=NULL WHERE name=?",(now,name))
        c.execute('DELETE FROM requests WHERE started<?',(now-7200,))
    write(batch_id,update);return identifier


def finish(batch_id,name,identifier,*,status=None,error=None,outcome_override=None):
    if not batch_id or not identifier:return
    state=outcome_override if outcome_override in ('blocked','unknown') else outcome(status,error);now=time.time();bucket=int(now)
    code='blocked' if outcome_override=='blocked' else str(status) if isinstance(status,int) and 100<=status<=999 else 'download_error' if error else 'unknown'
    def update(c):
        c.execute('DELETE FROM requests WHERE id=?',(identifier,))
        for table in ('series','totals'):
            if table=='series':
                c.execute('INSERT INTO series VALUES (?,?,?,?,1) ON CONFLICT(bucket,phase,status,outcome) DO UPDATE SET n=n+1',(bucket,name,code,state))
            else:c.execute('INSERT INTO totals VALUES (?,?,?,1) ON CONFLICT(phase,status,outcome) DO UPDATE SET n=n+1',(name,code,state))
        c.execute('UPDATE phases SET updated=? WHERE name=?',(now,name))
        c.execute('DELETE FROM series WHERE bucket<?',(bucket-WINDOW,))
    write(batch_id,update,create=False)


def retry(batch_id,name,url,due):
    now=time.time();host=(urlsplit(url).hostname or '')[:253]
    def update(c):
        c.execute('INSERT OR REPLACE INTO requests VALUES (?,?,?,?,?,?)',('retry:'+host,name,host,now,'retrying',due))
        c.execute("INSERT INTO series VALUES (?,?,?,'retrying',1) ON CONFLICT(bucket,phase,status,outcome) DO UPDATE SET n=n+1",(int(now),name,'retrying'))
        c.execute("INSERT INTO totals VALUES (?,?,'retrying',1) ON CONFLICT(phase,status,outcome) DO UPDATE SET n=n+1",(name,'retrying'))
        c.execute('UPDATE phases SET updated=? WHERE name=?',(now,name))
    write(batch_id,update)


def read(batch_id,now=None):
    now=time.time() if now is None else now
    path=Path(settings.MEDIA_ROOT)/paths(batch_id)[0]
    if not path.exists():return None
    c=None
    try:
        c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.2);c.row_factory=sqlite3.Row
        c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
        phases=[]
        for row in c.execute('SELECT * FROM phases'):
            item=dict(row);item['label']=PHASES.get(item['name'],item['name'])
            elapsed=max(0,(item['finished'] or now)-(item['started'] or now))
            observed=max(0,item['done']-item.get('baseline',0))
            rate=observed/elapsed if elapsed and observed else None
            pending=max(0,item['total']-item['done']) if item['total'] is not None else None
            item.update(elapsed_seconds=int(elapsed),units_per_second=rate,pending=pending,
                eta_seconds=math.ceil(pending/rate) if rate and pending is not None and item['status']=='running' else None)
            phases.append(item)
        requests=[{**dict(row),'waiting_seconds':int(max(0,now-row['started'])),'retry_in_seconds':max(0,math.ceil(row['due']-now)) if row['due'] else None} for row in c.execute("SELECT * FROM requests ORDER BY CASE WHEN state='request' THEN 0 ELSE 1 END,started LIMIT 20")]
        series=[dict(row) for row in c.execute('SELECT * FROM series WHERE bucket>=? ORDER BY bucket DESC,phase,status,outcome LIMIT 10000',(int(now)-WINDOW,))]
        totals=[dict(row) for row in c.execute('SELECT * FROM totals ORDER BY phase,status,outcome')]
        return {'phases':phases,'requests':requests,'series':series,'totals':totals,'interval_seconds':1,
            'updated_at':max((p['updated'] or 0 for p in phases),default=None),'end':int(now),'window_seconds':WINDOW}
    except (sqlite3.Error,OSError,ValueError):return None
    finally:
        if c:c.close()
