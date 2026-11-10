"""Durable crawl queue, scope snapshots and shared per-domain budgets."""
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4
from urllib.parse import urlsplit

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.policy import lock_access_changes
from projects.hosts import normalize_domain
from projects.permissions import can_manage
from projects.services import editable_project
from .models import PlaywrightCrawl, PlaywrightCrawlURL, PlaywrightCrawlSettings, PlaywrightDomainBudget
from .playwright_config import choose_desktop_profile, in_scope, is_excluded, project_domain
from .crawl_http import safe_http_url

ACTIVE = ('pending','preparing','running','paused')
BUSY = ('queued','processing')
SETTING_FIELDS = ('respect_robots','use_sitemaps','discover_urls','start_urls','sitemap_urls',
    'excluded_hosts','excluded_paths','desktop_profile','pages_per_second','parallel_agents',
    'slow_after_seconds','slow_pages_per_second','max_pages','max_depth','max_duration_minutes',
    'render_timeout_seconds','settle_seconds','max_retries','scroll_for_lazy_content')


class CrawlPaused(Exception):
    pass


class CrawlExpired(Exception):
    pass


def scope(crawl):
    return SimpleNamespace(domain=crawl.domain,website=crawl.configuration['website'],
                           include_subdomains=crawl.configuration['include_subdomains'])


def page_allowed(crawl,url):
    return in_scope(scope(crawl),url) and not is_excluded(url,
        crawl.configuration.get('excluded_hosts',[]),crawl.configuration.get('excluded_paths',[]))


def access(crawl_id, *, token=None):
    crawl = PlaywrightCrawl.objects.select_related('project','created_by').get(pk=crawl_id)
    if (crawl.status not in ACTIVE or not can_manage(crawl.created_by,crawl.project)
            or crawl.configuration.get('archive_version') and not crawl.batch_id
            or project_domain(crawl.project)!=crawl.domain
            or crawl.project.include_subdomains!=crawl.configuration['include_subdomains']
            or token is not None and crawl.run_token!=token):
        raise PermissionDenied
    if crawl.status=='paused':
        raise CrawlPaused
    return crawl


@transaction.atomic
def start_crawl(*, actor, project_id, overrides=None):
    lock_access_changes()
    project = editable_project(actor,project_id)
    settings = PlaywrightCrawlSettings.objects.filter(project=project).first()
    if not settings:
        raise ValidationError('Sla eerst de Playwright-crawlinstellingen op.')
    for key,value in (overrides or {}).items():
        if key not in ('max_pages','max_depth','max_duration_minutes'):
            raise ValidationError('Onbekende crawlbegrenzing.')
        setattr(settings,key,value)
    settings.full_clean()
    if PlaywrightCrawl.objects.filter(project=project,status__in=ACTIVE).exists():
        raise ValidationError('Er loopt al een crawl voor dit project.')
    configuration = {name: (str(value) if isinstance(value,Decimal) else value)
                     for name in SETTING_FIELDS for value in [getattr(settings,name)]}
    configuration.update(archive_version=1,domain=project_domain(project),website=project.website,include_subdomains=project.include_subdomains)
    crawl = PlaywrightCrawl.objects.create(project=project,created_by=actor,domain=project_domain(project),
        configuration=configuration,profile=choose_desktop_profile(settings.desktop_profile).key,
        current_agents=settings.parallel_agents)
    from sources.models import ImportBatch
    crawl.batch=ImportBatch.objects.create(project=project,created_by=actor,source='playwright',method='crawl',
        label='Playwright-crawl',status='processing',parser_version='playwright-v1',
        request_metadata={'type':'playwright_pages','record_scope':'url','crawl_id':str(crawl.pk),
                          'profile':crawl.profile,'configuration':configuration})
    crawl.save(update_fields=['batch'])
    from .crawl_tasks import enqueue_bootstrap
    transaction.on_commit(lambda:enqueue_bootstrap(crawl.pk))
    from accounts.models import AuditEvent
    AuditEvent.objects.create(actor=actor,project=project,action=AuditEvent.Action.PROJECT_UPDATED,
        details={'area':'playwright_crawl','crawl_id':str(crawl.pk),'action':'start'})
    return crawl


@transaction.atomic
def stop_crawl(crawl_id, *, reason='Gestopt.', incomplete=False):
    lock_access_changes()
    crawl = PlaywrightCrawl.objects.select_for_update().filter(pk=crawl_id,status__in=ACTIVE).first()
    if not crawl:
        return
    crawl.status = 'incomplete' if incomplete else 'cancelled'
    crawl.finished_at = timezone.now()
    crawl.error = reason
    crawl.urls.filter(status__in=('pending','queued','processing')).update(status='skipped',error=reason,lease_until=None)
    crawl.save(update_fields=['status','finished_at','error'])


