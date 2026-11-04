"""Deterministic page and same-batch relationship rules. Never fetches URLs."""
from collections import Counter, defaultdict, deque
import re
from urllib.parse import urlsplit
from django.db.models import Q
from catalog.adapters import normalize_url
from site_intelligence.catalog import ISO_CODES
from scans.seo import google_tokens
from .models import PageMeasurement, LinkObservation, AnalysisResult, Finding, FindingEvidence

# Technical priorities; these are review signals, not ranking or compliance scores.
RULES = {
    'http_error': ('http','high','Pagina geeft een HTTP-fout','Herstel de fout of pas verwijzingen en de gewenste URL-status aan.'),
    'redirect_chain': ('http','medium','Meerdere redirectstappen','Laat interne verwijzingen en redirects direct naar de gewenste eindbestemming wijzen.'),
    'redirect_loop': ('http','high','Redirectlus waargenomen','Verwijder de lus uit de redirectconfiguratie.'),
    'canonical_missing': ('indexability','medium','Canonical ontbreekt: beoordelen','Bepaal welke URL de voorkeursversie is en of een canonical nodig is.'),
    'canonical_multiple': ('indexability','high','Meerdere canonical-instructies','Gebruik één consistente canonical in HTML en eventuele HTTP-headers.'),
    'canonical_invalid': ('indexability','high','Ongeldige canonical-URL','Gebruik een geldige absolute HTTP(S)-bestemming.'),
    'canonical_target': ('relations','high','Canonicaldoel geeft fout of is uitgesloten','Controleer de status en robots-instructies van het canonicaldoel.'),
    'canonical_chain': ('relations','medium','Canonical verwijst door naar een andere canonical','Verwijs naar de uiteindelijke voorkeurs-URL.'),
    'canonical_loop': ('relations','high','Canonicalverwijzingen vormen een lus','Kies een consistente voorkeurs-URL en verwijder de lus.'),
    'robots_conflict': ('indexability','medium','Tegengestelde robots-instructies','Maak instructies per user-agent consistent; de beperkende instructie kan voorrang krijgen.'),
    'robots_noindex_blocked': ('indexability','medium','Noindex op een voor Googlebot geblokkeerde pagina','Controleer of Googlebot de noindex-instructie kan ophalen.'),
    'language_missing': ('content','medium','HTML-taal ontbreekt','Vul het lang-attribuut van het hoofddocument in.'),
    'heading_jump': ('content','medium','Sprong in headingniveaus: beoordelen','Controleer of koppen de inhoud logisch structureren.'),
    'image_alt_missing': ('assets','medium','Afbeelding zonder alt-attribuut','Voeg passende alternatieve tekst toe; gebruik alt="" voor decoratieve afbeeldingen.'),
    'mixed_content': ('assets','high','HTTP-resource op een HTTPS-pagina','Gebruik HTTPS voor scripts, stylesheets, afbeeldingen en andere resources.'),
    'jsonld_invalid': ('structured_data','medium','JSON-LD is syntactisch ongeldig','Herstel de JSON-syntaxis; controleer daarna apart schema en rich-result-voorwaarden.'),
    'security_headers_missing': ('security','medium','Beveiligingsheaders ontbreken: beoordelen','Beoordeel HSTS, CSP en X-Content-Type-Options voor deze applicatie.'),
    'hreflang_invalid': ('international','medium','Ongeldige hreflang-code of URL','Gebruik ondersteunde taal-/regiocodes en een absolute HTTP(S)-URL.'),
    'hreflang_conflict': ('international','medium','Eén hreflang-code verwijst naar verschillende URL’s','Maak de taalverwijzingen eenduidig.'),
    'hreflang_self_missing': ('international','medium','Eigen URL ontbreekt in hreflang-set','Neem de eigen taalversie op in de wederzijdse taalverwijzingen.'),
    'hreflang_target': ('relations','high','Hreflangdoel geeft fout of is niet de voorkeursversie','Verwijs naar een bereikbare, indexeerbare voorkeurs-URL.'),
    'hreflang_return_missing': ('relations','medium','Hreflang-terugverwijzing ontbreekt of heeft een andere taalcode','Laat de taalversies wederzijds naar elkaar verwijzen.'),
    'link_to_5xx': ('relations','high','Interne link leidt naar een serverfout','Herstel de doelpagina of de verwijzing.'),
    'link_to_redirect': ('relations','medium','Interne link leidt via een redirect','Verwijs waar mogelijk direct naar de eindbestemming.'),
    'link_to_nonindexable': ('relations','medium','Interne link naar uitgesloten of alternatieve URL: beoordelen','Beoordeel of de verwijzing bewust naar een niet-indexeerbare of niet-canonical URL wijst.'),
    'no_observed_inlinks': ('structure','medium','Geen interne verwijzingen waargenomen','Controleer interne ondersteuning; dit bewijst geen volledige orphanstatus buiten deze crawl.'),
}
LANGUAGES = frozenset('aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch co cr cs cu cv cy da de dv dz ee el en eo es et eu fa ff fi fj fo fr fy ga gd gl gn gu gv ha he hi ho hr ht hu hy hz ia id ie ig ii ik io is it iu ja jv ka kg ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv mg mh mi mk ml mn mr ms mt my na nb nd ne ng nl nn no nr nv ny oc oj om or os pa pi pl ps pt qu rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq sr ss st su sv sw ta te tg th ti tk tl tn to tr ts tt tw ty ug uk ur uz ve vi vo wa wo xh yi yo za zh zu'.split())


