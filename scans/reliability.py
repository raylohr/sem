"""Owner-thread attempt ledger, deferred retries and retained parallel responses."""
import gzip
import hashlib
import json
import math
import time
import uuid
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from django.utils import timezone
from .network import FetchResult,ScanError,ScanBudgetExceeded,NetworkRequestError,RobotsDenied
from .work import ScanPause

TRANSIENT={'connect_timeout','read_timeout','connection_reset','connection_refused','connection_error','dns_error'}
SAFE_HEADERS={'content-type','content-encoding','location','retry-after','link','x-robots-tag','strict-transport-security',
              'content-security-policy','x-content-type-options','x-frame-options','referrer-policy'}


class RetryDeferred(Exception):pass


def retry_after(value,now=None):
    now=time.time() if now is None else now
    try:return max(0,float(int(value)))
    except OverflowError:return math.inf
    except (ValueError,TypeError):
        try:return max(0,parsedate_to_datetime(value).timestamp()-now)
        except (ValueError,TypeError,OverflowError):return 0


class Attempts:
    def __init__(self,work,client):
        self.work,self.client=work,client
        self.maximum=client.max_attempts
        self.info={}
        self.active={}
        # A killed process cannot have completed entries that remain 'running'.
        for identifier,payload in work.connection.execute("SELECT id,payload FROM attempts WHERE state='running'").fetchall():
            data=json.loads(payload);data.update(finished_at=None,interruption_detected_at=timezone.now().isoformat(),error_code='interrupted',state='interrupted')
            with work.connection:work.connection.execute("UPDATE attempts SET state='interrupted',payload=? WHERE id=?",(json.dumps(data),identifier))
        client.defer_http_retries=True

    def prepare(self,url):
        cached=self.work.connection.execute('SELECT attempt_id,payload,body FROM retained_downloads WHERE url=?',(url,)).fetchone()
        if cached:
            identifier,payload,body=cached;data=json.loads(payload)
            if 'response' in data:data['result']=FetchResult(body=gzip.decompress(body),**data.pop('response'))
            else:
                code=data.pop('error_code','scan_error')
                error=RobotsDenied('Geblokkeerd volgens robots.txt.') if code=='robots_blocked' else ScanError('Download mislukt; zie foutcategorie.')
                error.code=code
                error.googlebot_allowed=data.pop('googlebot_allowed',None)
                error.details=data.pop('error_details',{});data['error']=error
            data['id']=identifier
            return data
        number=self.work.connection.execute('SELECT count(*) FROM attempts WHERE url=?',(url,)).fetchone()[0]+1
        if number>self.maximum:
            error=ScanError('Maximaal aantal pogingen bereikt na een onderbreking.');error.code='retry_exhausted';error.details={}
            return {'id':None,'error':error,'finished_at':timezone.now().isoformat(),'elapsed':0}
        identifier=str(uuid.uuid4())
        data={'url':url,'ordinal':number,'started_at':timezone.now().isoformat(),
              'connect_timeout':self.client.connect_timeout,'read_timeout':self.client.read_timeout}
        with self.work.connection:self.work.connection.execute('INSERT INTO attempts(id,url,ordinal,state,payload) VALUES (?,?,?,?,?)',(identifier,url,number,'running',json.dumps(data)))
        self.active[url]=identifier
        return identifier

    def perform(self,url,prepared):
        if isinstance(prepared,dict):return prepared
        started=time.monotonic()
        bundle={'id':prepared}
        try:bundle['result']=self.client.fetch(url)
        except Exception as exc:bundle['error']=exc
        bundle.update(finished_at=timezone.now().isoformat(),elapsed=round(time.monotonic()-started,3))
        return bundle

    def retain(self,url,bundle):
        result=bundle.get('result');error=bundle.get('error')
        identifier=bundle['id']
        row=self.work.connection.execute('SELECT payload FROM attempts WHERE id=?',(identifier,)).fetchone()
        data=json.loads(row[0])
        data.update(finished_at=bundle['finished_at'],elapsed=bundle['elapsed'],state='response' if result else 'error',
            http_status=result.status if result else None,error_code=getattr(error,'code','') or ('robots_blocked' if isinstance(error,RobotsDenied) else 'interrupted' if isinstance(error,ScanPause) else 'budget_exhausted' if isinstance(error,ScanBudgetExceeded) else 'scan_error' if error else ''))
        if result:data['redirects']=result.redirects
        if hasattr(error,'details'):data['error_details']=error.details
        cache={'finished_at':bundle['finished_at'],'elapsed':bundle['elapsed']}
        body=None
        if result:
            cache['response']={key:getattr(result,key) for key in ('url','status','elapsed','redirects','blocked_redirect','blocked_destination','robots_allowed','googlebot_allowed','robots_checked','header_values','header_values_observed','measurements')}
            cache['response']['headers']={k:v for k,v in result.headers.items() if k in SAFE_HEADERS}
            body=gzip.compress(result.body,compresslevel=1)
        elif isinstance(error,ScanError) and not isinstance(error,ScanBudgetExceeded):
            cache.update(error_code=data['error_code'],error_details=data.get('error_details',{}),googlebot_allowed=getattr(error,'googlebot_allowed',None))
        else:cache=None
        with self.work.connection:
            self.work.connection.execute('UPDATE attempts SET state=?,payload=? WHERE id=?',(data['state'],json.dumps(data),identifier))
            if cache:self.work.connection.execute('INSERT OR REPLACE INTO retained_downloads VALUES (?,?,?,?)',(url,identifier,json.dumps(cache),body))
        self.active.pop(url,None)
        self.info[url]={'attempt_count':data['ordinal'],'recovered_after_retry':bool(result and 200<=result.status<300 and not result.blocked_redirect and data['ordinal']>1)}
        return data

    def consume(self,url,bundle):
        if bundle['id'] is None:raise bundle['error']
        data=self.retain(url,bundle)
        result=bundle.get('result');error=bundle.get('error')
        transient=getattr(error,'code','') in TRANSIENT or (result is not None and result.status in (429,503))
        if transient and data['ordinal']<self.maximum:
            delay=max(2*2**(data['ordinal']-1),retry_after(result.headers.get('retry-after')) if result else 0)
            if time.monotonic()+delay<self.client.deadline:
                due=time.time()+delay
                from .activity import retry
                retry(self.client.batch_id,'pages',url,due)
                host=urlsplit(url).hostname
                with self.work.connection:
                    self.work.connection.execute("UPDATE frontier SET retry_after=max(retry_after,?) WHERE kind='crawl' AND done=0 AND url=?",(due,url))
                    self.work.connection.execute('DELETE FROM retained_downloads WHERE url=?',(url,))
                    if result and result.status in (429,503):
                        candidates=self.work.connection.execute("SELECT id,url FROM frontier WHERE kind='crawl' AND done=0").fetchall()
                        self.work.connection.executemany('UPDATE frontier SET retry_after=max(retry_after,?) WHERE id=?',[(due,i) for i,value in candidates if urlsplit(value).hostname==host])
                from .large import add_warning
                add_warning(self.work,'Tijdelijke downloadfout: opnieuw ingepland met wachttijd en lager tempo; pogingenhistorie blijft bewaard.')
                slowdown=min(8,self.work.get('retry_slowdown',1)*2)
                self.work.set('retry_slowdown',slowdown)
                self.client.delay=max(self.client.delay,slowdown/self.work.get('configured_rps',1))
                self.client.concurrency=max(1,self.work.get('configured_concurrency',1)//slowdown)
                raise RetryDeferred
        if error:raise error
        return result

    def finish(self,url):
        with self.work.connection:self.work.connection.execute('DELETE FROM retained_downloads WHERE url=?',(url,))

    def preserve_budget_results(self):
        """Keep already completed downloads/failures without making further network calls."""
        from .extraction import extract_page
        from .large import bounded_metadata
        rows=self.work.connection.execute('''SELECT f.id,f.url,f.depth,a.payload FROM frontier f JOIN attempts a ON a.url=f.url
            WHERE f.kind='crawl' AND f.done=0 AND a.ordinal=(SELECT max(b.ordinal) FROM attempts b WHERE b.url=f.url) ORDER BY f.id''')
        for item_id,url,depth,payload in rows:
            if len(self.work.records)>=self.work.get('max_page_results',100000):break
            attempt=json.loads(payload)
            data={'url':url,'depth':depth,'attempt_count':attempt['ordinal'],'measured_at':attempt.get('finished_at') or attempt['started_at']}
            cached=self.work.connection.execute('SELECT payload,body FROM retained_downloads WHERE url=?',(url,)).fetchone()
            metadata=json.loads(cached[0]) if cached else {}
            if 'response' in metadata:
                result=FetchResult(body=gzip.decompress(cached[1]),**metadata['response'])
                fields,links=extract_page(result)
                data.update(final_url=result.url,status=result.redirects[0]['status'] if result.redirects else result.status,final_status=result.status,
                    redirects=result.redirects,blocked_redirect=result.blocked_redirect,content_type=result.headers.get('content-type',''),
                    response_time=result.elapsed,robots_allowed=result.robots_allowed,googlebot_allowed=result.googlebot_allowed,
                    body_hash=hashlib.sha256(result.body).hexdigest(),**bounded_metadata(fields))
                if result.googlebot_allowed is False:data['is_indexable']=False
                if getattr(self.client,'discover_sitemaps',False) is True:
                    from .seeding import links as remember_links
                    remember_links(self.work,self.client,url,result.url,links,self.work.get('max_page_results',100000))
            else:
                data.update(error='Download niet voltooid binnen het netwerkbudget; zie pogingenhistorie.',
                    error_code=attempt.get('error_code') or 'budget_exhausted',error_details=attempt.get('error_details',{}))
            if self.work.get('retry_only',False):
                row=self.work.connection.execute('SELECT payload FROM retry_origins WHERE url=?',(url,)).fetchone()
                original=json.loads(row[0]) if row else {}
                data.update(depth=original.get('depth'),discovery_sources=['retry'],retry_of_batch=self.work.get('retry_of_batch'),
                    link_depth_basis='Overgenomen uit oorspronkelijke crawl; deze herdownload bepaalt geen nieuwe sitestructuur.')
                data['recovered_from_previous_scan']=bool(data.get('final_status') is not None and 200<=data['final_status']<300 and not data.get('error') and not data.get('blocked_redirect'))
            self.work.finish(item_id,{'locator':f'crawl:page:{len(self.work.records)}','data':data})
            self.finish(url)


def publish_attempts(batch,work):
    from .models import CrawlAttempt
    from django.utils.dateparse import parse_datetime
    from itertools import islice
    rows=iter(work.connection.execute("SELECT id,url,ordinal,state,payload FROM attempts WHERE published=0 AND state!='running' ORDER BY url,ordinal"))
    while chunk:=list(islice(rows,500)):
        values=[]
        for identifier,url,ordinal,state,payload in chunk:
            data=json.loads(payload)
            values.append(CrawlAttempt(id=identifier,batch=batch,url=url,url_hash=hashlib.sha256(url.encode()).hexdigest(),ordinal=ordinal,state=state,
                started_at=parse_datetime(data['started_at']),finished_at=parse_datetime(data['finished_at']) if data.get('finished_at') else None,
                http_status=data.get('http_status'),error_code=data.get('error_code',''),details=data))
        CrawlAttempt.objects.bulk_create(values,ignore_conflicts=True,batch_size=500)
        with work.connection:work.connection.executemany('UPDATE attempts SET published=1 WHERE id=?',[(row[0],) for row in chunk])