@transaction.atomic
def add_urls(crawl_id, values, *, run_token=None, depth=0, discovered_from='', provenance='inventory', kind='page'):
    crawl = PlaywrightCrawl.objects.select_for_update().get(pk=crawl_id)
    access(crawl_id,token=run_token)
    candidates = {}
    for value in values:
        try:
            url = safe_http_url(value)
        except Exception:
            continue
        if kind=='page' and not page_allowed(crawl,url) or kind=='external' and in_scope(scope(crawl),url):
            continue
        digest = sha256(url.encode()).hexdigest()
        candidates[digest] = url
        if len(candidates)>=1000:
            break
    existing = set(crawl.urls.filter(kind=kind,url_hash__in=candidates).values_list('url_hash',flat=True))
    pending = [(key,url) for key,url in candidates.items() if key not in existing]
    # Page count and total duration are warning thresholds, never discovery filters.
    remaining=len(pending) if kind=='page' else max(0,1000-crawl.urls.filter(kind=kind).exclude(status='skipped').count())
    over_depth=kind=='page' and depth>crawl.configuration['max_depth']
    if over_depth or len(pending)>remaining:
        crawl.limit_reached=True
        crawl.save(update_fields=['limit_reached'])
    rows=[]
    for index,(key,url) in enumerate(pending):
        skipped=over_depth or index>=remaining
        error=('Maximale linkdiepte bereikt; URL bewaard.' if over_depth else
               'Bereikbaarheidslimiet externe links bereikt; URL bewaard.' if skipped else '')
        rows.append(PlaywrightCrawlURL(crawl=crawl,url=url,url_hash=key,kind=kind,depth=depth,
            discovered_from=discovered_from,provenance=provenance,status='skipped' if skipped else 'pending',error=error))
    crawl.urls.bulk_create(rows,batch_size=100,ignore_conflicts=True)
    return len(pending)



@transaction.atomic
def dispatch_run(crawl_id):
    lock_access_changes()
    crawl = PlaywrightCrawl.objects.select_for_update().filter(pk=crawl_id,status='running').first()
    if not crawl:
        return
    try:
        access(crawl_id)
    except PermissionDenied:
        stop_crawl(crawl_id,reason='Projecttoegang of domeingrenzen gewijzigd.')
        return
    except CrawlExpired:
        stop_crawl(crawl_id,reason='Maximale looptijd bereikt.',incomplete=True)
        return
    busy = crawl.urls.filter(status__in=BUSY).count()
    slots = max(0,crawl.current_agents-busy)
    now = timezone.now()
    pending = crawl.urls.filter(status='pending').filter(Q(next_attempt_at__isnull=True)|Q(next_attempt_at__lte=now))
    selected = list(pending.filter(kind='page').order_by('depth','pk')[:slots])
    if not selected:
        selected = list(pending.filter(kind='external').order_by('pk')[:slots])
    from .crawl_tasks import enqueue_url
    for item in selected:
        item.status='queued'
        item.task_token=uuid4()
        item.lease_until=now+timedelta(minutes=4)
        item.save(update_fields=['status','task_token','lease_until'])
        transaction.on_commit(lambda pk=item.pk,token=item.task_token:enqueue_url(pk,token))
    if not crawl.urls.filter(status__in=('pending','queued','processing')).exists():
        from django.utils.dateparse import parse_datetime
        recovery=crawl.configuration.get('discovery_recovery',{})
        lease=parse_datetime(recovery.get('until','')) if recovery.get('until') else None
        if lease and lease>now:
            return
        crawl.status = 'incomplete' if crawl.limit_reached or any(note.get('type')=='sitemap_missing' for note in crawl.notes) or crawl.urls.filter(status='failed').exists() else 'complete'
        crawl.finished_at = now
        crawl.save(update_fields=['status','finished_at'])


@transaction.atomic
def acquire_slot(item_id, token):
    item = PlaywrightCrawlURL.objects.select_related('crawl').get(pk=item_id)
    crawl = PlaywrightCrawl.objects.select_for_update().get(pk=item.crawl_id)
    access(crawl.pk)
    item = PlaywrightCrawlURL.objects.select_for_update().get(pk=item_id)
    if item.status!='queued' or str(item.task_token)!=str(token):
        raise PermissionDenied
    key = crawl.domain if item.kind=='page' else urlsplit(item.url).hostname
    budget,_ = PlaywrightDomainBudget.objects.get_or_create(domain=key)
    budget = PlaywrightDomainBudget.objects.select_for_update().get(pk=key)
    now = timezone.now()
    if item.kind=='page':
        configurations = list(PlaywrightCrawl.objects.filter(domain=crawl.domain,status='running').values_list('configuration',flat=True))
        base = min(Decimal(config['pages_per_second']) for config in configurations)
        agents = min(config['parallel_agents'] for config in configurations)
        active = PlaywrightCrawlURL.objects.filter(crawl__domain=crawl.domain,crawl__status='running',kind='page',status='processing').count()
        if active>=min(agents,crawl.current_agents):
            return 1.0
    else:
        base = Decimal('1')
    if now-budget.updated_at>timedelta(minutes=10):
        budget.rate_factor=Decimal('1')
        budget.slow_until=None
    if budget.next_start_at and budget.next_start_at>now:
        return min(30,max(.05,(budget.next_start_at-now).total_seconds()))
    rate = max(Decimal('0.05'),base*budget.rate_factor)
    budget.next_start_at=now+timedelta(seconds=float(1/rate))
    budget.save()
    item.status='processing'
    item.attempts+=1
    item.lease_until=now+timedelta(minutes=4)
    item.save(update_fields=['status','attempts','lease_until'])
    return 0