def identity(url):
    value = normalize_url(url)
    return value[0] if value else None


def valid_hreflang(code):
    if code.lower() == 'x-default': return True
    parts = code.split('-')
    if not parts or parts[0].lower() not in LANGUAGES: return False
    rest = parts[1:]
    if rest and re.fullmatch('[A-Za-z]{4}',rest[0]): rest = rest[1:]
    return not rest or (len(rest)==1 and rest[0].upper() in ISO_CODES)


def graph_metrics(batch):
    """Unique URL edges, same scenario. Budgets prevent unbounded graph memory."""
    nodes = {};source_keys={}
    for row in PageMeasurement.objects.filter(batch=batch).values_list('id','url__url_hash','facts__privacy_scenario','url__normalized_url','source_record_id').iterator(chunk_size=1000):
        key=(row[1],row[2] or 'baseline');source_keys[row[0]]=key
        nodes.setdefault(key,{'url':row[3],'record':row[4]})
        if len(nodes)>100000: return {}, {'complete':False,'reason':'node_limit','node_limit':100000}
    outgoing=defaultdict(set); incoming=defaultdict(set); edges=0;observations=0
    links=LinkObservation.objects.filter(source__batch=batch).values_list('source_id','destination_hash')
    for source_id,target in links.iterator(chunk_size=2000):
        observations+=1
        if observations>1000000:return {}, {'complete':False,'reason':'edge_observation_limit','edge_limit':1000000}
        a=source_keys[source_id];b=(target,a[1])
        if a==b or b not in nodes: continue
        if b not in outgoing[a]:edges+=1
        outgoing[a].add(b);incoming[b].add(a)
        if edges>1000000: return {}, {'complete':False,'reason':'edge_limit','edge_limit':1000000}
    start=identity(batch.request_metadata.get('url') or batch.project.website)
    roots=[key for key,node in nodes.items() if identity(node['url'])==start]
    depths={key:0 for key in roots}; queue=deque(roots)
    while queue:
        source=queue.popleft()
        for target in outgoing[source]:
            if target not in depths: depths[target]=depths[source]+1;queue.append(target)
    complete = batch.status=='complete' and not PageMeasurement.objects.filter(batch=batch).filter(
        Q(coverage__links_known=False)|Q(coverage__links_truncated=True)|Q(coverage__analysis_truncated=True)|Q(coverage__legacy_link_limit_unknown=True)).exists()
    metrics={key:{'observed_inlinks':len(incoming[key]),'unique_internal_outlinks':len(outgoing[key]),
        'graph_depth':depths.get(key),'root':key in roots,'scope':'unique URL edges within same batch and scenario; self-links excluded'} for key in nodes}
    return metrics, {'complete':complete,'nodes':len(nodes),'edges':edges,'roots':len(roots),'root_url':start,
        'node_limit':100000,'edge_limit':1000000,'interpretation':'zero inlinks is a crawl-local candidate, not proof of an orphan across all data sources'}


TARGET_FIELDS = ['source_record_id','url__normalized_url','url__url_hash','final_url','http_status','final_http_status','is_indexable',
    'facts__canonicals','facts__hreflang','facts__hreflang_truncated','facts__robot_directives','facts__privacy_scenario','coverage__document_id','coverage__browser_artifact_id']


