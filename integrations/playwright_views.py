from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from accounts.models import AuditEvent
from accounts.policy import lock_access_changes
from core.ui import project_context
from projects.services import editable_project
from .models import PlaywrightCrawlSettings
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
    before_subdomains = project.include_subdomains
    project.include_subdomains = form.cleaned_data['include_subdomains']
    if before_subdomains != project.include_subdomains:
        project.save(update_fields=['include_subdomains'])
    settings = settings or PlaywrightCrawlSettings(project=project)
    for name, value in form.cleaned_data.items():
        if name != 'include_subdomains':
            setattr(settings, name, value)
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
        ('Begrenzing', ['max_pages','max_depth','max_duration_minutes','render_timeout_seconds','settle_seconds','max_retries']),
    ]
    context.update(form=form, form_groups=[(label, [form[name] for name in fields]) for label, fields in groups],
                   crawler_settings=settings, crawler_domain=project_domain(context['project']),
                   desktop_profiles=DESKTOP_PROFILES.values())
    return render(request, 'integrations/playwright_settings.html', context)
