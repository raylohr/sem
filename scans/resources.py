"""Disk-backed bounded resource/external phases, separate from page traversal."""
import hashlib
import json
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from urllib.parse import urljoin,urlsplit
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .network import SafeClient,ScanError,ScanBudgetExceeded,NetworkRequestError,RobotsDenied,normalize_url
from .work import ScanPause
from .host_rules import navigation_hosts,resource_hosts,excluded
from .resource_transfer import SAFE_HEADERS

DEFAULTS={'resource_max_targets':10000,'external_max_targets':1000,'resource_max_bytes':250*1024**2,
          'external_max_bytes':10*1024**2,'resource_max_depth':3,'resource_file_bytes':5*1024**2,'image_advice_bytes':100*1024}
MAX_INVENTORY=50000
MAX_REFS=100000


class PolicyDenied(ScanError):pass


class ResourceClient(SafeClient):
    def __init__(self,config,purpose,**kwargs):
        super().__init__([],progress_phase='files' if purpose=='resource' else 'external',**kwargs)
        self.config=dict(config);self.purpose=purpose;self.capture_documents=False
        original_check=self.check_access
        def check():
            batch=original_check()
            scope=getattr(batch,'current_host_scope',None)
            if scope:
                self.config['resource_hosts']=sorted(set(config.get('resource_hosts',[])) & set(scope['resource_hosts']))
                self.config['blocked_resource_hosts']=sorted(set(config.get('blocked_resource_hosts',[]))|set(scope['blocked_resource_hosts']))
                self.config['blocked_external_hosts']=sorted(set(config.get('blocked_external_hosts',[]))|set(scope['blocked_external_hosts']))
        self.check_access=check
        self.resource_body_limit=config.get('resource_file_bytes',DEFAULTS['resource_file_bytes']) if purpose=='resource' else 16384
        self.defer_http_retries=True
        # Each hop is checked by permitted(); avoid mutating host scope in workers.
        from .host_rules import HostRules
        self.allowed_hosts=HostRules(all_public=True)

    def permitted(self,url):
        host=urlsplit(normalize_url(url)).hostname
        if self.purpose=='external':return self.config.get('check_external_links',False) and not excluded(host,self.config.get('blocked_external_hosts',[]))
        return host in resource_hosts(self.config)

    def fetch_once(self,url,enforce_robots=True,cookie_header=''):
        self.guard()
        url=normalize_url(url)
        if not self.permitted(url):raise PolicyDenied('Hostbeleid blokkeert dit meetdoel.')
        return super().fetch_once(url,enforce_robots=enforce_robots)

    def fetch(self,url,enforce_robots=True):
        redirects=[];seen=set()
        for hop in range(6):
            url=normalize_url(url)
            if url in seen:raise ScanError('Redirectlus in bestands- of externe meting.')
            seen.add(url)
            result=self.fetch_once(url,enforce_robots=enforce_robots)
            if not enforce_robots and urlsplit(url).path=='/robots.txt' and not result.measurements.get('body_complete',False):raise ScanError('robots.txt kon niet volledig worden gelezen.')
            if result.status not in (301,302,303,307,308) or not result.headers.get('location'):
                result.redirects=redirects;return result
            destination=urljoin(url,result.headers['location'])
            try:
                destination=normalize_url(destination)
                if not self.permitted(destination):raise PolicyDenied('Redirect buiten meetbeleid.')
            except ScanError:
                result.redirects=redirects;result.blocked_redirect='Redirect geblokkeerd door meetbeleid.'
                return result
            redirects.append({'url':url,'status':result.status,'destination':destination});url=destination
        raise ScanError('Te veel redirects in bestands- of externe meting.')


def initialize(work):
    work.connection.executescript('''
        CREATE TABLE IF NOT EXISTS resource_targets (key TEXT PRIMARY KEY,purpose TEXT NOT NULL,url TEXT NOT NULL,depth INTEGER NOT NULL,state TEXT NOT NULL DEFAULT 'queued',result TEXT);
        CREATE INDEX IF NOT EXISTS resource_pending ON resource_targets(purpose,state);
        CREATE TABLE IF NOT EXISTS resource_refs (id INTEGER PRIMARY KEY,target_key TEXT NOT NULL,identity TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(target_key,identity));
        CREATE INDEX IF NOT EXISTS resource_ref_target ON resource_refs(target_key);
        CREATE TABLE IF NOT EXISTS resource_css_edges (parent_key TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(parent_key,payload));
    ''')
    work.resource_target_count=work.connection.execute('SELECT count(*) FROM resource_targets').fetchone()[0]
    work.resource_purpose_counts=dict(work.connection.execute('SELECT purpose,count(*) FROM resource_targets GROUP BY purpose'))
    work.resource_ref_count=work.connection.execute('SELECT count(*) FROM resource_refs').fetchone()[0]