def load_target_index(batch):
    fields=['id','source_record_id','url__normalized_url','url__url_hash','http_status','final_http_status','is_indexable',
        'facts__privacy_scenario','coverage__document_id','coverage__browser_artifact_id']
    targets={}
    for row in PageMeasurement.objects.filter(batch=batch).values(*fields).iterator(chunk_size=1000):
        key=(row['url__url_hash'],row['facts__privacy_scenario'] or 'baseline')
        if key not in targets or row['source_record_id']<targets[key]['source_record_id']:targets[key]=row
        if len(targets)>100000:return None
    return targets


def broken_links(batch,index):
    if index is None:return
    hashes={key[0] for key,target in index.items() if target['final_http_status']==404}
    if not hashes:return
    query=LinkObservation.objects.filter(source__batch=batch)
    if len(hashes)<=1000:query=query.filter(destination_hash__in=hashes)
    for link in query.select_related('source').order_by('source_id','position').iterator(chunk_size=200):
        target=index.get((link.destination_hash,link.source.facts.get('privacy_scenario') or 'baseline'))
        if not target or target['final_http_status']!=404:continue
        link.target_record=target['source_record_id'];link.target_status=target['http_status']
        link.target_final_status=target['final_http_status'];link.target_document=target['coverage__document_id']
        yield link


def target_lookup(batch,pages,index):
    """At most 10 batched frontier queries; no network and no query per edge."""
    if index is None:return {}
    scenarios={page.facts.get('privacy_scenario') or 'baseline' for page in pages}
    pending=set()
    for page in pages:
        pending.update(u for u in map(identity,page.facts.get('canonicals',[])[:20]) if u)
        pending.update(u for u in (identity(item.get('url')) for item in page.facts.get('hreflang',[])[:100]) if u)
    targets={}; visited=set()
    for _ in range(10):
        frontier=pending-visited
        if not frontier: break
        visited.update(frontier);pending=set()
        ids={index[(normalize_url(url)[2],scenario)]['id'] for url in frontier for scenario in scenarios if (normalize_url(url)[2],scenario) in index}
        for row in PageMeasurement.objects.filter(pk__in=ids,batch=batch).values(*TARGET_FIELDS).iterator(chunk_size=200):
            key=(row['url__normalized_url'],row['facts__privacy_scenario'] or 'baseline')
            targets.setdefault(key,row)
            pending.update(u for u in map(identity,(row['facts__canonicals'] or [])[:20]) if u)
    return targets


class Recorder:
    def __init__(self,page,result):
        self.page=page;self.result=result;self.checks={};self.issues=defaultdict(list);self.targets=defaultdict(dict)
    def check(self,rule,state,details=None,target=None):
        old=self.checks.get(rule)
        rank={'not_applicable':0,'pass':1,'unknown':2,'issue':3}
        if old is None or rank[state]>rank[old]:self.checks[rule]=state
        if state=='issue':
            if len(self.issues[rule])<20:self.issues[rule].append(details or {})
            if target and len(self.targets[rule])<5:self.targets[rule][target['source_record_id']]=target


