"""Resource-target findings. Targets never become content-page score denominators."""
from collections import Counter,defaultdict
from django.db.models import Count
from scans.models import ResourceMeasurement,ResourceReference
from .models import AnalysisResult,Finding,FindingEvidence

RULES={
 'resource_http_error':('assets','high','Bestand geeft een HTTP-fout','Herstel het gedeelde bestand of pas de verwijzingen aan; bekijk alle betrokken pagina’s bij de bestandsmeting.'),
 'resource_mime_mismatch':('assets','medium','Bestandstype wijkt af van de verwijzing','Controleer de route en het Content-Type; een HTML-antwoord is geen werkend script of stylesheet.'),
 'resource_image_large':('assets','low','Afbeelding boven ingestelde grootteadviesgrens','Beoordeel formaat en compressie in de getoonde gebruikscontext. Dit is geen gemeten paginalaadtijd of besparingsgarantie.'),
 'external_http_error':('technical','medium','Externe linkbestemming geeft een HTTP-fout','Controleer de externe bestemming en werk de verwijzing zo nodig bij. De externe site is niet verder gecrawld.'),
}

CONTROL_VERSION='resource-controls-v2'


def checks(target,threshold,types=None):
    status=target.http_status;metrics=target.measurements
    if types is None:types=set(target.references.values_list('kind',flat=True))
    mime=target.headers.get('content-type','').split(';')[0].strip().lower()
    measured=status is not None
    unknown=target.outcome in ('blocked','redirect_blocked','download_error','interrupted_unknown','not_measured_budget','not_measured_time','not_measured','unknown_response') or measured and not 200<=status<600
    failed=measured and (status in (404,410) or 500<=status<=599 or 400<=status<500 and status not in (401,403,408,425,429,451))
    states={}
    if target.purpose=='external':
        states['external_http_error']='issue' if failed else 'unknown' if unknown or not measured or status in (408,425,451) or 300<=status<400 else 'pass'
        return states
    states['resource_http_error']='issue' if failed else 'unknown' if unknown or not measured or status in (408,425,451) or 300<=status<400 else 'pass'
    expected=[kind for kind in types if kind in ('script','stylesheet','image','font')]
    valid={'script':{'text/javascript','application/javascript','application/x-javascript','text/ecmascript','application/ecmascript'},'stylesheet':{'text/css'}}
    def matches(kind):
        if kind=='image':return mime.startswith('image/')
        if kind=='font':return mime.startswith(('font/','application/font','application/x-font')) or mime in ('application/vnd.ms-fontobject','application/x-woff')
        return mime in valid[kind]
    applicable=measured and 200<=status<300 and bool(expected)
    states['resource_mime_mismatch']='not_applicable' if measured and status>=400 or not expected else 'unknown' if not applicable or not mime or mime=='application/octet-stream' else 'issue' if any(not matches(kind) for kind in expected) else 'pass'
    image=mime.startswith('image/') or 'image' in types and not mime
    size=metrics.get('wire_bytes')
    states['resource_image_large']='not_applicable' if not image or measured and not 200<=status<300 else 'issue' if isinstance(size,int) and size>threshold else 'pass' if measured and 200<=status<300 and isinstance(size,int) and metrics.get('wire_complete') and metrics.get('representation_complete',True) else 'unknown'
    return states


def attach(run,batch,summary):
    if not batch.request_metadata.get('resource_version'):return
    results={url:(pk,source) for pk,source,url in run.results.values_list('id','source_record_id','answer__input__url').iterator(chunk_size=1000)}
    kinds=defaultdict(set)
    for target_id,kind in ResourceReference.objects.filter(target__batch=batch).values_list('target_id','kind').iterator(chunk_size=1000):kinds[target_id].add(kind)
    coverage=defaultdict(Counter);findings=[];evidence=[]
    threshold=batch.request_metadata.get('image_advice_bytes',100*1024)
    def flush():
        Finding.objects.bulk_create(findings,batch_size=200)
        rows=[]
        for finding,urls,target in evidence:
            for url in urls:
                if url in results:rows.append(FindingEvidence(finding=finding,source_record_id=results[url][1],role='source',
                    snapshot={'url':url,'resource_id':str(target.pk),'destination':target.url,'http_status':target.http_status,
                              'measurements':target.measurements,'measured_at':target.measured_at.isoformat() if target.measured_at else None}))
        FindingEvidence.objects.bulk_create(rows,batch_size=1000)
        findings.clear();evidence.clear()
    for target in ResourceMeasurement.objects.filter(batch=batch).iterator(chunk_size=100):
        states=checks(target,threshold,kinds[target.pk])
        for rule,state in states.items():coverage[rule][state]+=1
        issues=[rule for rule,state in states.items() if state=='issue']
        if not issues:continue
        first_sources=[];page_sources=set()
        for url in target.references.values_list('source_url',flat=True).distinct().iterator(chunk_size=1000):
            if len(first_sources)<20:first_sources.append(url)
            if url in results:page_sources.add(url)
        source_urls=sorted(page_sources)[:20] or first_sources
        result=next((results[url] for url in source_urls if url in results),None)
        anchor_only=result is None
        if not result:result=next(iter(results.values()),None)
        if not result:continue
        affected=len(page_sources)
        examples=list(target.references.values('source_url','locator','element_path','kind','parent_url')[:20])
        for rule in issues:
            _,severity,title,action=RULES[rule]
            finding=Finding(result_id=result[0],rule=rule,severity=severity,title=title,recommendation=action,
                details={'resource_id':str(target.pk),'destination':target.url,'final_url':target.final_url,'purpose':target.purpose,
                    'http_status':target.http_status,'measured_at':target.measured_at.isoformat() if target.measured_at else None,
                    'resource_headers':target.headers,'resource_evidence':target.evidence,'content_type':target.headers.get('content-type',''),'measurements':target.measurements,'count':affected,
                    'source_urls':source_urls,'result_anchor_only':anchor_only,'elements':examples,'threshold_bytes':threshold if rule=='resource_image_large' else None,
                    'certainty':'measured target; review required','scope':'one resource target, all retained page references; separate from page scores'})
            findings.append(finding);summary[rule]+=1
            evidence.append((finding,source_urls,target))
        AnalysisResult.objects.filter(pk=result[0]).update(outcome='issue')
        if len(findings)>=200:flush()
    if findings:flush()
    run.coverage['resource_controls_version']=CONTROL_VERSION
    run.coverage['resource_checks']={rule:dict(states) for rule,states in coverage.items()}
    run.coverage['resource_scan']=batch.request_metadata.get('resource_scan',{})
