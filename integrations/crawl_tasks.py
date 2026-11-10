"""Dedicated Celery crawl agents. Rendering never runs in the web/import worker."""
from datetime import timedelta
from decimal import Decimal
import time
from urllib.parse import urlsplit, urljoin
from uuid import UUID, uuid4

from celery import shared_task
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
import requests

from sources.models import SourceRecord
from sources.inventory_index import url_expression
from .models import PlaywrightCrawl, PlaywrightCrawlURL, PlaywrightDomainBudget
from .crawl_http import SafeHTTP, CrawlNetworkError, RobotsRules, sitemap_entries, MAX_ROBOTS, retry_after_seconds
from .crawl_service import (ACTIVE, access, add_urls, acquire_slot, dispatch_run, stop_crawl, report_health,
                            page_allowed, scope, CrawlExpired)
from .playwright_config import in_scope, DESKTOP_PROFILES
from .crawl_renderer import desktop_user_agent, manifest_browser_version

ERRORS = {'timeout':'Timeout tijdens ophalen of renderen.','navigation_failed':'Pagina kon niet worden geladen.',
          'render_failed':'Browser kon de pagina niet renderen.','render_incomplete':'Rendering niet volledig afgerond.',
          'robots_blocked':'Overgeslagen volgens robots.txt.','robots_unknown':'Robotsregels voor redirect nog niet beschikbaar.',
          'external_redirect':'Redirect naar extern domein; alleen bereikbaarheid controleren.',
          'non_html':'Bestemming levert geen HTML.','page_too_large':'Pagina overschrijdt de groottebegrenzing.',
          'network_failed':'Bestemming niet bereikbaar of timeout.'}


def enqueue_bootstrap(crawl_id):
    try:
        bootstrap_crawl.apply_async(args=[str(crawl_id)],queue='crawler')
    except Exception:
        pass


def enqueue_url(item_id,token):
    try:
        process_crawl_url.apply_async(args=[item_id,str(token)],queue='crawler')
    except Exception:
        pass


def metadata_pause(crawl_id):
    while True:
        crawl=access(crawl_id)
        with transaction.atomic():
            budget,_=PlaywrightDomainBudget.objects.get_or_create(domain=crawl.domain)
            budget=PlaywrightDomainBudget.objects.select_for_update().get(pk=crawl.domain)
            now=timezone.now()
            wait=(budget.next_start_at-now).total_seconds() if budget.next_start_at and budget.next_start_at>now else 0
            if not wait:
                configurations=PlaywrightCrawl.objects.filter(domain=crawl.domain,status__in=ACTIVE).values_list('configuration',flat=True)
                base=min(Decimal(config['pages_per_second']) for config in configurations)
                rate=max(Decimal('0.05'),base*budget.rate_factor)
                budget.next_start_at=now+timedelta(seconds=float(1/rate))
                budget.save()
                return
        time.sleep(min(1,wait))


def robots_for(crawl_id,url):
    crawl=access(crawl_id)
    origin=urlsplit(url)
    key=origin.netloc
    cached=crawl.configuration.get('robots',{}).get(key)
    if cached is not None:
        retry_at=parse_datetime(cached.get('retry_at','')) if cached.get('retry_at') else None
        if not cached.get('unreachable') or retry_at and timezone.now()<retry_at:
            return cached
    metadata_pause(crawl_id)
    http=SafeHTTP(settings.CRAWLER_EGRESS_PROXY,desktop_user_agent(DESKTOP_PROFILES[crawl.profile],manifest_browser_version(DESKTOP_PROFILES[crawl.profile].engine)),lambda:access(crawl_id))
    try:
        result=http.fetch(f'{origin.scheme}://{origin.netloc}/robots.txt',maximum=MAX_ROBOTS,
                          allowed=lambda value:in_scope(scope(crawl),value))
        robot={'text':result.body.decode('utf-8','replace') if result.status==200 else '',
               'unreachable':result.status>=500 or result.status==429}
        if robot['unreachable']:
            report_health(crawl_id,failed=True,retry_after=retry_after_seconds(result.headers.get('retry-after')))
    except CrawlNetworkError:
        robot={'text':'','unreachable':True}
    if robot['unreachable']:
        robot['retry_at']=(timezone.now()+timedelta(seconds=60)).isoformat()
    with transaction.atomic():
        current=PlaywrightCrawl.objects.select_for_update().get(pk=crawl_id)
        current.configuration={**current.configuration,'robots':{**current.configuration.get('robots',{}),key:robot}}
        current.save(update_fields=['configuration'])
    return robot