def local_checks(rec):
    p=rec.page;f=p.facts;status=p.final_http_status
    rec.check('http_error','unknown' if status is None else 'issue' if status>=400 else 'pass',{'status':status})
    redirects=f.get('redirects')
    if isinstance(redirects,list):
        rec.check('redirect_chain','issue' if len(redirects)>1 else 'pass',{'hops':redirects[:10],'count':len(redirects)})
        urls=[identity(item.get('url')) for item in redirects if isinstance(item,dict)]
        urls=[url for url in urls if url]
        rec.check('redirect_loop','issue' if len(urls)!=len(set(urls)) or (urls and identity(p.final_url) in urls) else 'pass',{'hops':redirects[:10]})
    else:
        rec.check('redirect_chain','unknown');rec.check('redirect_loop','unknown')
    if not p.coverage.get('html_inspected') or status!=200:
        for rule in RULES:
            if rule not in rec.checks and RULES[rule][0] not in ('relations','structure'):
                rec.check(rule,'not_applicable' if status is not None and status!=200 else 'unknown')
        return
    canonicals=f.get('canonicals')
    rec.check('canonical_missing','unknown' if canonicals is None else 'issue' if not canonicals else 'pass')
    html_count=len([x for x in f.get('canonical_sources',[]) if x.get('source')=='html']) if 'canonical_sources' in f else len(canonicals or [])
    rec.check('canonical_multiple','unknown' if canonicals is None else 'issue' if len(set(canonicals))>1 or html_count>1 else 'pass',{'canonicals':canonicals})
    rec.check('canonical_invalid','unknown' if canonicals is None else 'issue' if any(identity(url) is None for url in canonicals) else 'pass',{'canonicals':canonicals})
    tokens=google_tokens(f)
    rec.check('robots_conflict','unknown' if 'robot_directives' not in f else 'issue' if ({'index','noindex'}<=tokens or {'follow','nofollow'}<=tokens) else 'pass',{'googlebot_tokens':sorted(tokens),'directives':f.get('robot_directives')})
    rec.check('robots_noindex_blocked','issue' if 'noindex' in tokens and f.get('googlebot_allowed') is False else 'unknown' if f.get('googlebot_allowed') is None else 'pass',{'googlebot_allowed':f.get('googlebot_allowed'),'googlebot_tokens':sorted(tokens)})
    rec.check('language_missing','unknown' if 'language' not in f else 'issue' if not f['language'].strip() else 'pass')
    headings=f.get('heading_sequence')
    jumps=[{'from':a['level'],'to':b['level'],'locator':b['locator']} for a,b in zip(headings or [],(headings or [])[1:]) if b['level']>a['level']+1]
    rec.check('heading_jump','issue' if jumps else 'unknown' if headings is None or f.get('headings_truncated') else 'pass',{'jumps':jumps[:20]})
    images=f.get('images');missing=[item for item in images or [] if not item.get('alt_present')]
    rec.check('image_alt_missing','issue' if missing else 'unknown' if images is None or f.get('images_truncated') else 'pass',{'images':missing[:20]})
    resources=f.get('resource_urls');mixed=[u for u in resources or [] if isinstance(u,str) and u.lower().startswith('http:')] if urlsplit(p.final_url).scheme=='https' else []
    rec.check('mixed_content','issue' if mixed else 'unknown' if resources is None or len(resources)>=200 else 'pass',{'resources':mixed[:20]})
    jsonld=f.get('jsonld');invalid=[item for item in jsonld or [] if item.get('status')=='invalid']
    rec.check('jsonld_invalid','issue' if invalid else 'unknown' if jsonld is None or f.get('jsonld_truncated') or any(item.get('status')=='unknown' for item in jsonld or []) else 'pass',{'blocks':invalid,'validator':'stdlib-json-syntax-v1; no schema or Google eligibility validation'})
    headers=f.get('response_headers',{})
    missing_headers=[name for name in ('content-security-policy','x-content-type-options','strict-transport-security') if name not in headers and (name!='strict-transport-security' or urlsplit(p.final_url).scheme=='https')]
    rec.check('security_headers_missing','unknown' if not f.get('headers_observed') else 'issue' if missing_headers else 'pass',{'missing':missing_headers,'interpretation':'configuration review, not an exploit or compliance verdict'})


