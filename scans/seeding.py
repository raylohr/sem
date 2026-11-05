"""Resumable sitemap seeds and URL provenance, separate from internal-link depth."""
import gzip
import io
import json
import zlib
from urllib.parse import urlsplit, urlunsplit, urljoin
from .resource_discovery import known_file
from .network import normalize_url, ScanError, ScanBudgetExceeded, MAX_BODY

MAX_SEED_MAPS = 100
MAX_SEED_MAP_DEPTH = 10


def initialize(work):
    work.connection.executescript('''
        CREATE TABLE IF NOT EXISTS crawl_origins (
            url TEXT PRIMARY KEY, start INTEGER NOT NULL DEFAULT 0,
            sitemap INTEGER NOT NULL DEFAULT 0, link INTEGER NOT NULL DEFAULT 0,
            sitemap_url TEXT);
        CREATE TABLE IF NOT EXISTS crawl_edges (
            source TEXT NOT NULL,target TEXT NOT NULL,PRIMARY KEY(source,target));
        CREATE TABLE IF NOT EXISTS crawl_aliases (url TEXT PRIMARY KEY,final_url TEXT NOT NULL);
    ''')


def origin(work, url, kind, sitemap_url=None):
    # Column names are application constants, never supplied by URLs.
    assert kind in ('start','sitemap','link')
    with work.connection:
        work.connection.execute(f'''INSERT INTO crawl_origins(url,{kind},sitemap_url) VALUES (?,1,?)
            ON CONFLICT(url) DO UPDATE SET {kind}=1,
            sitemap_url=coalesce(crawl_origins.sitemap_url,excluded.sitemap_url)''',(url,sitemap_url))


def links(work, client, source, final, values, max_urls):
    with work.connection:
        work.connection.execute('INSERT OR REPLACE INTO crawl_aliases VALUES (?,?)',(source,final))
    if work.connection.execute('SELECT 1 FROM crawl_origins WHERE url=? AND start=1',(source,)).fetchone():
        origin(work,final,'start')
    edges=work.get('provenance_edges',0)
    nodes=work.get('provenance_link_nodes',0)
    for value in values:
        try:value=normalize_url(value,client.allowed_hosts)
        except ScanError:continue
        exists=work.connection.execute('SELECT 1 FROM crawl_origins WHERE url=?',(value,)).fetchone()
        if not exists:
            if nodes>=max_urls*5:
                work.set('provenance_truncated',True);continue
            nodes+=1
        origin(work,value,'link')
        if edges<1000000:
            with work.connection:
                edges+=work.connection.execute('INSERT OR IGNORE INTO crawl_edges VALUES (?,?)',(final,value)).rowcount
        else:work.set('provenance_truncated',True)
    work.set('provenance_edges',edges)
    work.set('provenance_link_nodes',nodes)