def add_in_chunks(crawl_id,values,**kwargs):
    chunk=[]
    for value in values:
        chunk.append(value)
        if len(chunk)==250:
            add_urls(crawl_id,chunk,**kwargs)
            chunk=[]
    if chunk:
        add_urls(crawl_id,chunk,**kwargs)


@shared_task(ignore_result=True,time_limit=900,soft_time_limit=870)
def bootstrap_crawl(crawl_id):
    token=None
    try:
        with transaction.atomic():
            crawl=PlaywrightCrawl.objects.select_for_update().get(pk=crawl_id)
            if crawl.status!='pending':
                return
            access(crawl_id)
            token=uuid4()
            crawl.status='preparing'
            crawl.started_at=crawl.started_at or timezone.now()
            crawl.run_token=token
            crawl.lease_until=timezone.now()+timedelta(minutes=16)
            crawl.save(update_fields=['status','started_at','run_token','lease_until'])
        configuration=crawl.configuration
        add_urls(crawl_id,[configuration['website']],provenance='website')
        add_in_chunks(crawl_id,configuration['start_urls'],provenance='manual')
        batch_ids=[batch.pk for batch in crawl.project.import_batches.filter(status__in=('complete','incomplete'))
                   if batch.request_metadata.get('record_scope') not in ('summary','external')]
        seeds=SourceRecord.objects.filter(batch_id__in=batch_ids).annotate(seed_url=url_expression()).values_list('seed_url',flat=True).distinct()
        add_in_chunks(crawl_id,seeds.iterator(chunk_size=500),provenance='inventory')
        notes=[]
        successful_sitemaps=0
        if configuration['use_sitemaps']:
            origin=urlsplit(configuration['website'])
            robot=robots_for(crawl_id,configuration['website'])
            rules=RobotsRules(robot.get('text',''))
            pending=list(dict.fromkeys([*configuration['sitemap_urls'],*rules.sitemaps,
                                       f'{origin.scheme}://{origin.netloc}/sitemap.xml']))
            seen=set()
            http=SafeHTTP(settings.CRAWLER_EGRESS_PROXY,desktop_user_agent(DESKTOP_PROFILES[crawl.profile],manifest_browser_version(DESKTOP_PROFILES[crawl.profile].engine)),lambda:access(crawl_id,token=token))
            while pending and len(seen)<100:
                url=pending.pop(0)
                if url in seen or not page_allowed(crawl,url):
                    continue
                seen.add(url)
                try:
                    metadata_pause(crawl_id)
                    result=http.fetch(url,allowed=lambda value:page_allowed(crawl,value))
                    if result.status!=200:
                        notes.append({'type':'sitemap','url':url,'status':result.status})
                        continue
                    kind,values=sitemap_entries(result.body)
                    successful_sitemaps+=1
                    if kind=='sitemapindex':
                        pending.extend(values[:100])
                    else:
                        add_in_chunks(crawl_id,values,provenance='sitemap',discovered_from=url)
                except CrawlNetworkError:
                    notes.append({'type':'sitemap','url':url,'error':'niet_beschikbaar'})
            if not successful_sitemaps:
                notes.append({'type':'sitemap_missing'})
            if pending:
                notes.append({'type':'sitemap_limit','limit':100})
        with transaction.atomic():
            current=PlaywrightCrawl.objects.select_for_update().get(pk=crawl_id)
            access(crawl_id,token=token)
            current.status='running'
            current.notes=notes[:100]
            current.limit_reached=current.limit_reached or any(note['type']=='sitemap_limit' for note in notes)
            current.lease_until=None
            current.save(update_fields=['status','notes','limit_reached','lease_until'])
        dispatch_run(crawl_id)
    except PlaywrightCrawl.DoesNotExist:
        return
    except PermissionDenied:
        if PlaywrightCrawl.objects.filter(pk=crawl_id,run_token=token,status__in=('pending','preparing')).exists():
            stop_crawl(crawl_id,reason='Projecttoegang of domeingrenzen gewijzigd.')
    except CrawlExpired:
        stop_crawl(crawl_id,reason='Maximale looptijd bereikt.',incomplete=True)
    except Exception:
        with transaction.atomic():
            current=PlaywrightCrawl.objects.select_for_update().filter(pk=crawl_id,status='preparing',run_token=token).first()
            if current:
                current.status='failed'
                current.error='Crawl kon niet worden voorbereid.'
                current.finished_at=timezone.now()
                current.save(update_fields=['status','error','finished_at'])


