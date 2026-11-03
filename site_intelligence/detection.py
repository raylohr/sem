"""Auditable signatures: exact host boundaries, bounded parsing, no user regex/code."""
import hashlib
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin
from django.utils import timezone
from browser_scans.privacy import redacted_url
from .catalog import supplier_snapshot
from .models import TechnologyDetection, DetectionEvidence


class InventoryParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.generators=[]
        self.assets=[]
    def handle_starttag(self, tag, attrs):
        values=dict(attrs)
        if tag=='meta' and values.get('name','').lower()=='generator':
            self.generators.append(values.get('content','')[:300])
        if tag in ('script','link','iframe','img'):
            url=values.get('src') or values.get('href')
            if url and len(self.assets)<2000:
                self.assets.append(url[:4096])


def suffix(host, pattern):
    host=host.rstrip('.').lower()
    pattern=pattern.rstrip('.').lower()
    return host==pattern or host.endswith('.'+pattern)


def add_detection(run, rule, *, visit=None, state='present', phase='', signal='', resource=None, artifact=None, domain=None, version=''):
    if run.detections.count()>=2000:
        run.coverage={**run.coverage,'detections_truncated':True}
        run.save(update_fields=['coverage'])
        return
    fingerprint=hashlib.sha256(json.dumps([rule.pk,visit.pk if visit else None,state,phase,signal],ensure_ascii=False).encode()).hexdigest()
    snapshots=[]
    for relation in rule.technology.supplier_relations.select_related('supplier').filter(valid_until__isnull=True, valid_from__lte=timezone.localdate()):
        snapshots.append({**supplier_snapshot(relation.supplier),'role':relation.role,'relationship_source':relation.source_url})
    detection,created=TechnologyDetection.objects.get_or_create(run=run,fingerprint=fingerprint,defaults={
        'technology':rule.technology,'visit':visit,'rule':rule,'state':state,'phase':phase,'version':version,
        'confidence':'inferred' if rule.kind in ('asset_path','asn','dns_suffix') else 'observed',
        'measured_at':visit.measured_at if visit and visit.measured_at else domain.measured_at if domain else timezone.now(),
        'supplier_snapshot':snapshots})
    if created:
        DetectionEvidence.objects.create(detection=detection,source_record_id=visit.source_record_id if visit else None,
            artifact=artifact,resource=resource,domain=domain,snapshot={'signal':signal,'state':state,'rule':rule.key,'version':rule.version,'scenario':visit.scenario if visit else None,'scope':'public website signal; not proof of central CRM, complete plugin list or processing location'})


def detect_visit(run, visit, rules):
    artifacts=list(visit.artifacts.filter(kind__in=('server_html','rendered_dom')).defer('body'))
    parsed=[]
    for artifact in artifacts:
        parser=InventoryParser()
        # Deliberate extraction cap; original artifacts remain separately available.
        raw=bytes(artifact.body)
        parser.feed(raw[:1024*1024].decode('utf-8',errors='replace'))
        parsed.append((artifact,parser))
        if len(raw)>1024*1024:
            run.coverage={**run.coverage,'html_detection_truncated':True}
            run.save(update_fields=['coverage'])
    resources=list(visit.resources.all())
    cookies=list(visit.storage_observations.filter(kind='cookie'))
    headers=visit.coverage.get('response_headers',{})
    globals_=visit.coverage.get('technology_globals',{})
    for rule in rules:
        matches=[]
        if rule.kind=='generator':
            for artifact,parser in parsed:
                for generator in parser.generators:
                    if generator.lower().startswith(rule.pattern.lower()):
                        version=''
                        found=re.search(r'\b(\d+(?:\.\d+){1,3}(?:[-a-zA-Z0-9.]*)?)\b',generator)
                        if found:version=found.group(1)[:60]
                        matches.append({'signal':generator,'artifact':artifact,'version':version})
        elif rule.kind=='asset_path':
            for artifact,parser in parsed:
                for asset in parser.assets:
                    if rule.pattern.lower() in urlsplit(asset).path.lower():
                        matches.append({'signal':redacted_url(urljoin(visit.final_url or visit.url,asset)),'artifact':artifact})
        elif rule.kind=='global' and rule.pattern in globals_:
            version=globals_[rule.pattern]
            matches.append({'signal':rule.pattern,'version':version if re.fullmatch(r'\d+(?:\.\d+){1,3}(?:[-a-zA-Z0-9.]*)?',version) else ''})
        elif rule.kind=='header' and rule.config.get('contains','').lower() in str(headers.get(rule.pattern,'')).lower() and headers.get(rule.pattern):
            matches.append({'signal':rule.pattern+': '+str(headers[rule.pattern])[:200]})
        elif rule.kind=='cookie_prefix':
            for cookie in cookies:
                if cookie.name.startswith(rule.pattern):
                    matches.append({'signal':cookie.name,'phase':cookie.phase,'state':'cookie_observed'})
        elif rule.kind in ('resource_host','request_path'):
            for resource in resources:
                parts=urlsplit(resource.url)
                if not parts.hostname or not suffix(parts.hostname,rule.pattern):continue
                if rule.config.get('path') and not parts.path.startswith(rule.config['path']):continue
                state='request_observed' if resource.status and not resource.blocked_reason else 'attempted_request'
                matches.append({'signal':redacted_url(resource.url),'phase':resource.details.get('phase','baseline'),'resource':resource,'state':state})
        # Up to three independent signals per rule/visit, retaining distinct phases first.
        selected=[];phases=set()
        for match in matches:
            phase=match.get('phase','')
            if phase not in phases:
                selected.append(match);phases.add(phase)
        selected.extend(match for match in matches if match not in selected)
        for match in selected[:3]:
            add_detection(run,rule,visit=visit,**match)


def detect_domain(run, domain, rules):
    values=[]
    for kind in ('CNAME','NS','MX'):
        values.extend(domain.records.get(kind,{}).get('values',[]))
    asns={str(asn) for network in domain.network.get('networks',[]) for asn in network.get('asns',[])}
    for rule in rules:
        if rule.kind=='dns_suffix':
            for value in values:
                # MX wire text begins with preference; NS/CNAME do not.
                parts=value.strip('"').split()
                if not parts:continue
                hostname=parts[-1].rstrip('.')
                if suffix(hostname,rule.pattern):
                    add_detection(run,rule,state='dns_observed',signal=hostname,domain=domain)
                    break
        elif rule.kind=='asn' and rule.pattern in asns:
            add_detection(run,rule,state='network_observed',signal='AS'+rule.pattern,domain=domain)