def add(work,url,purpose,reference,depth=0):
    try:url=normalize_url(url)
    except ScanError:return
    key=hashlib.sha256((purpose+'\n'+url).encode()).hexdigest()
    connection=work.connection
    exists=connection.execute('SELECT 1 FROM resource_targets WHERE key=?',(key,)).fetchone()
    if not exists:
        count=work.resource_target_count
        if count>=MAX_INVENTORY:
            work.set('resource_inventory_truncated',True);return
        connection.execute('INSERT OR IGNORE INTO resource_targets(key,purpose,url,depth) VALUES (?,?,?,?)',(key,purpose,url,depth))
        work.resource_target_count+=1
        work.resource_purpose_counts[purpose]=work.resource_purpose_counts.get(purpose,0)+1
    else:connection.execute('UPDATE resource_targets SET depth=min(depth,?) WHERE key=?',(depth,key))
    from .references import classify
    classification=classify(url,reference.get('source_url',url),getattr(work,'reference_config',{}),function='loaded_resource' if reference.get('kind') not in ('download','hyperlink') else 'download' if reference.get('kind')=='download' else None)
    reference={**reference,**{k:classification[k] for k in ('destination','function','scope')},'classification_version':classification['version']}
    reference={k:v for k,v in reference.items() if k in ('source_url','locator','element_path','kind','parent_url','raw_url','reference_type','destination','function','scope','classification_version')}
    identity=hashlib.sha256(json.dumps(reference,sort_keys=True).encode()).hexdigest()
    if work.resource_ref_count>=MAX_REFS:
        work.set('resource_inventory_truncated',True);return
    inserted=connection.execute('INSERT OR IGNORE INTO resource_refs(target_key,identity,payload) VALUES (?,?,?)',(key,identity,json.dumps(reference))).rowcount
    work.resource_ref_count+=inserted


def seed(work,config,check_access):
    from .resource_discovery import css_urls
    after=work.get('resource_seed_after',0);started=time.monotonic()
    for index,record in enumerate(work.records):
        if index<after:continue
        if index%20==0:
            check_access();work.check_disk()
            if time.monotonic()-started>60:raise ScanPause
        if work.resource_ref_count>=MAX_REFS:
            work.set('resource_inventory_truncated',True);break
        data=record.get('data',{});source=data.get('final_url') or data.get('url')
        if not source:continue
        with work.connection:
            if config.get('check_resources'):
                if data.get('analysis_truncated') or 'resource_candidates' not in data or not data.get('resource_discovery',{}).get('complete',False):work.set('resource_discovery_incomplete',True)
                for reference in data.get('resource_candidates',[]):
                    add(work,reference.get('url',''),'resource',{**reference,'source_url':source})
            if config.get('check_external_links'):
                for reference in data.get('link_details',[]):
                    try:host=urlsplit(normalize_url(reference.get('url'))).hostname
                    except ScanError:continue
                    from .references import classify
                    classification=classify(reference['url'],source,config,download=reference.get('download',False))
                    if classification['function'] in ('fragment','contact','action'):continue
                    if classification['function']=='download' and config.get('check_resources'):continue
                    if host not in navigation_hosts(config):
                        shared=work.connection.execute("SELECT 1 FROM resource_targets WHERE purpose='resource' AND url=?",(normalize_url(reference['url']),)).fetchone()
                        add(work,reference['url'],'resource' if shared else 'external',{**reference,'source_url':source,'kind':'download' if classification['function']=='download' else 'hyperlink'})
                if data.get('links_truncated') or data.get('analysis_truncated'):work.set('resource_discovery_incomplete',True)
        work.set('resource_seed_after',index+1)
    work.set('resource_seed_done',True)


