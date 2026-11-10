"""Replay retained discoveries and seeds into the same live crawl, without re-rendering pages."""
from datetime import timedelta
from uuid import uuid4
from urllib.parse import urlsplit

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.models import AuditEvent, User
from projects.permissions import can_manage
from accounts.policy import lock_access_changes
from projects.services import editable_project
from sources.models import SourceRecord
from sources.inventory_index import url_expression
from .models import PlaywrightCrawl
from .crawl_service import access, add_urls, dispatch_run, page_allowed
from .crawl_http import SafeHTTP, RobotsRules, sitemap_entries, CrawlNetworkError
from .crawl_renderer import desktop_user_agent, manifest_browser_version
from .playwright_config import DESKTOP_PROFILES


def recover_urls(*,actor,crawl_id,progress=None):
    from django.conf import settings
    from .crawl_tasks import metadata_pause,robots_for
    token=str(uuid4())
    with transaction.atomic():
        lock_access_changes()
        project_id=PlaywrightCrawl.objects.values_list('project_id',flat=True).get(pk=crawl_id)
        project=editable_project(actor,project_id)
        crawl=PlaywrightCrawl.objects.select_for_update().get(pk=crawl_id)
        access(crawl.pk)
        if crawl.status!='running':
            raise ValidationError('URL-herstel vereist een lopende crawl.')
        old=crawl.configuration.get('discovery_recovery',{})
        until=parse_datetime(old.get('until','')) if old.get('until') else None
        if until and until>timezone.now():
            raise ValidationError('Er loopt al een URL-herstel.')
        crawl.configuration={**crawl.configuration,'discovery_recovery':{
            'token':token,'until':(timezone.now()+timedelta(hours=1)).isoformat()}}
        crawl.save(update_fields=['configuration'])
        maximum_pk=crawl.urls.aggregate(last=Max('pk'))['last'] or 0
    counts={'added':0,'pages_replayed':0,'sitemaps_read':0,'sitemap_failures':0}
    succeeded=False

    def guard():
        current=access(crawl_id)
        if not can_manage(User.objects.get(pk=actor.pk),current.project):
            raise PermissionDenied
        if current.configuration.get('discovery_recovery',{}).get('token')!=token:
            raise PermissionDenied
        return current

    def add(values,**kwargs):
        chunk=[]
        for url in values:
            if not isinstance(url,str):
                continue
            chunk.append(url)
            if len(chunk)==250:
                guard()
                counts['added']+=add_urls(crawl_id,chunk,**kwargs)
                chunk=[]
        if chunk:
            guard()
            counts['added']+=add_urls(crawl_id,chunk,**kwargs)

    try:
        rows=crawl.urls.filter(kind='page',pk__lte=maximum_pk).values('url','depth','metadata')
        for row in rows.iterator(chunk_size=100):
            links=row['metadata'].get('links',[])
            blocked=row['metadata'].get('blocked_navigation')
            if blocked:
                links=[*links,blocked]
            if crawl.configuration['discover_urls']:
                add(links,depth=row['depth']+1,discovered_from=row['url'],provenance='recovered_links')
            elif blocked:
                add([blocked],depth=row['depth'],discovered_from=row['url'],provenance='recovered_redirect')
            counts['pages_replayed']+=1
            if progress and counts['pages_replayed']%500==0:
                progress(dict(counts))
        add([crawl.configuration['website'],*crawl.configuration['start_urls']],provenance='recovered_seed')
        batch_ids=[batch.pk for batch in project.import_batches.filter(status__in=('complete','incomplete'))
                   if batch.request_metadata.get('record_scope') not in ('summary','external')]
        seeds=SourceRecord.objects.filter(batch_id__in=batch_ids).annotate(seed_url=url_expression()).values_list('seed_url',flat=True).distinct()
        add(seeds.iterator(chunk_size=500),provenance='recovered_inventory')
        if crawl.configuration['use_sitemaps']:
            origin=urlsplit(crawl.configuration['website'])
            robot=robots_for(crawl_id,crawl.configuration['website'])
            known=list(crawl.urls.filter(provenance__in=('sitemap','recovered_sitemap')).exclude(discovered_from='').values_list('discovered_from',flat=True).distinct())
            pending=list(dict.fromkeys([*crawl.configuration['sitemap_urls'],*RobotsRules(robot.get('text','')).sitemaps,
                                       *known,f'{origin.scheme}://{origin.netloc}/sitemap.xml']))
            seen=set()
            profile=DESKTOP_PROFILES[crawl.profile]
            http=SafeHTTP(settings.CRAWLER_EGRESS_PROXY,desktop_user_agent(profile,manifest_browser_version(profile.engine)),guard)
            while pending and len(seen)<100:
                url=pending.pop(0)
                if url in seen or not page_allowed(crawl,url):
                    continue
                seen.add(url)
                guard()
                metadata_pause(crawl_id)
                try:
                    response=http.fetch(url,allowed=lambda value:page_allowed(crawl,value))
                    if response.status!=200:
                        raise CrawlNetworkError('Sitemap niet beschikbaar.')
                    kind,values=sitemap_entries(response.body)
                    counts['sitemaps_read']+=1
                    if kind=='sitemapindex':
                        pending.extend(values[:100])
                    else:
                        add(values,provenance='recovered_sitemap',discovered_from=url)
                except CrawlNetworkError:
                    counts['sitemap_failures']+=1
            if pending:
                counts['sitemap_failures']+=1
        guard()
        succeeded=True
        return counts
    finally:
        with transaction.atomic():
            current=PlaywrightCrawl.objects.select_for_update().get(pk=crawl_id)
            if current.configuration.get('discovery_recovery',{}).get('token')==token:
                current.configuration={key:value for key,value in current.configuration.items() if key!='discovery_recovery'}
                result={'type':'discovery_recovery','at':timezone.now().isoformat(),'completed':succeeded,**counts}
                current.notes=[*current.notes,result]
                current.save(update_fields=['configuration','notes'])
                if current.batch_id:
                    batch=current.batch
                    batch.request_metadata={**batch.request_metadata,'discovery_recoveries':[
                        *batch.request_metadata.get('discovery_recoveries',[]),result]}
                    batch.save(update_fields=['request_metadata'])
                AuditEvent.objects.create(actor=actor,project_id=current.project_id,action='project_updated',
                    details={'area':'playwright_crawl','action':'recover_discovery','crawl_id':str(crawl_id),**counts,'completed':succeeded})
        dispatch_run(crawl_id)