def call_renderer(item,crawl):
    budget=PlaywrightDomainBudget.objects.filter(pk=crawl.domain).first()
    factor=float(budget.rate_factor) if budget else 1
    payload={'url':item.url,'profile':crawl.profile,'configuration':{
        **crawl.configuration,'effective_rate':max(.05,float(crawl.configuration['pages_per_second'])*factor),'robots':{urlsplit(item.url).netloc:crawl.configuration.get('robots',{}).get(urlsplit(item.url).netloc,{})}}}
    with requests.Session() as session:
        session.trust_env=False
        try:
            with session.post(settings.CRAWLER_RENDER_URL,json=payload,headers={
                    'Authorization':'Bearer '+settings.CRAWLER_RENDER_TOKEN},
                    timeout=(5,crawl.configuration['render_timeout_seconds']+40),stream=True,allow_redirects=False) as response:
                if response.status_code!=200:
                    return {'error':'render_failed','retry_after':10 if response.status_code==429 else 0}
                chunks=[]
                size=0
                for chunk in response.iter_content(65536):
                    size+=len(chunk)
                    if size>2*1024*1024:
                        raise CrawlNetworkError('Browserantwoord te groot.')
                    chunks.append(chunk)
                import json
                result=json.loads(b''.join(chunks))
                if not isinstance(result,dict):
                    raise ValueError
                return result
        except (requests.RequestException,ValueError):
            return {'error':'render_failed'}