def relationship_checks(rec,targets):
    p=rec.page;f=p.facts;scenario=f.get('privacy_scenario') or 'baseline'
    source=identity(p.final_url)
    canonicals=list(dict.fromkeys(f['canonicals'])) if isinstance(f.get('canonicals'),list) else None
    for rule in ('canonical_target','canonical_chain','canonical_loop'):
        rec.check(rule,'unknown' if canonicals is None or len(canonicals)>1 else 'not_applicable' if not canonicals else 'pass')
    if canonicals and len(canonicals)==1 and identity(canonicals[0]):
        url=identity(canonicals[0]);seen={source};path=[source]
        for depth in range(10):
            if url==source and depth==0:break  # a self-canonical is normal
            if url in seen:
                rec.check('canonical_loop','issue',{'path':path,'repeated':url});break
            seen.add(url);path.append(url);target=targets.get((url,scenario))
            if not target:
                for rule in ('canonical_target','canonical_chain','canonical_loop'):rec.check(rule,'unknown')
                break
            status=target['final_http_status'];initial=target['http_status']
            noindex='noindex' in google_tokens({'robot_directives':target['facts__robot_directives'] or []})
            if status is None:rec.check('canonical_target','unknown')
            elif status>=400 or (initial and 300<=initial<400) or noindex:
                rec.check('canonical_target','issue',{'destination':url,'http_status':initial,'final_status':status,'noindex':noindex},target)
            next_urls=target['facts__canonicals']
            if next_urls is None:
                rec.check('canonical_chain','unknown');rec.check('canonical_loop','unknown');break
            if len(next_urls)>1:
                rec.check('canonical_chain','unknown');rec.check('canonical_loop','unknown');break
            if not next_urls:break
            next_url=identity(next_urls[0])
            if not next_url or next_url==url:break
            rec.check('canonical_chain','issue',{'destination':url,'next':next_url},target)
            url=next_url
        else:rec.check('canonical_loop','unknown')
    entries=f.get('hreflang')
    for rule in ('hreflang_invalid','hreflang_conflict','hreflang_self_missing','hreflang_target','hreflang_return_missing'):
        rec.check(rule,'unknown' if entries is None or f.get('hreflang_truncated') else 'not_applicable' if not entries else 'pass')
    if not entries:return
    own_codes={str(item.get('code','')).lower() for item in entries if identity(item.get('url'))==source and valid_hreflang(str(item.get('code','')))}
    codes=defaultdict(set)
    for entry in entries[:100]:
        code=str(entry.get('code',''));url=identity(entry.get('url'))
        if not valid_hreflang(code) or not url:
            rec.check('hreflang_invalid','issue',{'entry':entry});continue
        codes[code.lower()].add(url)
        if url==source:continue
        target=targets.get((url,scenario))
        if not target:
            rec.check('hreflang_target','unknown');rec.check('hreflang_return_missing','unknown');continue
        status=target['final_http_status'];canonical=target['facts__canonicals']
        bad=(status is not None and status!=200) or (target['http_status'] is not None and target['http_status']!=200) or target['is_indexable'] is False or (canonical and any(identity(u)!=url for u in canonical))
        rec.check('hreflang_target','issue' if bad else 'unknown' if status is None or canonical is None or target['is_indexable'] is None else 'pass',
            {'destination':url,'status':status,'canonical':canonical,'is_indexable':target['is_indexable']},target)
        back=target['facts__hreflang']
        if back is None or target['facts__hreflang_truncated']:
            rec.check('hreflang_return_missing','unknown')
        elif not any(identity(item.get('url'))==source and valid_hreflang(str(item.get('code',''))) and (not own_codes or str(item.get('code','')).lower() in own_codes) for item in back):
            rec.check('hreflang_return_missing','issue',{'destination':url,'expected_return':source,'expected_codes':sorted(own_codes),'observed':back[:20]},target)
    conflicts={code:sorted(urls) for code,urls in codes.items() if len(urls)>1}
    if conflicts:rec.check('hreflang_conflict','issue',{'conflicts':conflicts})
    if not any(identity(item.get('url'))==source for item in entries):rec.check('hreflang_self_missing','issue',{'source':source})


def persist_recorders(recorders,run,summary,coverage):
    findings=[]; evidence=[];results=[]
    for rec in recorders:
        result=rec.result
        result.answer['checks'].update(rec.checks)
        states=set(result.answer['checks'].values())
        result.outcome='issue' if 'issue' in states else 'unknown' if 'unknown' in states else 'pass' if 'pass' in states else 'not_applicable'
        results.append(result)
        for rule,state in rec.checks.items():coverage[rule][state]+=1
        for rule,observations in rec.issues.items():
            category,severity,title,action=RULES[rule]
            finding=Finding(result=result,rule=rule,severity=severity,title=title,recommendation=action,details={
                'url':rec.page.final_url,'category':category,'observations':observations,'certainty':'observed technical signal; review required',
                'scope':'same batch and consent scenario; no new network requests','measured_at':rec.page.measured_at.isoformat() if rec.page.measured_at else None})
            findings.append(finding);summary[rule]+=1
            evidence.append((finding,rec.page,rec.targets[rule]))
    Finding.objects.bulk_create(findings,batch_size=200)
    rows=[]
    for finding,page,targets in evidence:
        rows.append(FindingEvidence(finding=finding,source_record_id=page.source_record_id,document_id=page.coverage.get('document_id'),
            browser_artifact_id=page.coverage.get('browser_artifact_id'),role='source',snapshot={'url':page.final_url,'measured_at':page.measured_at.isoformat() if page.measured_at else None,'observations':finding.details['observations']}))
        for target in targets.values():
            rows.append(FindingEvidence(finding=finding,source_record_id=target['source_record_id'],document_id=target['coverage__document_id'],browser_artifact_id=target.get('coverage__browser_artifact_id'),role='target',
                snapshot={'url':target['url__normalized_url'],'http_status':target['http_status'],'final_http_status':target['final_http_status'],
                    'canonical':target['facts__canonicals'],'hreflang':target['facts__hreflang'],'is_indexable':target['is_indexable']}))
    FindingEvidence.objects.bulk_create(rows,batch_size=200)
    AnalysisResult.objects.bulk_update(results,['answer','outcome'],batch_size=100)


