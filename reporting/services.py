"""Freeze inputs, group observations and publish reproducible report snapshots."""
import hashlib
import json
from collections import Counter, defaultdict
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, F
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from projects.permissions import can_edit
from sources.models import ImportBatch
from assessments.models import AnalysisRun, Finding
from browser_scans.models import BrowserRun
from site_intelligence.models import EnrichmentRun, CustomerDeclaration
from catalog.adapters import normalize_url
from .models import Dossier,DossierInput,DossierPage,IssueGroup,IssueOccurrence,CategoryScore
from .catalog import VERSION,CATEGORIES,EXPECTED,STATES,normalized_checks,category,score


MAX_PAGES=150000
MAX_OCCURRENCES=100000
MAX_GROUPS=10000

def digest(value):return hashlib.sha256(value.encode()).hexdigest()


def date(value):
    if not isinstance(value,str):return None
    try:
        result=parse_datetime(value)
        return result if result and timezone.is_aware(result) else None
    except (ValueError,TypeError):return None


def url_identity(value):
    result=normalize_url(value)
    return (result[0],result[2]) if result else ('[URL onbekend]',digest(str(value)))


def allowed(actor,project):
    if not actor.is_active or not can_edit(actor,project):raise PermissionDenied


def technology_snapshot(browser):
    enrichment=EnrichmentRun.objects.filter(batch=browser,status__in=('complete','incomplete')).order_by('-completed_at','-pk').first() if browser else None
    return {'profile':enrichment.european_profile,'enrichment_id':enrichment.pk,'status':enrichment.status,
        'technologies':list(enrichment.detections.values_list('technology__name',flat=True).distinct())} if enrichment else {}


def declaration_snapshot(project):
    return [{'kind':item.kind,'statement':item.statement,'technology__name':item.technology.name,'actor_id':item.actor_id,'declared_at':item.created_at.isoformat()}
        for item in CustomerDeclaration.objects.filter(project=project).select_related('technology').order_by('-created_at')[:50]]


@transaction.atomic
def create_dossier(project,actor,crawl_id,browser_id=None,title=''):
    allowed(actor,project)
    crawl=ImportBatch.objects.get(pk=crawl_id,project=project,status__in=('complete','incomplete'),request_metadata__scan='crawl')
    browser=None
    if browser_id:
        browser=ImportBatch.objects.get(pk=browser_id,project=project,status__in=('complete','incomplete'),request_metadata__scan='browser')
        if not BrowserRun.objects.filter(batch=browser,source_batch=crawl).exists():
            raise ValidationError('Het browseronderzoek hoort niet bij de geselecteerde webcrawl.')
    selections=[]
    for batch,kind in ((crawl,'crawl'),(browser,'browser')):
        if not batch:continue
        latest={}
        for run in batch.analysis_runs.filter(status='complete').select_related('definition').order_by('-started_at','-id'):
            if run.definition.key not in ('crawl-basics','browser-basics','privacy-signals'):continue
            latest.setdefault(run.definition.key,run)
        if 'crawl-basics' not in latest:raise ValidationError('Een pagina-analyse ontbreekt. Voer eerst heranalyse uit.')
        for key,run in latest.items():selections.append((batch,'privacy' if key=='privacy-signals' else kind,run))
    dossier=Dossier.objects.create(project=project,crawl=crawl,browser=browser,actor=actor,title=title.strip() or 'Websiteonderzoek '+project.name,version=VERSION,
        coverage={'frozen_technology':technology_snapshot(browser),'customer_declarations':declaration_snapshot(project)})
    for batch,kind,run in selections:
        DossierInput.objects.create(dossier=dossier,run=run,batch=batch,kind=kind,snapshot={'run_id':str(run.pk),'batch_id':str(batch.pk),
            'batch_label':batch.label,'batch_status':batch.status,'definition':run.definition.key,'rule_version':run.definition.version,
            'extractor_version':run.extractor_version,'analysis_at':run.started_at.isoformat(),'privacy_enabled':batch.request_metadata.get('privacy_enabled',False)})
    transaction.on_commit(lambda:enqueue(dossier.pk))
    return dossier


def enqueue(dossier_id):
    from .tasks import build_dossier
    try:build_dossier.delay(str(dossier_id))
    except Exception:Dossier.objects.filter(pk=dossier_id,status='pending').update(status='failed',error='Dossier kon niet worden ingepland.')


