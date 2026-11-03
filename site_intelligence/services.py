"""Evidence-backed privacy signals and separate European publisher dependency profile."""
import hashlib
import uuid
from urllib.parse import urlsplit
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from projects.permissions import can_edit
from sources.models import ImportBatch
from browser_scans.models import BrowserRun
from browser_scans.privacy import redacted_url
from scans.network import normalize_url, normalize_host, SafeClient, ScanError
from scans.extraction import extract_page
from types import SimpleNamespace
from assessments.models import AnalysisDefinition, AnalysisRun, AnalysisResult, Finding, FindingEvidence
from .catalog import EU27, supplier_snapshot
from .models import EnrichmentRun, DomainObservation, PolicyDocument, TechnologyDetection, Supplier, SupplierRevision


def check_run(run_id):
    run=EnrichmentRun.objects.select_related('batch__project','batch__scan_target','actor').get(pk=run_id)
    if run.batch.status not in ('complete','incomplete') or not can_edit(run.actor,run.batch.project):
        raise PermissionDenied('Bron of projecttoegang niet beschikbaar.')
    target=run.batch.scan_target
    if target and (not target.active or target.project_id!=run.batch.project_id):
        raise PermissionDenied('Scandoel ingetrokken.')
    return run


def schedule_enrichment(batch_id, actor_id, *, fresh=False):
    version='intel-v1-'+uuid.uuid4().hex[:8] if fresh else 'intel-v1'
    run,_=EnrichmentRun.objects.get_or_create(batch_id=batch_id,version=version,defaults={'actor_id':actor_id})
    if run.status in ('complete','incomplete'):return run
    try:
        from .tasks import enrich
        enrich.delay(run.pk)
    except Exception:
        EnrichmentRun.objects.filter(pk=run.pk).update(status='failed',error='Verrijking kon niet worden ingepland.')
    return run


def profile(run):
    suppliers={}
    unknown_tech=set()
    for detection in run.detections.all().iterator(chunk_size=200):
        if not detection.supplier_snapshot:unknown_tech.add(detection.technology_id)
        for item in detection.supplier_snapshot:
            suppliers[(item['key'],item['revision'])]=item
    return {'definition':'EU-27; publisher entities, not contractual processors or data locations',
        'eu_publishers':sum(s['country'] in EU27 for s in suppliers.values()),
        'non_eu_publishers':sum(bool(s['country']) and s['country'] not in EU27 for s in suppliers.values()),
        'unknown_publishers':sum(not s['country'] for s in suppliers.values()),
        'unknown_parent_countries':sum(not s['parent_country'] for s in suppliers.values()),
        'non_eu_parents':sum(bool(s['parent_country']) and s['parent_country'] not in EU27 for s in suppliers.values()),
        'technologies_without_supplier':len(unknown_tech),'technology_count':run.detections.values('technology_id').distinct().count(),
        'data_locations':'unknown; edge/ASN/company country does not prove data location',
        'gdpr_assessment':'not inferred from geography'}


def fetch_policies(run, browser):
    if run.policies.exists():return
    candidates=[]
    for visit in browser.visits.all().only('coverage'):
        for link in visit.coverage.get('policy_links',[]):
            try:url=normalize_url(link.get('url'),browser.config['allowed_hosts'])
            except (ScanError,TypeError):continue
            if url not in candidates:candidates.append(url)
    client=SafeClient(browser.config['allowed_hosts'],check_access=lambda:check_run(run.pk),duration=20,
        delay=1/browser.config['requests_per_second'],max_transfer=10*1024*1024,respect_robots=browser.config['respect_robots'])
    errors=[]
    try:
        for url in candidates[:2]:
            try:
                result=client.fetch(url)
                if result.status!=200 or 'html' not in result.headers.get('content-type',''):
                    errors.append({'url':redacted_url(url),'status':result.status});continue
                fields,_=extract_page(result)
                text=fields.get('text','')
                PolicyDocument.objects.create(run=run,url=redacted_url(result.url),status=result.status,text=text[:30000],body=result.body,
                    checksum=hashlib.sha256(result.body).hexdigest(),truncated=len(text)>30000 or fields.get('text_truncated',False))
            except ScanError as exc:
                errors.append({'url':redacted_url(url),'code':type(exc).__name__})
    finally:client.close()
    run.coverage={**run.coverage,'policy_candidates':len(candidates),'policy_fetch_errors':errors,'policy_lookup_limit':2}
    run.save(update_fields=['coverage'])