def extend_link_checks(recorders,batch,targets):
    """Three bounded set queries per page chunk, avoiding correlated OR subqueries."""
    sources={rec.page.pk:rec for rec in recorders}
    links=LinkObservation.objects.filter(source_id__in=sources)
    if targets is None:return
    for link in links.order_by('source_id','position').iterator(chunk_size=500):
        rec=sources[link.source_id];scenario=rec.page.facts.get('privacy_scenario') or 'baseline'
        target=targets.get((link.destination_hash,scenario))
        if not target:
            rec.check('link_to_nonindexable','unknown');continue
        if target['final_http_status'] is None or (target['final_http_status']==200 and target['is_indexable'] is None):
            rec.check('link_to_nonindexable','unknown')
        target={**target,'facts__canonicals':None,'facts__hreflang':None}
        initial=target['http_status'];final=target['final_http_status']
        for rule,match in [('link_to_5xx',final is not None and final>=500),
            ('link_to_redirect',initial is not None and 300<=initial<400),
            ('link_to_nonindexable',target['is_indexable'] is False and final==200)]:
            if match:
                rec.check(rule,'issue',{'destination':link.destination,'anchor_text':link.anchor_text,'locator':link.locator,
                    'initial_status':initial,'final_status':final},target)


def duplicate_peer_evidence(run,batch,pages):
    specs={'duplicate_text':'text_hash','duplicate_titles':'facts__normalized_titles','duplicate_meta_descriptions':'facts__normalized_meta_descriptions'}
    findings=Finding.objects.filter(result__run=run,result__source_record_id__in=[p.source_record_id for p in pages],rule__in=specs).select_related('result')
    page_map={p.source_record_id:p for p in pages}; peers={}
    for rule,field in specs.items():
        keys={p.text_hash if field=='text_hash' else p.facts.get(field.split('__')[1]) for p in pages}
        keys.discard(None);keys.discard('')
        if not keys:continue
        grouped=defaultdict(list)
        for row in PageMeasurement.objects.filter(batch=batch,**{field+'__in':keys}).order_by('source_record_id').values(
            field,'source_record_id','final_url','coverage__document_id','facts__privacy_scenario').iterator(chunk_size=200):
            key=(row[field],row['facts__privacy_scenario'] or 'baseline')
            if len(grouped[key])<6 and all(peer['final_url']!=row['final_url'] for peer in grouped[key]):grouped[key].append(row)
        peers[rule]=grouped
    evidence=[]
    for finding in findings:
        page=page_map[finding.result.source_record_id];field=specs[finding.rule]
        value=page.text_hash if field=='text_hash' else page.facts.get(field.split('__')[1])
        examples=[peer for peer in peers.get(finding.rule,{}).get((value,page.facts.get('privacy_scenario') or 'baseline'),[])
            if peer['source_record_id']!=page.source_record_id and peer['final_url']!=page.final_url][:5]
        for peer in examples:
            if peer['source_record_id']==page.source_record_id or peer['final_url']==page.final_url:continue
            evidence.append(FindingEvidence(finding=finding,source_record_id=peer['source_record_id'],document_id=peer['coverage__document_id'],
                role='target',snapshot={'url':peer['final_url'],'matching_field':field,'matching_value':value,'scope':'exact match, same batch and scenario; at most five peer examples'}))
    FindingEvidence.objects.bulk_create(evidence,batch_size=200)