def result_for(client,url):
    try:
        result=client.fetch(url)
        measurements=dict(result.measurements)
        headers={key:str(value)[:4096] for key,value in result.headers.items() if key.lower() in SAFE_HEADERS}
        status=result.status
        outcome='unknown_response' if not 200<=status<600 else 'blocked' if status in (401,403,429) else 'http_error' if status>=400 else 'redirect_blocked' if result.blocked_redirect else 'success' if 200<=status<300 else 'response'
        evidence={'redirects':result.redirects,'response_time_ms':round(result.elapsed*1000),
            'body_hash':hashlib.sha256(result.body).hexdigest(),'hash_scope':'retained decoded body; full only if body_complete',
            'blocked_redirect':result.blocked_redirect,'request_profile':'baseline GET; no cookies or authorization'}
        if status>=400 and ('text/' in headers.get('content-type','') or 'json' in headers.get('content-type','')):evidence['body_excerpt']=result.body[:512].decode('utf-8',errors='replace')
        return {'outcome':outcome,'http_status':status,'final_url':result.url,'measured_at':timezone.now().isoformat(),
                'headers':headers,'measurements':measurements,'evidence':evidence},result
    except (ScanPause,ScanBudgetExceeded):raise
    except (ScanError,ValueError) as exc:
        if isinstance(exc,(PolicyDenied,RobotsDenied)):
            from . import activity
            identifier=activity.start(client.batch_id,client.progress_phase,url)
            activity.finish(client.batch_id,client.progress_phase,identifier,outcome_override='blocked')
        code=exc.code if isinstance(exc,NetworkRequestError) else 'robots_denied' if isinstance(exc,RobotsDenied) else 'policy_denied' if isinstance(exc,PolicyDenied) else 'request_error'
        return {'outcome':'blocked' if isinstance(exc,(PolicyDenied,RobotsDenied)) else 'download_error','measured_at':timezone.now().isoformat(),
                'evidence':{'error_code':code,'message':str(exc)[:250]}},None


def expand_css(work,key,url,depth,result,config):
    if not result:return
    mime=result.headers.get('content-type','').split(';')[0].lower().strip()
    if mime=='text/css' and result.status==206:work.set('resource_css_incomplete',True)
    if result.status!=200 or mime!='text/css':return
    if not result.measurements.get('body_complete'):
        work.set('resource_css_incomplete',True);return
    from .resource_discovery import css_urls
    refs,complete=css_urls(result.body.decode('utf-8',errors='replace'))
    if not complete:work.set('resource_css_incomplete',True)
    if depth>=config.get('resource_max_depth',DEFAULTS['resource_max_depth']):
        if refs:work.set('resource_css_depth_limited',True)
        return
    for ref in refs:work.connection.execute('INSERT OR IGNORE INTO resource_css_edges VALUES (?,?)',(key,json.dumps(dict(ref,resolved_url=urljoin(result.url,ref['raw_url'])))))
    for row in work.connection.execute('SELECT payload FROM resource_refs WHERE target_key=?',(key,)).fetchall():
        parent=json.loads(row[0])
        for ref in refs:
            add(work,urljoin(result.url,ref['raw_url']),'resource',{**ref,'source_url':parent['source_url'],
                'element_path':parent.get('element_path',''),'parent_url':url},depth+1)


