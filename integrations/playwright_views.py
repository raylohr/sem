from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count
from django.shortcuts import redirect, render, get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_GET

from accounts.models import AuditEvent
from accounts.policy import lock_access_changes
from core.ui import project_context
from projects.services import editable_project
from .models import PlaywrightCrawlSettings, PlaywrightCrawl
from .crawl_service import ACTIVE, start_crawl, control_crawl, CrawlExpired
from .crawl_schedule import configure_schedule
from .playwright_config import DESKTOP_PROFILES, project_domain
from .playwright_forms import PlaywrightCrawlForm


@transaction.atomic
def save_configuration(*, actor, project_id, data):
    lock_access_changes()
    project = editable_project(actor, project_id)
    settings = PlaywrightCrawlSettings.objects.select_for_update().filter(project=project).first()
    form = PlaywrightCrawlForm(data, project=project, instance=settings)
    if not form.is_valid():
        return None, form
    before_schedule={key:getattr(settings,key) if settings else None for key in
        ('schedule_enabled','schedule_frequency','schedule_time','schedule_weekday')}
    before_subdomains = project.include_subdomains
    project.include_subdomains = form.cleaned_data['include_subdomains']
    if before_subdomains != project.include_subdomains:
        project.save(update_fields=['include_subdomains'])
    settings = settings or PlaywrightCrawlSettings(project=project)
    for name, value in form.cleaned_data.items():
        if name != 'include_subdomains':
            setattr(settings, name, value)
    configure_schedule(settings,actor,before_schedule)
    settings.updated_by = actor
    settings.full_clean()
    settings.save()
    AuditEvent.objects.create(actor=actor, project=project, action=AuditEvent.Action.PROJECT_UPDATED,
        details={'area':'playwright_configuration', 'configuration_id':settings.pk,
                 'include_subdomains_before':before_subdomains,
                 'include_subdomains_after':project.include_subdomains})
    return settings, form


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def configuration(request, project_id):
    context = project_context(request, project_id, 'connections')
    if request.method == 'POST' and not context['can_manage']:
        raise PermissionDenied
    settings = PlaywrightCrawlSettings.objects.filter(project=context['project']).first()
    form = PlaywrightCrawlForm(project=context['project'], instance=settings)
    if request.method == 'POST':
        saved, form = save_configuration(actor=request.user, project_id=project_id, data=request.POST)
        if saved:
            messages.success(request, 'Playwright-crawlinstellingen opgeslagen.')
            return redirect('ui:playwright-settings', project_id=project_id)
    if not context['can_manage']:
        for field in form.fields.values():
            field.disabled = True
    groups = [
        ('Bereik en URL’s', ['include_subdomains','discover_urls','use_sitemaps','respect_robots',
                            'start_urls','sitemap_urls','excluded_hosts','excluded_paths']),
        ('Desktopprofiel', ['desktop_profile','scroll_for_lazy_content']),
        ('Snelheid en paralleliteit', ['pages_per_second','parallel_agents','slow_after_seconds','slow_pages_per_second']),
        ('Herhaling', ['schedule_enabled','schedule_frequency','schedule_time','schedule_weekday']),
        ('Begrenzing', ['max_pages','max_depth','max_duration_minutes','render_timeout_seconds','settle_seconds','max_retries']),
    ]
    context.update(form=form, form_groups=[(label, [form[name] for name in fields]) for label, fields in groups],
                   crawler_settings=settings, crawler_domain=project_domain(context['project']),
                   desktop_profiles=DESKTOP_PROFILES.values())
    context.update(crawl_context(context['project']))
    return render(request, 'integrations/playwright_settings.html', context)


def crawl_context(project):
    current=project.playwright_crawls.filter(status__in=ACTIVE).select_related('batch').first()
    latest=project.playwright_crawls.select_related('batch').first()
    run=current or latest
    counts={'page':{},'external':{}}
    issues=[]
    if run:
        for item in run.urls.values('kind','status').annotate(total=Count('id')):
            counts[item['kind']][item['status']]=item['total']
        for kind in counts:
            counts[kind]['total']=sum(counts[kind].values())
            counts[kind]['done']=sum(counts[kind].get(state,0) for state in ('complete','failed','skipped'))
        issues=list(run.urls.exclude(error='').order_by('-pk').values('url','error')[:8])
    return {'crawl_run':run,'active_crawl':current,'crawl_counts':counts,'crawl_issues':issues,
            'crawl_history':project.playwright_crawls.select_related('batch','created_by')[:10]}


@never_cache
@login_required
@require_GET
def crawl_status(request,project_id):
    context=project_context(request,project_id,'connections')
    context.update(crawl_context(context['project']))
    context['crawler_settings']=PlaywrightCrawlSettings.objects.filter(project=context['project']).first()
    return render(request,'integrations/playwright_status.html',context)


@never_cache
@login_required
@require_POST
def crawl_action(request,project_id):
    context=project_context(request,project_id,'connections')
    if not context['can_manage']:
        raise PermissionDenied
    action=request.POST.get('action')
    try:
        if action=='start':
            start_crawl(actor=request.user,project_id=project_id)
            messages.success(request,'Crawl gestart met de opgeslagen instellingen.')
        elif action in ('pause','resume','stop'):
            crawl=get_object_or_404(PlaywrightCrawl,pk=request.POST.get('crawl_id'),project=context['project'])
            control_crawl(actor=request.user,project_id=project_id,crawl_id=crawl.pk,action=action)
            messages.success(request,{'pause':'Crawl gepauzeerd.','resume':'Crawl hervat.',
                'stop':'Crawl gestopt; beschikbare resultaten worden als onvolledig bewaard.'}[action])
        else:
            messages.error(request,'Onbekende crawlactie.')
    except ValidationError as error:
        messages.error(request,' '.join(error.messages))
    except CrawlExpired:
        messages.error(request,'Maximale looptijd bereikt. Stop deze crawl en start eventueel een nieuwe.')
    return redirect('ui:playwright-settings',project_id=project_id)