@transaction.atomic
def privacy_analysis(run, browser):
    existing=run.coverage.get('analysis_id')
    if existing and AnalysisRun.objects.filter(pk=existing).exists():return
    definition,_=AnalysisDefinition.objects.get_or_create(key='privacy-signals',version='1',defaults={
        'method':'rules','question':'Welke tracking- en opslagwaarnemingen vragen beoordeling vóór keuze, na weigeren of intrekken?'})
    analysis=AnalysisRun.objects.create(batch=run.batch,definition=definition,extractor_version=run.version,
        coverage={'scenario_set':browser.config.get('scenarios',['baseline']),'interpretation':'technical signals for manual review, no automatic legal compliance verdict'})
    counts={}
    policy_text=' '.join(run.policies.values_list('text',flat=True)).casefold()
    for visit in browser.visits.all().iterator(chunk_size=20):
        enabled=browser.config.get('privacy_enabled',False)
        results=[]
        result=AnalysisResult.objects.create(run=analysis,source_record_id=visit.source_record_id,outcome='unknown',answer={
            'scenario':visit.scenario,'actions':visit.privacy.get('actions',[]),'coverage':visit.coverage,'storage_values_retained':False,
            'scope':'Public observations only; policies, purposes, exemptions and agreements require interpretation.'})
        if enabled:
            phase={'no_choice':'before_choice','reject':'after_reject','withdraw':'after_withdraw'}.get(visit.scenario)
            action_ok=visit.scenario=='no_choice' or visit.privacy.get('complete') is True
            trackers=run.detections.filter(visit=visit,phase=phase,rule__config__tracking=True,state='request_observed').select_related('technology') if phase and action_ok else []
            seen=set()
            for detection in trackers:
                if detection.technology_id in seen:continue
                seen.add(detection.technology_id)
                evidence=detection.evidence.first()
                results.append(('tracking_'+phase,'Trackingverzoek waargenomen '+{'before_choice':'vóór een keuze','after_reject':'na weigeren','after_withdraw':'na intrekken'}[phase],
                    {'technology':detection.technology.name,'phase':phase,'signal':evidence.snapshot if evidence else {},'review_required':True}))
            if phase and action_ok:
                for cookie in visit.storage_observations.filter(phase=phase,kind='cookie'):
                    if cookie.name.startswith(('_ga','_fbp','_gcl_au','_hj')):
                        results.append(('tracking_cookie_'+phase,'Mogelijke trackingcookie waargenomen',{'name':cookie.name,'origin':cookie.origin,'phase':phase,'metadata':cookie.metadata,'review_required':True}))
        if policy_text and visit.scenario in ('baseline','no_choice'):
            technologies=run.detections.filter(visit=visit,technology__category__in=('advertising','analytics','behavior_analytics','crm_marketing')).select_related('technology').distinct('technology_id')
            for detection in technologies:
                aliases=detection.technology.aliases or [detection.technology.name]
                if not any(alias.casefold() in policy_text for alias in aliases):
                    results.append(('policy_name_not_matched','Geobserveerde dienst niet herkend in gevonden verklaring',{'technology':detection.technology.name,'policy_urls':list(run.policies.values_list('url',flat=True)),'review_required':True,'basis':'bounded literal alias match; not proof of an incomplete legal notice'}))
        for rule,title,details in results:
            finding=Finding.objects.create(result=result,rule=rule,severity='medium',title=title,recommendation='Controleer doel, configuratie, toestemmingsgedrag en de bijbehorende verklaring; dit signaal is geen juridisch oordeel.',details={'url':visit.final_url or visit.url,'browser_visit_id':visit.pk,'intelligence_run_id':run.pk,**details})
            FindingEvidence.objects.create(finding=finding,source_record_id=visit.source_record_id,browser_artifact=visit.artifacts.filter(kind='screenshot').defer('body').first(),role='page',snapshot=details)
            counts[rule]=counts.get(rule,0)+1
        if results:result.outcome='issue'
        elif enabled and visit.status=='complete' and visit.privacy.get('complete'):result.outcome='pass'
        result.save(update_fields=['outcome'])
    analysis.status='complete';analysis.completed_at=timezone.now();analysis.summary=counts
    analysis.save(update_fields=['status','completed_at','summary'])
    run.coverage={**run.coverage,'analysis_id':str(analysis.pk)}
    run.save(update_fields=['coverage'])


@transaction.atomic
def update_supplier(supplier_id, actor, values):
    if not actor.is_active or not actor.is_superuser:raise PermissionDenied
    supplier=Supplier.objects.select_for_update().get(pk=supplier_id)
    for key in ('name','country','parent_name','parent_country','notes','checked_at'):
        setattr(supplier,key,values.get(key,''))
    supplier.sources=[values['source_url']] if values.get('source_url') else []
    supplier.revision+=1
    supplier.save()
    SupplierRevision.objects.create(supplier=supplier,revision=supplier.revision,snapshot=supplier_snapshot(supplier),actor=actor)
    return supplier