def run(batch,work,config,check_access):
    work.reference_config=config
    initialize(work)
    if not work.get('resource_seed_done'):seed(work,config,check_access)
    if hasattr(batch,'project_id'):
        from .models import HostPolicy
        seen=set()
        for url,purpose in work.connection.execute('SELECT url,purpose FROM resource_targets ORDER BY rowid'):
            host=urlsplit(url).hostname
            if host in seen or host in navigation_hosts(config):continue
            if len(seen)>=100:break
            seen.add(host)
            HostPolicy.objects.get_or_create(project_id=batch.project_id,host=host,defaults={'discovered_batch_id':batch.pk,'example_url':url,'purpose':'resources' if purpose=='resource' else 'external'})
    # Never blindly repeat a request whose worker died before committing its result.
    with work.connection:
        work.connection.execute("UPDATE resource_targets SET state='done',result=? WHERE state='fetching'",(json.dumps({'outcome':'interrupted_unknown','evidence':{'message':'Onderbroken verzoek; eindantwoord onbekend, niet automatisch opnieuw opgehaald.'}}),))
    for purpose,enabled in [('resource',config.get('check_resources')),('external',config.get('check_external_links'))]:
        if not enabled:continue
        from .activity import phase
        phase_name='files' if purpose=='resource' else 'external'
        total=work.resource_purpose_counts.get(purpose,0)
        done=work.connection.execute("SELECT count(*) FROM resource_targets WHERE purpose=? AND state='done'",(purpose,)).fetchone()[0]
        phase(getattr(batch,'pk',work.batch_id),phase_name,done=done,total=total)
        limit=config.get(purpose+'_max_targets',DEFAULTS[purpose+'_max_targets'])
        client=ResourceClient(config,purpose,check_access=check_access,batch_id=batch.pk,
            concurrency=config.get('concurrency',1),network_scope=config.get('network_scope',batch.pk),
            duration=max(0,config['duration']-(time.time()-work.started)),slice_seconds=90,
            max_transfer=config.get(purpose+'_max_bytes',DEFAULTS[purpose+'_max_bytes']),
            connect_timeout=config.get('connect_timeout',10),read_timeout=config.get('read_timeout',30),
            respect_robots=config.get('respect_robots',True),delay=1/config.get('requests_per_second',1),shared_rate=True,
            robot_cache=work.get('resource_robots_'+purpose,{}))
        client.transferred=work.get(purpose+'_transferred',0);client.decoded_bytes=work.get(purpose+'_decoded',0)
        try:
            pending={}
            count=done
            executor=ThreadPoolExecutor(max_workers=max(1,config.get('concurrency',1)))
            try:
                while True:
                    client.guard();work.check_disk()
                    budget=client.transferred>=client.max_transfer or client.decoded_bytes>=client.max_transfer
                    capacity=max(0,min(client.concurrency-len(pending),limit-count-len(pending)))
                    if budget or count>=limit:
                        with work.connection:work.connection.execute("UPDATE resource_targets SET state='done',result=? WHERE purpose=? AND state='queued'",(json.dumps({'outcome':'not_measured_budget'}),purpose))
                        capacity=0
                    rows=work.connection.execute("SELECT key,url,depth FROM resource_targets WHERE purpose=? AND state='queued' ORDER BY depth,rowid LIMIT ?",(purpose,capacity)).fetchall()
                    for key,url,depth in rows:
                        with work.connection:work.connection.execute("UPDATE resource_targets SET state='fetching' WHERE key=?",(key,))
                        pending[executor.submit(result_for,client,url)]=(key,url,depth)
                    if not pending:break
                    completed,_=wait(pending,timeout=.1,return_when=FIRST_COMPLETED)
                    for future in completed:
                        key,url,depth=pending.pop(future)
                        try:measurement,response=future.result()
                        except (ScanPause,ScanBudgetExceeded):
                            with work.connection:work.connection.execute("UPDATE resource_targets SET state='done',result=? WHERE key=?",(json.dumps({'outcome':'interrupted_unknown'}),key))
                            raise
                        with work.connection:
                            if purpose=='resource':expand_css(work,key,url,depth,response,config)
                            work.connection.execute("UPDATE resource_targets SET state='done',result=? WHERE key=?",(json.dumps(measurement),key))
                        count+=1
                        phase(getattr(batch,'pk',work.batch_id),phase_name,done=count,total=work.resource_purpose_counts.get(purpose,0))
                        work.set('resource_progress',summary(work,config))
            finally:
                # Only the owner writes SQLite. Drain actual completed responses even
                # on cancellation; interrupted in-flight requests stay explicitly unknown.
                client.stopped.set()
                executor.shutdown(wait=True,cancel_futures=True)
                for future,(key,url,depth) in pending.items():
                    try:
                        measurement,response=future.result()
                    except BaseException:
                        measurement={'outcome':'interrupted_unknown'};response=None
                    with work.connection:
                        if purpose=='resource' and response:expand_css(work,key,url,depth,response,config)
                        work.connection.execute("UPDATE resource_targets SET state='done',result=? WHERE key=?",(json.dumps(measurement),key))
        except ScanBudgetExceeded:
            with work.connection:work.connection.execute("UPDATE resource_targets SET state='done',result=? WHERE purpose=? AND state='queued'",(json.dumps({'outcome':'not_measured_time'}),purpose))
        finally:
            work.set(purpose+'_transferred',client.transferred);work.set(purpose+'_decoded',client.decoded_bytes)
            work.set('resource_robots_'+purpose,client.robot_cache);client.close()
        finished=work.connection.execute("SELECT count(*) FROM resource_targets WHERE purpose=? AND state='done'",(purpose,)).fetchone()[0]
        total=work.resource_purpose_counts.get(purpose,0)
        phase(getattr(batch,'pk',work.batch_id),phase_name,status='complete',done=finished,total=total)
    propagate_css_refs(work,config)
    work.set('resource_progress',summary(work,config));work.set('resources_done',True)


