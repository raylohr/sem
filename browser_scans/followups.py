from scans.host_rules import navigation_hosts
import hashlib
import json
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404,redirect
from django.views.decorators.http import require_POST
from core.ui import project_context
from projects.models import Project
from scans.models import ScanRetryTarget
from scans.network import normalize_url,ScanError
from scans.scope import host_scope
from sources.models import ImportBatch
from .models import BrowserRun


@login_required
@require_POST
def start(request,project_id,run_id):
    context=project_context(request,project_id,'assessments')
    if not context['can_edit']:raise PermissionDenied
    with transaction.atomic():
        project=Project.objects.select_for_update().get(pk=project_id)
        from projects.permissions import can_edit
        if not can_edit(request.user,project):raise PermissionDenied
        run=get_object_or_404(BrowserRun,pk=run_id,batch__project=context['project'],batch__status__in=('complete','incomplete'))
        target=run.batch.scan_target
        if not target or not target.active or target.project_id!=project.pk:raise PermissionDenied
        existing=ImportBatch.objects.filter(project=context['project'],request_metadata__browser_followup_run=str(run.pk),status__in=('pending','processing')).first()
        if existing:return redirect('ui:batch',project_id=project_id,batch_id=existing.pk)
        scope=host_scope(target);selected={}
        measured=set(run.source_batch.url_observations.values_list('url__url_hash',flat=True)) if run.source_batch_id else set()
        from .models import DiscoveredLink
        measured.update(DiscoveredLink.objects.filter(visit__run=run,followup__isnull=False).values_list('url_hash',flat=True))
        for link in run.visits.filter(scenario='baseline').values_list('id','source_record_id','url'):
            visit_id,record_id,source_url=link
            if not record_id:continue
            from .models import DiscoveredLink
            for candidate in DiscoveredLink.objects.filter(visit_id=visit_id,followup=None,state__in=('budget','queued_browser','already_selected')).order_by('id'):
                if candidate.url_hash in measured:continue
                try:url=normalize_url(candidate.url,navigation_hosts(scope))
                except ScanError:continue
                selected.setdefault(url,(record_id,candidate,source_url))
                if len(selected)>=1000:break
            if len(selected)>=1000:break
        if not selected:return redirect('ui:browser:run',project_id=project_id,run_id=run.pk)
        urls=list(selected)
        config={**scope,'scan':'crawl','url':urls[0],'retry_only':True,'browser_followup_run':str(run.pk),
            'discover_sitemaps':False,'reliability_version':'page-retry-v1','max_urls':len(urls),'max_depth':0,
            'duration':min(604800,max(120,len(urls)*180)),'max_transfer':max(25*1024**2,len(urls)*15*1024**2),
            'max_attempts':3,'connect_timeout':10,'read_timeout':30,'respect_robots':run.config.get('respect_robots',True),
            'requests_per_second':run.config.get('requests_per_second',1),'concurrency':1,'browser_phase':False}
        batch=ImportBatch.objects.create(project=context['project'],created_by=request.user,scan_target=target,source='semscan',method='scan',
            label='HTML-vervolgmeting van browserlinks',parser_version='scan-v3',request_metadata=config)
        ScanRetryTarget.objects.bulk_create([ScanRetryTarget(batch=batch,source_record_id=selected[url][0],url=url,url_hash=hashlib.sha256(url.encode()).hexdigest(),
            snapshot={'source_browser_visit':selected[url][1].visit_id,'source_url':selected[url][2],'discovered_phase':selected[url][1].phase}) for url in urls],batch_size=500)
        from .models import DiscoveredLink
        DiscoveredLink.objects.filter(visit__run=run,visit__scenario='baseline',url_hash__in=[row[1].url_hash for row in selected.values()]).update(followup=batch)
        from scans.tasks import enqueue_scan
        transaction.on_commit(lambda:enqueue_scan(batch.pk))
    return redirect('ui:batch',project_id=project_id,batch_id=batch.pk)


def annotate(work):
    if not work.get('browser_followup_run'):return
    with work.connection:
        for pk,payload in work.connection.execute('SELECT id,payload FROM records').fetchall():
            record=json.loads(payload);data=record['data']
            original=work.connection.execute('SELECT payload FROM retry_origins WHERE url=?',(data['url'],)).fetchone()
            data.update(discovery_sources=['rendered_dom'],browser_followup_run=work.get('browser_followup_run'),
                browser_discovery=json.loads(original[0]) if original else {},link_depth_basis='targeted HTTP follow-up from observed browser links; no website graph expansion')
            work.connection.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(record,ensure_ascii=False),pk))