@transaction.atomic
def report_health(crawl_id, *, slow=False, failed=False, retry_after=0):
    crawl = PlaywrightCrawl.objects.select_for_update().get(pk=crawl_id)
    budget,_ = PlaywrightDomainBudget.objects.get_or_create(domain=crawl.domain)
    budget = PlaywrightDomainBudget.objects.select_for_update().get(pk=crawl.domain)
    now = timezone.now()
    if slow or failed:
        factor = Decimal(crawl.configuration['slow_pages_per_second'])/Decimal(crawl.configuration['pages_per_second'])
        budget.rate_factor=min(budget.rate_factor,factor)
        budget.slow_until=now+timedelta(seconds=max(60,retry_after))
        budget.next_start_at=max(budget.next_start_at or now,budget.slow_until if retry_after else now)
        budget.healthy_samples=0
        budget.failures=min(1000,budget.failures+1)
        crawl.current_agents=max(1,crawl.current_agents//2)
        crawl.save(update_fields=['current_agents'])
    elif not budget.slow_until or now>=budget.slow_until:
        budget.healthy_samples+=1
        if budget.healthy_samples>=3:
            budget.rate_factor=min(Decimal('1'),budget.rate_factor*2)
            budget.healthy_samples=0
            budget.failures=0
            crawl.current_agents=min(crawl.configuration['parallel_agents'],crawl.current_agents+1)
            crawl.save(update_fields=['current_agents'])
    budget.save()


@transaction.atomic
def control_crawl(*,actor,project_id,crawl_id,action):
    from django.db.models import F
    from accounts.models import AuditEvent
    lock_access_changes()
    project=editable_project(actor,project_id)
    crawl=PlaywrightCrawl.objects.select_for_update().get(pk=crawl_id,project=project)
    if action=='stop':
        stop_crawl(crawl.pk,reason='Gestopt door projectbeheerder; beschikbare resultaten bewaard.',incomplete=True)
    elif action=='pause':
        if crawl.status=='paused':
            return crawl
        if crawl.status not in ACTIVE:
            raise ValidationError('Deze crawl is al afgerond.')
        access(crawl.pk)
        crawl.resume_status='running' if crawl.status=='running' else 'pending'
        crawl.status='paused'
        crawl.paused_at=timezone.now()
        crawl.run_token=uuid4()
        crawl.lease_until=None
        crawl.urls.filter(status='processing',attempts__gt=0).update(attempts=F('attempts')-1)
        crawl.urls.filter(status__in=BUSY).update(status='pending',task_token=None,lease_until=None,next_attempt_at=None)
        crawl.save(update_fields=['status','paused_at','resume_status','run_token','lease_until'])
    elif action=='resume':
        if crawl.status!='paused':
            raise ValidationError('Alleen een gepauzeerde crawl kan worden hervat.')
        # Recheck the original crawl actor and scope before allowing more network work.
        try:
            access(crawl.pk)
        except CrawlPaused:
            pass
        crawl.paused_seconds+=max(0,int((timezone.now()-crawl.paused_at).total_seconds())) if crawl.paused_at and crawl.started_at else 0
        crawl.paused_at=None
        crawl.status=crawl.resume_status
        crawl.run_token=uuid4()
        crawl.save(update_fields=['status','paused_seconds','paused_at','run_token'])
        from .crawl_tasks import enqueue_bootstrap
        if crawl.status=='pending':
            transaction.on_commit(lambda:enqueue_bootstrap(crawl.pk))
        else:
            transaction.on_commit(lambda:dispatch_run(crawl.pk))
    else:
        raise ValidationError('Onbekende crawlactie.')
    AuditEvent.objects.create(actor=actor,project=project,action=AuditEvent.Action.PROJECT_UPDATED,
        details={'area':'playwright_crawl','crawl_id':str(crawl.pk),'action':action})
    return crawl