def summary(work,config):
    counts={purpose:dict(Counter((json.loads(result).get('outcome') if result else 'queued') for result, in work.connection.execute('SELECT result FROM resource_targets WHERE purpose=?',(purpose,)))) for purpose in ('resource','external')}
    return {'version':'resources-v1','outcomes':counts,'references':work.connection.execute('SELECT count(*) FROM resource_refs').fetchone()[0],
        'resource_wire_bytes':work.get('resource_transferred',0),'external_wire_bytes':work.get('external_transferred',0),
        'discovery_incomplete':bool(work.get('resource_inventory_truncated') or work.get('resource_discovery_incomplete') or work.get('resource_css_incomplete') or work.get('resource_css_depth_limited')),
        'limits':{key:config.get(key,value) for key,value in DEFAULTS.items()},
        'external_enabled':bool(config.get('check_external_links')),'resources_enabled':bool(config.get('check_resources')),
        'scope':'baseline HTTP GET; encoded body bytes exclude headers/framing; not browser traffic; no external page expansion'}


def publish(batch,work):
    from .models import ResourceMeasurement,ResourceReference
    buffer=[]
    def flush_targets():
        ResourceMeasurement.objects.bulk_create(buffer,batch_size=500,update_conflicts=True,unique_fields=['id'],
            update_fields=['outcome','http_status','final_url','measured_at','headers','measurements','evidence'])
        buffer.clear()
    for key,purpose,url,payload in work.connection.execute('SELECT key,purpose,url,result FROM resource_targets ORDER BY rowid'):
        data=json.loads(payload) if payload else {'outcome':'not_measured'}
        buffer.append(ResourceMeasurement(id=uuid.uuid5(batch.pk,key),batch=batch,purpose=purpose,url=url,
            url_hash=hashlib.sha256(url.encode()).hexdigest(),outcome=data['outcome'],http_status=data.get('http_status'),
            final_url=data.get('final_url',''),measured_at=parse_datetime(data['measured_at']) if data.get('measured_at') else None,
            headers=data.get('headers',{}),measurements=data.get('measurements',{}),evidence=data.get('evidence',{})))
        if len(buffer)>=500:flush_targets()
    if buffer:flush_targets()
    for key,identity,payload in work.connection.execute('SELECT target_key,identity,payload FROM resource_refs ORDER BY id'):
        ref=json.loads(payload)
        buffer.append(ResourceReference(target_id=uuid.uuid5(batch.pk,key),reference_hash=identity,source_url=ref['source_url'],locator=ref.get('locator','')[:120],
            element_path=ref.get('element_path','')[:1024],kind=ref.get('kind','resource')[:32],parent_url=ref.get('parent_url',''),details=ref))
        if len(buffer)>=1000:
            ResourceReference.objects.bulk_create(buffer,ignore_conflicts=True,batch_size=1000);buffer.clear()
    if buffer:ResourceReference.objects.bulk_create(buffer,ignore_conflicts=True,batch_size=1000)


def propagate_css_refs(work,config):
    for iteration in range(config.get('resource_max_depth',DEFAULTS['resource_max_depth'])+1):
        before=work.connection.execute('SELECT count(*) FROM resource_refs').fetchone()[0]
        edges=work.connection.execute('SELECT e.parent_key,e.payload,t.url,t.depth FROM resource_css_edges e JOIN resource_targets t ON t.key=e.parent_key').fetchall()
        for key,payload,url,depth in edges:
            ref=json.loads(payload)
            parents=work.connection.execute('SELECT payload FROM resource_refs WHERE target_key=?',(key,)).fetchall()
            with work.connection:
                for row in parents:
                    parent=json.loads(row[0])
                    add(work,ref.get('resolved_url') or urljoin(url,ref['raw_url']),'resource',{**ref,'source_url':parent['source_url'],'element_path':parent.get('element_path',''),'parent_url':url},depth+1)
        after=work.connection.execute('SELECT count(*) FROM resource_refs').fetchone()[0]
        if before==after:break