def browser_checks(answer,violations):
    coverage=answer.get('coverage',{})
    complete=answer.get('_complete',False) and coverage.get('privacy_action_complete') is not False and not coverage.get('timed_out') and not coverage.get('blocked') and not coverage.get('a11y_incomplete')
    # Browser diagnostics are not field CWV; that control deliberately stays unknown.
    checks={'performance_field':'unknown'}
    for key,rules in {'a11y_contrast':{'accessibility_color-contrast','accessibility_link-in-text-block'},
        'a11y_alt':{'accessibility_image-alt'},'a11y_structure':{'accessibility_region','accessibility_landmark-one-main'}}.items():
        checks[key]='issue' if rules & violations else 'pass' if complete else 'unknown'
    if any(rule.startswith('accessibility_') and rule not in {'accessibility_color-contrast','accessibility_link-in-text-block','accessibility_image-alt'} for rule in violations):checks['a11y_structure']='issue'
    checks['broken_resource']='issue' if 'broken_resource' in violations else 'pass' if complete and coverage.get('resource_count',100)>=0 and coverage.get('resource_count',100)<100 else 'unknown'
    return checks


def collect_pages(dossier,inputs,limits):
    processed=0
    for item in inputs:
        run=item.run
        query=run.results.annotate(raw_url=F('source_record__data__url'),raw_final=F('source_record__data__final_url'),
            raw_date=F('source_record__data__measured_at'),raw_scenario=F('source_record__data__privacy_scenario')).order_by('id')
        buffer=[]
        def flush(rows):
            if not rows:return
            prepared=[]
            violations=defaultdict(set)
            browser_status={}
            if item.snapshot['definition']=='browser-basics':
                from browser_scans.models import BrowserVisit
                browser_status=dict(BrowserVisit.objects.filter(source_record_id__in=[row['source_record_id'] for row in rows]).values_list('source_record_id','status'))
                for rid,rule in Finding.objects.filter(result_id__in=[row['id'] for row in rows]).values_list('result_id','rule'):violations[rid].add(rule)
            for row in rows:
                answer=row['answer'];url,url_hash=url_identity(answer.get('input',{}).get('url') or row['raw_final'] or row['raw_url'])
                scenario=row['raw_scenario'] or 'baseline'
                scope='privacy:'+scenario if item.kind=='privacy' else item.kind
                checks=normalized_checks(answer.get('checks',{}))
                if item.snapshot['definition']=='browser-basics':checks.update(browser_checks({**answer,'_complete':browser_status.get(row['source_record_id'])=='complete'},violations[row['id']]))
                if item.kind=='privacy':checks={'privacy_tracking':row['outcome'] if scenario!='baseline' else 'unknown'}
                prepared.append((row,url,url_hash,scope,checks,date(answer.get('measured_at') or row['raw_date'])))
            existing={(p.url_hash,p.scope):p for p in DossierPage.objects.filter(dossier=dossier,url_hash__in={p[2] for p in prepared})}
            created={};updated={}
            for row,url,url_hash,scope,checks,measured in prepared:
                key=(url_hash,scope);page=existing.get(key) or created.get(key)
                if page:
                    for rule,state in checks.items():
                        if STATES.get(state,2)>STATES.get(page.checks.get(rule,'not_applicable'),0):page.checks[rule]=state
                    page.measured_at=min(page.measured_at,measured) if page.measured_at and measured else None
                    page.snapshot.setdefault('definitions',{})[item.snapshot['definition']]=item.snapshot['rule_version']
                    if page.pk:updated[page.pk]=page
                else:
                    page=DossierPage(dossier=dossier,source_record_id=row['source_record_id'],url=url,url_hash=url_hash,scope=scope,
                        measured_at=measured,checks=checks,snapshot={'definitions':{item.snapshot['definition']:item.snapshot['rule_version']},'input_kind':item.kind})
                    created[key]=page
            DossierPage.objects.bulk_create(created.values(),batch_size=200)
            DossierPage.objects.bulk_update(updated.values(),['checks','measured_at','snapshot'],batch_size=100)
        for row in query.values('id','source_record_id','answer','outcome','raw_url','raw_final','raw_date','raw_scenario').iterator(chunk_size=50):
            processed+=1
            if processed>MAX_PAGES:
                limits.add('page_limit');flush(buffer);return
            buffer.append(row)
            if len(buffer)==50:flush(buffer);buffer=[]
        flush(buffer)