def update_link_coverage(run,batch,targets):
    """Measure target coverage with set joins rather than a correlated aggregate."""
    from .services import chunks
    if targets is None:run.coverage={**run.coverage,'target_lookup_truncated':True}
    pages=PageMeasurement.objects.filter(batch=batch).order_by('id')
    for chunk in chunks(pages.iterator(chunk_size=100),100):
        sources={page.pk:page for page in chunk}
        links=list(LinkObservation.objects.filter(source_id__in=sources).values_list('source_id','destination_hash'))
        totals=Counter();measured=Counter()
        for source_id,url_hash in links:
            totals[source_id]+=1
            target=(targets or {}).get((url_hash,sources[source_id].facts.get('privacy_scenario') or 'baseline'))
            if target and target['final_http_status'] is not None:measured[source_id]+=1
        results=list(AnalysisResult.objects.filter(run=run,source_record_id__in=[page.source_record_id for page in chunk]))
        by_record={page.source_record_id:page.pk for page in chunk}
        for result in results:
            source_id=by_record[result.source_record_id];total=totals[source_id];known=measured[source_id]
            result.answer['links']={'total':total,'measured_targets':known,'unmeasured_targets':total-known}
            if total!=known:
                if result.answer['checks']['links_to_404']!='issue':result.answer['checks']['links_to_404']='unknown'
                if result.outcome=='pass':result.outcome='unknown'
        AnalysisResult.objects.bulk_update(results,['answer','outcome'],batch_size=100)
    return targets


def extend_analysis(run,batch,summary,target_index):
    metrics,graph=graph_metrics(batch)
    coverage=defaultdict(Counter)
    pages=PageMeasurement.objects.filter(batch=batch).select_related('url').order_by('id')
    from .services import chunks
    for chunk in chunks(pages.iterator(chunk_size=100),100):
        targets=target_lookup(batch,chunk,target_index)
        results={result.source_record_id:result for result in AnalysisResult.objects.filter(run=run,source_record_id__in=[page.source_record_id for page in chunk])}
        recorders=[]
        for page in chunk:
            result=results[page.source_record_id];rec=Recorder(page,result)
            local_checks(rec)
            if page.final_http_status==200 and page.coverage.get('html_inspected'):relationship_checks(rec,targets)
            else:
                for rule in ('canonical_target','canonical_chain','canonical_loop','hreflang_target','hreflang_return_missing'):
                    rec.check(rule,'not_applicable' if page.final_http_status else 'unknown')
            key=(page.url.url_hash,page.facts.get('privacy_scenario') or 'baseline')
            metric=metrics.get(key)
            result.answer['internal_links']=metric or {'scope':'not calculated; graph budget exceeded'}
            if metric and graph['complete'] and graph['roots'] and page.final_http_status==200 and page.is_indexable is True:
                rec.check('no_observed_inlinks','issue' if not metric['observed_inlinks'] and not metric['root'] else 'pass',metric)
            else:rec.check('no_observed_inlinks','unknown' if page.final_http_status==200 else 'not_applicable')
            # Absence of bad links is assessed below only when all destinations are measured.
            for rule in ('link_to_5xx','link_to_redirect','link_to_nonindexable'):
                link_counts=result.answer.get('links',{})
                known=page.coverage.get('links_known') and not any(page.coverage.get(k) for k in ('links_truncated','analysis_truncated','legacy_link_limit_unknown'))
                fully_measured=link_counts.get('total')==link_counts.get('measured_targets') and 'total' in link_counts
                rec.check(rule,'pass' if known and fully_measured and target_index is not None else 'unknown')
            recorders.append(rec)
        duplicate_peer_evidence(run,batch,chunk)
        extend_link_checks(recorders,batch,target_index)
        persist_recorders(recorders,run,summary,coverage)
    coverage=defaultdict(Counter)
    for checks in run.results.values_list('answer__checks',flat=True).iterator(chunk_size=1000):
        for rule,state in checks.items():
            if rule in RULES:coverage[rule][state]+=1
    run.coverage={**run.coverage,'analysis_module':'page-relations-v1','graph':graph,
        'advanced_checks':{rule:dict(counts) for rule,counts in coverage.items()},
        'structured_data_validator':'Python stdlib JSON syntax v1; no schema validation or rich-result eligibility',
        'hreflang_scope':'HTML and retained HTTP Link headers only; sitemap annotations not inferred',
        'limitations':['No near-duplicate similarity, Link Score or Google index verdict.','Security header absence is a configuration review signal.']}
