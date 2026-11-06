"""Derive reference outcomes from stored evidence, without making requests."""
import hashlib
import json
from catalog.adapters import normalize_url
from scans.references import classify,candidates,local_check,VERSION
from scans.models import ResourceMeasurement
from .models import ReferenceObservation,PageMeasurement,LinkObservation


def build(page,data,config):
    result=[];seen=set()
    for position,item in enumerate(candidates(data)):
        if position>=6000:
            page.coverage={**page.coverage,'references_truncated':True}
            page.save(update_fields=['coverage'])
            break
        ref=classify(item.get('url',''),page.final_url,config,function=item.get('function'),download=item.get('download',False),raw_reference=item.get('raw_url'))
        identity=hashlib.sha256(json.dumps([ref['url'],ref['function'],item.get('locator') or 'position:'+str(position),item.get('element_path'),item.get('instruction'),item.get('reference_type')],sort_keys=True).encode()).hexdigest()
        if identity in seen:continue
        seen.add(identity)
        outcome,reason=local_check(ref,data)
        result.append(ReferenceObservation(source=page,identity=identity,url=ref['url'],destination=ref['destination'],function=ref['function'],scope=ref['scope'],
            outcome=outcome or 'not_tested',reason=reason or 'not_in_measurements',details={'classification':ref,'reference':item,'position':position,'checks':{}}))
    return result


def evaluate(batch):
    pages={p['url__url_hash']:p for p in PageMeasurement.objects.filter(batch=batch).values('url__url_hash','final_http_status','http_status','content_type','is_indexable')}
    resources={r.url:r for r in ResourceMeasurement.objects.filter(batch=batch).order_by('purpose','id')}
    config=batch.request_metadata
    buffer=[]
    for ref in ReferenceObservation.objects.filter(source__batch=batch).select_related('source').iterator(chunk_size=200):
        item=ref.details['reference'];identity=normalize_url(ref.url)
        page=pages.get(identity[2]) if identity else None
        resource=resources.get(identity[0]) if identity else None
        mime=(resource.headers.get('content-type','') if resource else '') or (page['content_type'] if page else '')
        classification=classify(ref.url,ref.source.final_url,config,function=item.get('function'),download=item.get('download',False),mime=mime,raw_reference=item.get('raw_url'))
        ref.destination=classification['destination'];ref.function=classification['function'];ref.scope=classification['scope']
        state,reason=local_check(classification,ref.source.facts)
        checks={};status=None
        if state is None:
            if ref.function=='instruction':
                # Canonical/hreflang validity remains in their dedicated page controls.
                state,reason='not_applicable','evaluated_by_instruction_controls'
            else:
                status=resource.http_status if resource else page['final_http_status'] if page else None
                outcome=resource.outcome if resource else ''
                if outcome.startswith('not_measured'):state,reason='not_tested',outcome
                elif outcome in ('blocked','redirect_blocked','download_error','interrupted_unknown','unknown_response'):
                    state,reason='unknown',outcome
                elif page and page['final_http_status'] is None:state,reason='unknown','page_download_error'
                elif status is None:
                    state,reason='not_tested','file_checks_disabled' if ref.function in ('download','loaded_resource') and not config.get('check_resources',True) else 'external_checks_disabled' if ref.destination=='external' and not config.get('check_external_links',True) else 'not_in_measurements'
                elif status in (401,403,408,425,429,451) or not 200<=status<600:state,reason='unknown','http_access_uncertain'
                elif 200<=status<300:state,reason='pass','http_success'
                elif 300<=status<400:state,reason='unknown','redirect_destination_unverified'
                else:state,reason='issue','http_error'
                checks['availability']=state
                if ref.destination=='own' and ref.function=='navigation':
                    checks['indexability']='not_tested' if not page else 'unknown' if page['is_indexable'] is None else 'pass' if page['is_indexable'] else 'issue'
                if ref.function=='download':
                    from .r4 import download_type_state
                    checks['file_type']=download_type_state(ref.url,mime,status)
                if resource:ref.details['measurement']={'id':str(resource.pk),'http_status':status,'outcome':outcome,'measurements':resource.measurements}
                elif page:ref.details['measurement']={'http_status':status,'source':'page_measurement'}
        if checks.get('file_type')=='issue':state,reason='issue','download_mime_mismatch'
        ref.outcome=state;ref.reason=reason
        ref.details={**ref.details,'classification':classification,'checks':checks or {'local':state}}
        buffer.append(ref)
        if len(buffer)>=500:
            ReferenceObservation.objects.bulk_update(buffer,['destination','function','scope','outcome','reason','details'],batch_size=500);buffer=[]
    if buffer:ReferenceObservation.objects.bulk_update(buffer,['destination','function','scope','outcome','reason','details'],batch_size=500)
    # Link graph controls consume only own, permitted navigation links. Update
    # observed MIME classification too (extensionless downloads are not pages).
    refs=ReferenceObservation.objects.filter(source__batch=batch,function__in=('navigation','download','fragment','contact','action'))
    by_source={}
    def flush_links(source_id,items):
        links=list(LinkObservation.objects.filter(source_id=source_id))
        for link in links:
            match=items.get(link.position)
            if match:link.details={**(link.details or {}),'reference':match}
        LinkObservation.objects.bulk_update(links,['details'],batch_size=500)
    current=None
    for source_id,position,classification in refs.order_by('source_id','id').values_list('source_id','details__position','details__classification').iterator(chunk_size=500):
        if current is not None and source_id!=current:flush_links(current,by_source);by_source={}
        current=source_id;by_source[position]=classification
    if current is not None:flush_links(current,by_source)


