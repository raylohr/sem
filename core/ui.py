from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, render
from django.core.paginator import Paginator
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache
from projects.models import Project
from projects.permissions import can_edit, can_manage, role_for
from sources.models import ImportBatch
from integrations.gsc import configured


def project_context(request, project_id, active):
    project = get_object_or_404(Project.objects.visible_to(request.user), pk=project_id)
    return {'project': project, 'active': active, 'role': role_for(request.user, project),
            'can_edit': can_edit(request.user, project), 'can_manage': can_manage(request.user, project)}


@never_cache
@login_required
@require_GET
def dashboard(request):
    projects = Project.objects.visible_to(request.user).annotate(
        batch_count=Count('import_batches', distinct=True),
        active_count=Count('import_batches', filter=Q(import_batches__status__in=['pending','processing']),distinct=True))
    count = projects.count()
    query = request.GET.get('q', '').strip()[:200]
    if query:
        projects = projects.filter(name__icontains=query)
    return render(request, 'ui/dashboard.html', {'projects':projects,'project_count':count,'query':query,'active':'projects'})


@never_cache
@login_required
@require_GET
def project_home(request, project_id):
    context=project_context(request,project_id,'overview')
    project=context['project']
    context.update(stats=project.import_batches.aggregate(total=Count('id'),records=Sum('record_count'),
        running=Count('id',filter=Q(status__in=['pending','processing']))),
        recent=project.import_batches.select_related('created_by')[:8],target_count=project.scan_targets.filter(active=True).count())
    return render(request,'ui/project.html',context)


@never_cache
@login_required
@require_GET
def sources(request, project_id):
    context=project_context(request,project_id,'sources')
    batches=context['project'].import_batches.select_related('created_by')
    source=request.GET.get('source','')
    status=request.GET.get('status','')
    if source in ImportBatch.Source.values:
        batches=batches.filter(source=source)
    if status in ImportBatch.Status.values:
        batches=batches.filter(status=status)
    context.update(page=Paginator(batches,25).get_page(request.GET.get('page')),source=source,status=status,
        source_choices=ImportBatch.Source.choices,status_choices=ImportBatch.Status.choices)
    return render(request,'ui/sources.html',context)


@never_cache
@login_required
@require_GET
def batch(request, project_id, batch_id):
    context=project_context(request,project_id,'sources')
    item=get_object_or_404(ImportBatch.objects.select_related('created_by'),pk=batch_id,project=context['project'])
    from scans.work import stale_scan
    from scans.diagnostics import MESSAGES
    counts = item.request_metadata.get('scan_outcome',{}).get('errors_by_code',{})
    labels = {**MESSAGES,'legacy_http_error':'HTTP-fout (historisch; technische oorzaak niet opgeslagen)', 'scan_error':'Download- of verwerkingsfout'}
    context.update(scan_error_counts=[{'label': labels.get(code, code.replace('http_', 'HTTP ')), 'count': count} for code, count in counts.items()],
        scan_stale=stale_scan(item),batch=item,records=item.records.order_by('id')[:10] if item.status in ('complete','incomplete') else [],
        documents=item.scan_documents.defer('body').order_by('index')[:50],
        url_index_issues=item.records.filter(pk__in=item.url_index_info.get('rejected_record_ids',[])).only('id','locator').order_by('id'))
    return render(request,'ui/batch.html',context)


@never_cache
@login_required
@require_GET
def scans(request, project_id):
    context=project_context(request,project_id,'scans')
    from urllib.parse import urljoin
    from scans.forms import ScanForm
    targets = list(context['project'].scan_targets.all())
    for target in targets:
        target.default_sitemap_url = urljoin(target.url, '/sitemap.xml')
    modes = [
        {'kind':'crawl','title':'Website crawlen','button_label':'Crawl starten','description':'Bezoekt pagina’s en volgt links binnen de toegestane website. Onderzoekt onder meer HTTP-status, titels, canonicals en robots-instructies.',
         'url_label':'Start-URL','depth_label':'Maximale linkdiepte','depth_help':'Aantal linkstappen vanaf de startpagina; maximaal 100.',
         'url_help':'Leeg laten gebruikt de startpagina van deze website.'},
        {'kind':'sitemap','title':'Sitemap inlezen','button_label':'Sitemapimport starten','description':'Leest URL’s en gegevens uit sitemapbestanden en onderliggende sitemapindexen. De gevonden webpagina’s worden hierbij niet opgehaald.',
         'url_label':'Sitemap-URL','depth_label':'Maximale sitemapdiepte','depth_help':'Aantal niveaus met onderliggende sitemapindexen; maximaal 100.',
         'url_help':'Leeg laten gebruikt /sitemap.xml op deze website. Een andere locatie kun je hier invullen.'},
    ]
    context.update(targets=targets,scan_modes=modes,
        scan_defaults={name:ScanForm.base_fields[name].initial for name in ('max_urls','max_depth','robots_mode','concurrency','requests_per_second')},
        recent=context['project'].import_batches.filter(method='scan')[:10])
    return render(request,'ui/scans.html',context)


@never_cache
@login_required
@require_GET
def connections(request, project_id):
    context=project_context(request,project_id,'connections')
    context.update(connections=context['project'].gsc_connections.defer('encrypted_refresh_token'),gsc_configured=configured())
    return render(request,'ui/connections.html',context)


@never_cache
@login_required
@require_GET
def source_record(request,project_id,batch_id,record_id):
    from sources.models import SourceRecord
    context=project_context(request,project_id,'sources')
    batch=get_object_or_404(ImportBatch.objects.select_related('created_by'),pk=batch_id,project=context['project'],status__in=('complete','incomplete'))
    record=get_object_or_404(SourceRecord,pk=record_id,batch=batch)
    context.update(batch=batch,record=record)
    return render(request,'ui/source_record.html',context)
