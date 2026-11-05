"""Project-authorized workflow assembly; no source bodies or raw URLs in live output."""
import json
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit
from django.conf import settings
from django.db.models import Count,Max
from browser_scans.models import BrowserRun
from . import activity
from .work import work_paths

ORDER=('pages','files','external','analysis','browser','browser_analysis','enrichment')
LABELS={**activity.PHASES,'enrichment':'Technologie & privacy onderzoeken'}
TERMINAL={'complete','partial','failed','cancelled','skipped'}


def checkpoint_state(batch):
    path=Path(settings.MEDIA_ROOT)/work_paths(batch.pk)[0]
    if not path.exists():return {}
    c=None
    try:
        c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.1)
        return {key:json.loads(value) for key,value in c.execute("SELECT key,payload FROM metadata WHERE key IN ('resource_progress','page_phase_done','seed_maps_done','resources_done')")}
    except (sqlite3.Error,OSError):return {}
    finally:
        if c:c.close()


def assemble(batch,live):
    now=time.time();config=batch.request_metadata;is_browser=batch.scan_kind=='browser'
    root=activity.read(batch.pk,now) or {};state=checkpoint_state(batch) if batch.status in ('pending','processing') else {}
    phases={p['name']:p for p in root.get('phases',[])}
    series=list(root.get('series',[]));totals=list(root.get('totals',[]));requests=list(root.get('requests',[]))
    root_active=batch.status in ('pending','processing')
    failed=batch.status in ('failed','cancelled')
    def ensure(name,status,done=0,total=None,**kwargs):
        item=phases.setdefault(name,{'name':name,'label':LABELS[name],'status':status,'done':done,'total':total,'eta_seconds':None,'updated':None})
        item.update(kwargs)
        return item
    if not is_browser:
        finished=bool(state.get('page_phase_done')) or not root_active
        ensure('pages','complete' if finished else 'running',live['records'],live['records']+live.get('pending',0))
        resource=state.get('resource_progress') or config.get('resource_scan') or config.get('scan_progress',{}).get('resources',{})
        for name,purpose,enabled in [('files','resource',config.get('check_resources',False)),('external','external',config.get('check_external_links',False))]:
            counts=resource.get('outcomes',{}).get(purpose,{})
            total=sum(counts.values()) if counts else None;done=sum(n for k,n in counts.items() if k!='queued')
            ready=finished and (name=='files' or phases.get('files',{}).get('status') in TERMINAL)
            status='skipped' if not enabled else 'complete' if not root_active or state.get('resources_done') or counts and not counts.get('queued') else 'running' if ready else 'queued'
            ensure(name,status,done,total)
            if counts:phases[name]['outcomes']={
                'success':counts.get('success',0),'http_error':counts.get('http_error',0),'blocked':counts.get('blocked',0),
                'unknown':sum(n for k,n in counts.items() if k not in ('success','http_error','blocked','queued'))}
        if batch.scan_kind=='sitemap':phases['pages']['label']='Sitemaps inlezen'
        analysis=batch.analysis_runs.filter(definition__key='crawl-basics').order_by('-started_at').first()
        if batch.scan_kind=='crawl':ensure('analysis','complete' if analysis and analysis.status=='complete' else 'running' if analysis else 'queued' if root_active else 'skipped',batch.record_count if analysis and analysis.status=='complete' else 0,batch.record_count or None)
    browser=BrowserRun.objects.filter(batch=batch).first() if is_browser else BrowserRun.objects.filter(source_batch=batch,batch__project_id=batch.project_id).select_related('batch').order_by('-started_at','-pk').first()
    if browser:
        child=activity.read(browser.batch_id,now) or {}
        if not is_browser:
            phases.update({p['name']:p for p in child.get('phases',[])})
            series.extend(child.get('series',[]));totals.extend(child.get('totals',[]));requests.extend(child.get('requests',[]))
        counts={row['status']:row['n'] for row in browser.visits.values('status').annotate(n=Count('id'))}
        done=sum(n for k,n in counts.items() if k not in ('pending','processing'));total=sum(counts.values())
        item=ensure('browser','running' if browser.batch.status in ('pending','processing') else 'complete',done,total)
        item.update(done=done,total=total,outcomes={'success':counts.get('complete',0),'unknown':counts.get('incomplete',0)+counts.get('failed',0)})
        item['updated']=max(item.get('updated') or 0,(browser.completed_at or browser.started_at).timestamp() if browser.completed_at or browser.started_at else 0)
        if browser.started_at:item.setdefault('started',browser.started_at.timestamp())
        if browser.batch.status in TERMINAL:item['status']='complete' if browser.batch.status in ('complete','incomplete') else browser.batch.status
        if not requests:
            visit=browser.visits.filter(status='processing').values('url','coverage').first()
            if visit:
                stamp=visit['coverage'].get('progress_started_at')
                requests.append({'phase':'browser','host':urlsplit(visit['url']).hostname,'state':'rendering','waiting_seconds':int(now-stamp) if stamp else None})
        browser_busy=browser.batch.status in ('pending','processing')
        analysis=browser.batch.analysis_runs.filter(definition__key='browser-basics').order_by('-started_at').first()
        ensure('browser_analysis','complete' if analysis and analysis.status=='complete' else 'queued' if browser_busy else 'skipped',total if analysis and analysis.status=='complete' else 0,total)
        if browser_busy and done==total:
            phases['browser_analysis']['status']='running'
        from site_intelligence.models import EnrichmentRun
        enrichment=EnrichmentRun.objects.filter(batch_id=browser.batch_id).order_by('-started_at').first()
        if enrichment:
            domains=list(enrichment.domains.values('coverage','measured_at'))
            finished=sum(d['coverage'].get('status')!='pending' for d in domains)
            entry=ensure('enrichment',enrichment.status,finished,len(domains) or None)
            entry.update(status='running' if enrichment.status=='processing' else 'queued' if enrichment.status=='pending' else enrichment.status,done=finished,total=len(domains) or None)
            if enrichment.started_at:entry['started']=enrichment.started_at.timestamp()
            entry['updated']=max((d['measured_at'].timestamp() for d in domains if d['measured_at']),default=None)
        elif browser.config.get('progress_version')=='workflow-v1' and browser.batch.status in ('complete','incomplete'):
            grace=bool(browser.completed_at and now-browser.completed_at.timestamp()<120)
            ensure('enrichment','queued' if grace else 'failed')
    elif not is_browser and config.get('browser_phase') and not config.get('retry_only'):
        state_name=config.get('browser_phase_status')
        if config.get('browser_followup_id'):state_name='failed'
        ensure('browser','failed' if state_name=='failed' else 'skipped' if str(state_name).startswith('skipped') else 'queued',0,config.get('browser_max_pages'))
        ensure('browser_analysis','skipped' if phases['browser']['status'] in ('failed','skipped') else 'queued')
    for name,item in phases.items():
        if failed and item['status'] not in TERMINAL:item['status']=batch.status
        if name in ('analysis','browser_analysis') and item['status']=='running':item['detail']='Controles en relaties beoordelen' if item.get('done')==item.get('total') else 'Pagina-eigenschappen verzamelen'
    ordered=[phases[name] for name in ORDER if name in phases]
    current=next((p for p in ordered if p['status']=='running'),None) or next((p for p in ordered if p['status']=='queued'),None)
    active=not failed and (root_active or current is not None or bool(browser and browser.batch.status in ('pending','processing')))
    # Never keep polling a terminal failed browser's queued analysis.
    if browser and browser.batch.status in ('failed','cancelled'):
        for p in ordered:
            if p['name'] in ('browser','browser_analysis') and p['status'] not in ('complete','partial','skipped'):p['status']=browser.batch.status
        current=next((p for p in ordered if p['status'] in ('running','queued')),None);active=not failed and (root_active or current is not None)
    for p in ordered:
        if p.get('started') and p['status']=='running':
            elapsed=max(0,now-p['started']);observed=max(0,p['done']-p.get('baseline',0));rate=observed/elapsed if elapsed and observed else None
            pending=max(0,p['total']-p['done']) if p.get('total') is not None else None
            import math
            p.update(elapsed_seconds=int(elapsed),units_per_second=rate,pending=pending,eta_seconds=math.ceil(pending/rate) if pending and rate else None)
    uncertain=batch.status=='incomplete' or any(p['status'] in ('partial','incomplete','failed','cancelled') or p.get('outcomes',{}).get('unknown',0) or p.get('outcomes',{}).get('blocked',0) for p in ordered)
    final_label='Onderzoek gestopt' if failed else 'Onderzoek afgerond met gedeeltelijke metingen' if uncertain else 'Onderzoek afgerond'
    last=max([p.get('updated') or 0 for p in ordered]+[live.get('updated_at') or 0]) or None
    return {'status_label':final_label,'phases':ordered,'current_phase':current['name'] if current else None,'active':active,'requests':requests[:20] if active else [],
        'series':series,'totals':totals,'updated_at':last,'end':int(now),'interval_seconds':1,'window_seconds':300,
        'telemetry_available':bool(root or browser and child),
        'stale_seconds':int(now-last) if active and last else None,
        'browser_run_id':str(browser.pk) if browser else None}