def signature(rule,details):
    if rule in ('link_to_404','link_to_5xx','broken_resource'):
        candidates=[details.get('destination')]
        if rule=='broken_resource' and not candidates[0]:candidates=[details.get('url')]
        candidates += [item.get('destination') for item in details.get('observations',[])]
        destinations=sorted({url_identity(url)[0] for url in candidates if url})
        if len(destinations)==1:return 'Zelfde gemeten bestemming',destinations[0]
        if destinations:return 'Zelfde set gemeten bestemmingen',json.dumps(destinations)
    if rule.startswith('duplicate_'):
        value=details.get('value')
        if isinstance(value,list):value=' '.join(str(value[0]).split()).casefold() if value else ''
        return 'Exact dezelfde primaire waarde',json.dumps(value,sort_keys=True,ensure_ascii=False)
    if rule.startswith('accessibility_'):
        selectors=sorted({str(node.get('target'))[:400] for node in details.get('nodes',[])})
        return 'Zelfde controle en elementselectie',json.dumps(selectors)
    return 'Zelfde controle; oorzaak nog te beoordelen',rule


def evidence_summary(details):
    """Small public observations, without cookie values or raw headers."""
    labels={'destination':'Bestemming','target_final_status':'HTTP-eindstatus','final_status':'HTTP-eindstatus','http_status':'HTTP-status','status':'HTTP-status',
        'value':'Waarde','next':'Vervolgcanonical','noindex':'Noindex','technology':'Dienst','phase':'Meetfase','name':'Cookienaam','missing':'Ontbrekend'}
    sources=[details]+[row for row in details.get('observations',[])[:3] if isinstance(row,dict)]
    summaries=[]
    for row in sources:
        for key,label in labels.items():
            if key not in row:continue
            value=row[key]
            if isinstance(value,bool):text='ja' if value else 'nee'
            elif isinstance(value,(list,tuple)):text=', '.join(str(v) for v in value[:3])
            elif isinstance(value,(str,int,float)):text=str(value)
            else:continue
            line=label+': '+' '.join(text.split())[:350]
            if line not in summaries:summaries.append(line)
            if len(summaries)>=5:return summaries
    nodes=details.get('nodes',[])
    if nodes and isinstance(nodes[0],dict) and nodes[0].get('failureSummary'):
        summaries.append('Waarneming: '+' '.join(str(nodes[0]['failureSummary']).split())[:600])
    return summaries[:5]


def collect_groups(dossier,inputs,limits):
    groups={};buffer=[];count=0
    for item in inputs:
        rows=Finding.objects.filter(result__run=item.run).annotate(raw_url=F('result__source_record__data__url'),
            raw_date=F('result__source_record__data__measured_at'),analysis_date=F('result__answer__measured_at'),scenario=F('result__source_record__data__privacy_scenario')).values(
                'id','rule','severity','title','recommendation','details','result__source_record_id','raw_url','raw_date','analysis_date','scenario').order_by('id')
        for row in rows.iterator(chunk_size=100):
            count+=1
            if count>MAX_OCCURRENCES:limits.add('occurrence_limit');break
            rule=row['rule'];details=row['details'];basis,signal=signature(rule,details)
            fingerprint=digest(json.dumps(['group-v1',rule,signal],ensure_ascii=False))
            group=groups.get(fingerprint)
            if not group:
                if len(groups)>=MAX_GROUPS:limits.add('group_limit');continue
                group=IssueGroup.objects.create(dossier=dossier,fingerprint=fingerprint,rule=rule,category=category(rule),title=row['title'],
                    recommendation=row['recommendation'],severity=row['severity'],basis=basis)
                groups[fingerprint]=group
            if row['severity']=='high':group.severity='high'
            url,url_hash=url_identity(details.get('source_url') or details.get('url') or row['raw_url'])
            scope='privacy:'+(row['scenario'] or 'baseline') if item.kind=='privacy' else item.kind
            buffer.append(IssueOccurrence(group=group,finding_id=row['id'],source_record_id=row['result__source_record_id'],
                url=url,url_hash=url_hash,scope=scope,measured_at=date(row['analysis_date'] or details.get('measured_at') or row['raw_date']),snapshot={'rule':rule,'definition':item.snapshot['definition'],
                    'rule_version':item.snapshot['rule_version'],'run_id':item.snapshot['run_id'],'summary':evidence_summary(details),'certainty':details.get('certainty','technical signal; review required')}))
            if len(buffer)==500:IssueOccurrence.objects.bulk_create(buffer,batch_size=500);buffer=[]
    IssueOccurrence.objects.bulk_create(buffer,batch_size=500)
    totals={row['group_id']:row for row in IssueOccurrence.objects.filter(group__dossier=dossier).values('group_id').annotate(n=Count('id'),urls=Count('url_hash',distinct=True))}
    for group in groups.values():
        row=totals.get(group.pk,{'n':0,'urls':0});group.occurrences=row['n'];group.affected_urls=row['urls']
    IssueGroup.objects.bulk_update(groups.values(),['occurrences','affected_urls','severity'],batch_size=200)