def discover(client, work, start_url, max_urls):
    from .large import add_warning
    from .runners import parse_sitemap
    def admit(value,source):
        try:return normalize_url(value,client.allowed_hosts)
        except ScanError:
            discovery=getattr(client,'host_discovery',None)
            if discovery:discovery.observe([value],source)
            add_warning(work,'Sitemapverwijzing buiten de toegestane hosts overgeslagen; hostkeuzes blijven gelden.')
            return None

    if work.get('seed_maps_done'):return
    if not work.get('seed_maps_initialized'):
        first=work.connection.execute('SELECT payload FROM records ORDER BY id LIMIT 1').fetchone()
        first_data=json.loads(first[0]).get('data',{}) if first else {}
        effective=first_data.get('final_url') or start_url
        parts=urlsplit(effective)
        base=urlunsplit((parts.scheme,parts.netloc,'/','',''))
        robot_url=urljoin(base,'robots.txt')
        cache=getattr(client,'robot_cache',{})
        cached=cache.get(base.rstrip('/')) if isinstance(cache,dict) else None
        declared=[]
        try:
            if cached:
                body=cached['body'] if cached['status']==200 else ''
            else:
                response=client.fetch(robot_url,enforce_robots=False)
                body=response.body.decode('utf-8','replace') if response.status==200 and not response.blocked_redirect else ''
                if response.blocked_redirect and getattr(response,'blocked_destination',''):
                    admit(response.blocked_destination,robot_url)
            for line in body.splitlines():
                key,separator,value=line.partition(':')
                if separator and key.strip().lower()=='sitemap':
                    value=admit(value.strip(),robot_url)
                    if value and value not in declared:declared.append(value)
                    if len(declared)>=MAX_SEED_MAPS:break
        except ScanBudgetExceeded:raise
        except ScanError:
            add_warning(work,'robots.txt kon niet worden gelezen voor sitemapontdekking; gebruikelijke sitemaplocaties worden geprobeerd.')
        candidates=declared or [urljoin(base,path) for path in ('sitemap.xml','sitemap_index.xml','wp-sitemap.xml')]
        for value in candidates:work.add('crawl_sitemap',value,0)
        work.set('seed_maps_initialized',True)
        work.set('seed_maps_explicit',bool(declared))
    while (item:=work.next('crawl_sitemap')):
        client.guard()
        work.check_disk()
        item_id,current,depth=item
        try:
            result=client.fetch(current)
            if result.blocked_redirect:
                if result.blocked_destination:admit(result.blocked_destination,current)
                raise ScanError('Sitemapredirect buiten de toegestane scope.')
            if result.status!=200:
                if work.get('seed_maps_explicit') or depth:
                    add_warning(work,f'Sitemap niet opgehaald: {current} (HTTP {result.status}).')
                work.finish(item_id);continue
            body=result.body
            if body.startswith(b'\x1f\x8b'):
                with gzip.GzipFile(fileobj=io.BytesIO(body)) as archive:body=archive.read(MAX_BODY+1)
                if len(body)>MAX_BODY:raise ScanError('Uitgepakte sitemap is te groot.')
            kind,entries=parse_sitemap(body)
            if not work.get(f'seed_map_found:{item_id}'):
                work.set('seed_maps_found',work.get('seed_maps_found',0)+1)
                work.set(f'seed_map_found:{item_id}',True)
            count=work.count('crawl')
            for position,entry in enumerate(entries):
                if position%100==0:client.guard()
                value=admit(entry['loc'],result.url)
                if not value:continue
                if kind=='sitemapindex':
                    if work.connection.execute("SELECT 1 FROM frontier WHERE kind='crawl_sitemap' AND url=?",(value,)).fetchone():continue
                    if depth<MAX_SEED_MAP_DEPTH and work.count('crawl_sitemap')<MAX_SEED_MAPS:
                        work.add('crawl_sitemap',value,depth+1)
                    else:
                        work.set('seed_maps_truncated',True)
                elif getattr(client,'separate_resources',False) is True and known_file(value):
                    from .resources import initialize as resource_initialize,add as add_resource
                    if not hasattr(work,'resource_target_count'):resource_initialize(work)
                    with work.connection:add_resource(work,value,'resource',{'source_url':result.url,'kind':'download','reference_type':'sitemap','raw_url':entry['loc']})
                    origin(work,value,'sitemap',result.url)
                elif count<max_urls*5 or work.connection.execute("SELECT 1 FROM frontier WHERE kind='crawl' AND url=?",(value,)).fetchone():
                    count+=work.add('crawl',value,0)
                    origin(work,value,'sitemap',result.url)
                else:work.set('seed_maps_truncated',True)
        except ScanBudgetExceeded:raise
        except (ScanError,OSError,EOFError,zlib.error):
            add_warning(work,f'Sitemap niet bruikbaar: {current}. De linkcrawl gaat verder.')
        work.finish(item_id)
    if work.get('seed_maps_truncated'):
        add_warning(work,'Sitemapontdekking begrensd; niet alle sitemapverwijzingen zijn verwerkt.')
    work.set('seed_maps_done',True)


def finalize(client,work,start_url,max_depth):
    """Compute depth from the start URL, never from sitemap seed positions."""
    if work.get('provenance_done'):return
    c=work.connection
    def guard():
        try:client.guard()
        except ScanBudgetExceeded:pass  # Local publication must retain pages when the network budget expires.
    c.executescript('DROP TABLE IF EXISTS crawl_paths; CREATE TABLE crawl_paths(url TEXT PRIMARY KEY,depth INTEGER NOT NULL); CREATE INDEX crawl_path_depth ON crawl_paths(depth);')
    with c:c.execute('INSERT INTO crawl_paths VALUES (?,0)',(start_url,))
    for depth in range(1000):
        guard()
        with c:
            c.execute('''INSERT OR IGNORE INTO crawl_paths SELECT a.final_url,p.depth FROM crawl_aliases a
                JOIN crawl_paths p ON p.url=a.url WHERE p.depth=?''',(depth,))
            inserted=c.execute('''INSERT OR IGNORE INTO crawl_paths SELECT e.target,? FROM crawl_edges e
                JOIN crawl_paths p ON p.url=e.source WHERE p.depth=?''',(depth+1,depth)).rowcount
        if not inserted:break
    else:work.set('provenance_truncated',True)
    after=work.get('provenance_after',0)
    while True:
        guard()
        rows=c.execute('''SELECT r.id,r.payload,o.start,o.sitemap,o.link,o.sitemap_url,p.depth
            FROM records r LEFT JOIN crawl_origins o ON o.url=json_extract(r.payload,'$.data.url')
            LEFT JOIN crawl_paths p ON p.url=o.url WHERE r.id>? ORDER BY r.id LIMIT 25''',(after,)).fetchall()
        if not rows:break
        with c:
            for record_id,payload,start,sitemap,link,sitemap_url,depth in rows:
                record=json.loads(payload);data=record['data']
                data['exploration_depth']=data.get('exploration_depth',data.get('depth'))
                data['depth']=depth
                data['discovery_sources']=[key for key,value in [('start_url',start),('sitemap',sitemap),('internal_link',link)] if value]
                data['sitemap_url']=sitemap_url
                data['link_depth_basis']='shortest observed internal-link path from start URL; null means not established'
                c.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(record,ensure_ascii=False),record_id))
            after=rows[-1][0]
            c.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('provenance_after',json.dumps(after)))
    work.set('provenance_done',True)