@shared_task(bind=True,ignore_result=True,time_limit=180,soft_time_limit=165,max_retries=10000)
def process_crawl_url(self,item_id,token):
    crawl_id=None
    try:
        item=PlaywrightCrawlURL.objects.select_related('crawl').get(pk=item_id)
        crawl_id=item.crawl_id
        if item.status!='queued' or str(item.task_token)!=str(token):
            return
        wait=acquire_slot(item_id,token)
        if wait:
            PlaywrightCrawlURL.objects.filter(pk=item_id,status='queued',task_token=token).update(lease_until=timezone.now()+timedelta(minutes=4))
            raise self.retry(countdown=wait)
        item.refresh_from_db()
        crawl=access(crawl_id)
        if item.kind=='page':
            if not page_allowed(crawl,item.url):
                data={'error':'scope_changed'}
            elif crawl.configuration['respect_robots']:
                robot=robots_for(crawl_id,item.url)
                crawl=access(crawl_id)
                if robot['unreachable']:
                    data={'error':'network_failed','retry_after':60}
                else:
                    data=call_renderer(item,crawl)
            else:
                data=call_renderer(item,crawl)
        else:
            http=SafeHTTP(settings.CRAWLER_EGRESS_PROXY,'Mozilla/5.0',lambda:access(crawl_id))
            result=http.reachable(item.url)
            data={'http_status':result.status,'final_url':result.url,'redirects':result.redirects,
                  'retry_after':retry_after_seconds(result.headers.get('retry-after')),
                  'reachable':True if 200<=result.status<400 else False if result.status in (404,410) else None,
                  'error':''}
        status=data.get('http_status') or 0
        error=data.get('error','')
        retryable=error in ('timeout','navigation_failed','render_failed','render_incomplete','network_failed') or status in (429,500,502,503,504)
        if item.kind=='page' and not error and not data.get('render_complete',False):
            error='render_incomplete'
            data['error']=error
            retryable=True
        if item.kind=='page':
            slow=float(data.get('ttfb_seconds',0))>crawl.configuration['slow_after_seconds']
            report_health(crawl_id,slow=slow,failed=retryable,retry_after=data.get('retry_after',0))
        with transaction.atomic():
            current=PlaywrightCrawl.objects.select_for_update().get(pk=crawl_id)
            access(crawl_id)
            row=PlaywrightCrawlURL.objects.select_for_update().get(pk=item_id)
            if row.status!='processing' or str(row.task_token)!=str(token):
                return
            row.metadata=data
            row.error=ERRORS.get(error,'')
            row.lease_until=None
            if retryable and row.attempts<=current.configuration['max_retries']:
                row.status='pending'
                row.next_attempt_at=timezone.now()+timedelta(seconds=max(10*row.attempts,data.get('retry_after',0)))
            elif error in ('robots_blocked','robots_unknown','external_redirect','non_html','scope_changed'):
                row.status='skipped'
            elif retryable or error:
                row.status='failed'
                row.error=row.error or 'Tijdelijke fout bleef bestaan na de ingestelde retries.'
            else:
                row.status='complete'
            row.save(update_fields=['status','metadata','error','lease_until','next_attempt_at'])
            if data.get('links_truncated'):
                current.limit_reached=True
                current.save(update_fields=['limit_reached'])
        links=data.get('links',[]) if item.kind=='page' else []
        blocked=data.get('blocked_navigation')
        if blocked:
            links=[*links,blocked]
        internal=[url for url in links if page_allowed(crawl,url)]
        external=[url for url in links if isinstance(url,str) and not in_scope(scope(crawl),url)]
        if crawl.configuration['discover_urls']:
            add_in_chunks(crawl_id,internal,depth=item.depth+1,discovered_from=item.url,provenance='discovered')
        elif blocked and page_allowed(crawl,blocked):
            add_urls(crawl_id,[blocked],depth=item.depth,discovered_from=item.url,provenance='redirect')
        add_in_chunks(crawl_id,external,kind='external',depth=item.depth,discovered_from=item.url,provenance='external')
    except PlaywrightCrawlURL.DoesNotExist:
        return
    except PlaywrightCrawl.DoesNotExist:
        return
    except PermissionDenied:
        if crawl_id:
            try:
                access(crawl_id)
            except PermissionDenied:
                stop_crawl(crawl_id,reason='Projecttoegang of domeingrenzen gewijzigd.')
            except CrawlExpired:
                stop_crawl(crawl_id,reason='Maximale looptijd bereikt.',incomplete=True)
    except CrawlExpired:
        if crawl_id:
            stop_crawl(crawl_id,reason='Maximale looptijd bereikt.',incomplete=True)
    except Exception as error:
        from celery.exceptions import Retry
        if isinstance(error,Retry):
            raise
        if crawl_id:
            with transaction.atomic():
                row=PlaywrightCrawlURL.objects.select_for_update().filter(pk=item_id,status='processing',task_token=token).first()
                if row:
                    row.status='pending' if row.attempts<=row.crawl.configuration['max_retries'] else 'failed'
                    row.error='Crawl-agent kon de bestemming niet verwerken.'
                    row.next_attempt_at=timezone.now()+timedelta(seconds=10*max(1,row.attempts))
                    row.lease_until=None
                    row.save(update_fields=['status','error','next_attempt_at','lease_until'])
    finally:
        if crawl_id:
            dispatch_run(crawl_id)


@shared_task(ignore_result=True)
def recover_crawls():
    now=timezone.now()
    for crawl_id in PlaywrightCrawl.objects.filter(status__in=ACTIVE).values_list('pk',flat=True):
        try:
            crawl=access(crawl_id)
            if crawl.status=='pending':
                enqueue_bootstrap(crawl_id)
            elif crawl.status=='preparing' and crawl.lease_until and crawl.lease_until<now:
                PlaywrightCrawl.objects.filter(pk=crawl_id,status='preparing',run_token=crawl.run_token).update(status='pending')
                enqueue_bootstrap(crawl_id)
            elif crawl.status=='running':
                expired=crawl.urls.filter(status__in=('queued','processing'),lease_until__lt=now)
                expired.filter(attempts__gt=crawl.configuration['max_retries']).update(status='failed',
                    error='Agent herhaaldelijk onderbroken; retrylimiet bereikt.',task_token=None,lease_until=None)
                expired.filter(attempts__lte=crawl.configuration['max_retries']).update(status='pending',task_token=None,lease_until=None)
                dispatch_run(crawl_id)
        except PermissionDenied:
            stop_crawl(crawl_id,reason='Projecttoegang of domeingrenzen gewijzigd.')
        except CrawlExpired:
            stop_crawl(crawl_id,reason='Maximale looptijd bereikt.',incomplete=True)
        except PlaywrightCrawl.DoesNotExist:
            continue