def calculate_categories(dossier,limits):
    for key in EXPECTED:
        scope='browser' if key=='experience' and dossier.browser_id else 'crawl'
        pages=dossier.pages.filter(scope=scope)
        state,value,coverage,calculation=score(pages.values_list('checks',flat=True).iterator(chunk_size=500),EXPECTED[key])
        if key=='experience' and not dossier.browser_id:state,value='insufficient',None
        if limits:state,value='insufficient',None
        coverage.update({'url_count':pages.count(),'scope':'Browsersteekproef' if scope=='browser' else 'Webcrawl'})
        CategoryScore.objects.create(dossier=dossier,key=key,title=CATEGORIES[key],state=state,score=value,coverage=coverage,calculation=calculation)
    counts=Counter();browser_urls=dossier.pages.filter(scope='browser').values('url_hash').distinct().count()
    for checks in dossier.pages.filter(scope__startswith='privacy:').exclude(scope='privacy:baseline').values_list('checks',flat=True):counts[checks.get('privacy_tracking','unknown')]+=1
    expected=browser_urls*4
    counts['unknown']+=max(0,expected-sum(counts.values()))
    known=counts['issue']+counts['pass']
    CategoryScore.objects.create(dossier=dossier,key='privacy',title=CATEGORIES['privacy'],state='signals' if counts['issue'] else 'observed' if expected and known==expected else 'insufficient',
        coverage={'counts':dict(counts),'known':known,'applicable':expected,'percent':round(100*known/expected,1) if expected else 0,'scope':'Vier afzonderlijke toestemmingsscenario’s'},
        calculation={'version':VERSION,'interpretation':'technical consent signals; no legal compliance score'})
    technology=dossier.coverage.get('frozen_technology',{})
    CategoryScore.objects.create(dossier=dossier,key='technology',title=CATEGORIES['technology'],state='profile' if technology else 'insufficient',
        coverage=technology,calculation={'version':VERSION,'interpretation':'publisher geography is separate from data location, contract and GDPR compliance'})


@transaction.atomic
def assemble(dossier_id):
    dossier=Dossier.objects.select_for_update(of=('self',)).select_related('project','actor','crawl','browser').get(pk=dossier_id)
    if dossier.status=='complete':return dossier
    if dossier.version!=VERSION:raise ValidationError('Deze rapportversie is niet meer uitvoerbaar. Maak een nieuw dossier.')
    allowed(dossier.actor,dossier.project)
    inputs=list(dossier.inputs.select_related('run','batch'))
    if not inputs or any(not item.run_id or not item.batch_id or item.batch.project_id!=dossier.project_id or item.run.status!='complete' for item in inputs):
        raise ValidationError('Een bevroren analysebron is niet meer beschikbaar.')
    dossier.status='processing';dossier.save(update_fields=['status'])
    dossier.pages.all().delete();dossier.groups.all().delete();dossier.categories.all().delete()
    limits=set();collect_pages(dossier,inputs,limits);collect_groups(dossier,inputs,limits);calculate_categories(dossier,limits)
    dates=dossier.pages.exclude(measured_at=None).order_by('measured_at')
    oldest=dates.values_list('measured_at',flat=True).first();newest=dates.values_list('measured_at',flat=True).last()
    dossier.coverage={**dossier.coverage,'version':VERSION,'limits':sorted(limits),'crawl_urls':dossier.pages.filter(scope='crawl').count(),
        'browser_urls':dossier.pages.filter(scope='browser').count(),'undated_measurements':dossier.pages.filter(measured_at=None).count(),'groups':dossier.groups.count(),
        'occurrences':IssueOccurrence.objects.filter(group__dossier=dossier).count(),
        'measured_from':oldest.isoformat() if oldest else None,'measured_until':newest.isoformat() if newest else None,
        'grouping':'Observed common signatures; shared cause is a hypothesis, not automatically proven.',
        'customer_declarations':dossier.coverage.get('customer_declarations',[])}
    allowed(dossier.actor,dossier.project)
    dossier.status='complete';dossier.completed_at=timezone.now();dossier.error=''
    dossier.save(update_fields=['status','completed_at','coverage','error'])
    return dossier