def internal_navigation(query,*,include_excluded=False):
    query=query.filter(details__reference__destination='own',details__reference__function='navigation')
    return query if include_excluded else query.filter(details__reference__scope='allowed')


def prepare_legacy_links(batch):
    from django.db.models import Q
    rows=[]
    query=LinkObservation.objects.filter(source__batch=batch).filter(Q(details=None)|~Q(details__has_key='reference')).select_related('source')
    for link in query.iterator(chunk_size=500):
        link.details={**(link.details or {}),'reference':classify(link.destination,link.source.final_url,batch.request_metadata)}
        rows.append(link)
        if len(rows)>=500:LinkObservation.objects.bulk_update(rows,['details'],batch_size=500);rows=[]
    if rows:LinkObservation.objects.bulk_update(rows,['details'],batch_size=500)


def attach_findings(run,batch,summary):
    from .models import AnalysisResult,Finding,FindingEvidence
    results=dict(AnalysisResult.objects.filter(run=run).values_list('source_record_id','pk'))
    definitions={'fragment':('reference_fragment_missing','Paginaonderdeel achter link ontbreekt','Pas de verwijzing aan of voeg het ontbrekende id toe.'),
        'contact':('reference_contact_invalid','Contactlink heeft een ongeldige opbouw','Corrigeer het e-mail- of telefoonadres. Er wordt geen bericht of oproep verstuurd.')}
    findings=[];evidence=[]
    def flush():
        Finding.objects.bulk_create(findings,batch_size=500)
        FindingEvidence.objects.bulk_create([FindingEvidence(finding=f,source_record_id=source,role='source',snapshot=f.details) for f,source in evidence],batch_size=500)
        AnalysisResult.objects.filter(pk__in={f.result_id for f in findings}).update(outcome='issue')
        findings.clear();evidence.clear()
    for ref in ReferenceObservation.objects.filter(source__batch=batch,function__in=definitions,outcome='issue').select_related('source').iterator(chunk_size=500):
        result_id=results.get(ref.source.source_record_id)
        if not result_id:continue
        rule,title,action=definitions[ref.function]
        finding=Finding(result_id=result_id,rule=rule,severity='medium',title=title,recommendation=action,
            details={'source_url':ref.source.final_url,'destination':ref.url,'locator':ref.details['reference'].get('locator',''),
                'classification':ref.details['classification'],'reason':ref.reason,'count':1})
        findings.append(finding);evidence.append((finding,ref.source.source_record_id))
        summary[rule]+=1
        if len(findings)>=500:flush()
    if findings:flush()
