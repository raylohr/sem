"""Bounded follow-up browser crawl with independently published evidence."""
import base64
import hashlib
from itertools import islice
from types import SimpleNamespace
from urllib.parse import urlsplit
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from catalog.adapters import normalize_url as identity
from projects.models import Project
from projects.permissions import can_edit
from sources.models import ImportBatch
from sources.services import publish_records
from scans.models import ScanTarget
from scans.extraction import extract_page
from scans.large import bounded_metadata
from scans.network import normalize_url, ScanError, MAX_BODY, USER_AGENT
from assessments.models import AnalysisDefinition, AnalysisRun, AnalysisResult, Finding, FindingEvidence
from .models import BrowserRun, BrowserVisit, BrowserResource, AccessibilityIssue, BrowserArtifact, StorageObservation

MAX_RUN_ARTIFACTS = 256 * 1024 * 1024


@transaction.atomic
def create_run(source_batch, actor, values):
    project = Project.objects.select_for_update().get(pk=source_batch.project_id)
    source = ImportBatch.objects.select_for_update(of=('self',)).select_related('scan_target').get(pk=source_batch.pk)
    if not can_edit(actor, project) or source.status not in ('complete', 'incomplete') or source.scan_kind != 'crawl':
        raise PermissionDenied
    target = source.scan_target
    if not target or not target.active or target.project_id != project.pk:
        raise PermissionDenied
    config = {'max_pages': values.get('max_pages') or 20, 'profile': values.get('profile') or 'desktop',
        'privacy_enabled': bool(values.get('privacy_enabled')),
        'scenarios': ['no_choice','reject','accept','withdraw'] if values.get('privacy_enabled') else ['baseline'],
        'consent_selectors': {key: values.get('consent_' + key, '') for key in ('accept','reject','preferences','withdraw')},
        'timeout_ms': (values.get('timeout_seconds') or 30)*1000, 'settle_ms': 750, 'scroll_steps': 2,
        'requests_per_second': values.get('requests_per_second') or 1,
        'duration': min(7200, max(120, (values.get('max_pages') or 20)*((values.get('timeout_seconds') or 30)+25)*(4 if values.get('privacy_enabled') else 1))), 'max_requests': 100,
        'max_bytes': 20*1024*1024, 'max_depth': 3, 'resource_hosts': values.get('resource_hosts') or [],
        'allowed_hosts': list(source.request_metadata.get('allowed_hosts') or target.allowed_hosts),
        'respect_robots': source.request_metadata.get('respect_robots', True),
        'sampling': 'first URL per host/top-level path, then lexical fill; rendered links use remaining slots',
        'scenario': 'four isolated consent contexts; ephemeral cookie transport; GET only' if values.get('privacy_enabled') else 'no consent interaction; stateless GET transport; service workers and websockets blocked'}
    if not set(config['allowed_hosts']).issubset(set(target.allowed_hosts)):
        raise PermissionDenied
    batch = ImportBatch.objects.create(project=project, created_by=actor, scan_target=target,
        source='semscan', method='scan', label='Browseronderzoek: ' + target.name,
        parser_version='browser-v1', request_metadata={'scan': 'browser', **config})
    run = BrowserRun.objects.create(batch=batch, source_batch=source, config=config)
    eligible = source.url_observations.filter(facts__content_type__icontains='html').values_list('url__normalized_url', flat=True).distinct().order_by('url__normalized_url')
    selected, fallback, groups = [], [], set()
    for url in eligible.iterator(chunk_size=500):
        try:
            url = normalize_url(url, config['allowed_hosts'])
        except ScanError:
            continue
        parts = urlsplit(url)
        group = (parts.hostname, parts.path.strip('/').split('/')[0])
        if group not in groups:
            groups.add(group)
            selected.append(url)
        elif len(fallback) < config['max_pages']:
            fallback.append(url)
        if len(selected) >= config['max_pages']:
            break
    selected.extend(fallback[:max(0, config['max_pages']-len(selected))])
    if not selected:
        selected = [normalize_url(source.request_metadata.get('url') or target.url, config['allowed_hosts'])]
    BrowserVisit.objects.bulk_create([BrowserVisit(run=run, url=url, url_hash=identity(url)[2], scenario=scenario) for url in selected for scenario in config['scenarios']])
    run.coverage = {'eligible_source_pages': eligible.count(), 'initial_sample_pages': len(selected),
        'sample_limit': config['max_pages'], 'sampling': config['sampling'], 'artifact_bytes': 0}
    run.save(update_fields=['coverage'])
    return run


def check_run(run_id):
    run = BrowserRun.objects.select_related('batch__created_by', 'batch__project', 'batch__scan_target').get(pk=run_id)
    batch = run.batch
    target = batch.scan_target
    if batch.status == 'cancelled':
        raise PermissionDenied('Browseronderzoek geannuleerd.')
    if not can_edit(batch.created_by, batch.project) or not target or not target.active or target.project_id != batch.project_id:
        raise PermissionDenied('Toegang of scandoel ingetrokken.')
    if not set(run.config['allowed_hosts']).issubset(set(target.allowed_hosts)):
        raise PermissionDenied('Toegestane hosts gewijzigd.')
    return run


def decode_artifact(value):
    body = base64.b64decode(value, validate=True)
    if len(body) > MAX_BODY:
        raise ScanError('Bewijsdocument overschrijdt 5 MiB.')
    return body


@transaction.atomic
def store_visit(visit_id, response):
    visit = BrowserVisit.objects.select_related('run__batch').get(pk=visit_id)
    project = Project.objects.select_for_update().get(pk=visit.run.batch.project_id)
    run = check_run(visit.run_id)
    main = response.get('server_response')
    if not main:
        raise ScanError('Geen gemeten hoofddocument.')
    if 'html' not in main['headers'].get('content-type', '').lower():
        raise ScanError('Het hoofddocument is geen HTML-pagina.')
    final_url = normalize_url(response['final_url'], run.config['allowed_hosts'])
    server_body = decode_artifact(main['body'])
    dom = decode_artifact(response['dom'])
    screenshot = decode_artifact(response['screenshot'])
    if not screenshot.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ScanError('Ongeldig screenshotformaat.')
    artifact_size = len(server_body) + len(dom) + len(screenshot)
    if run.coverage.get('artifact_bytes', 0) + artifact_size > MAX_RUN_ARTIFACTS:
        raise ScanError('Totaal bewijsbudget bereikt.')
    result = SimpleNamespace(url=final_url, status=main['status'], headers=main['headers'], body=server_body)
    server_fields, _ = extract_page(result)
    result.body = dom
    result.headers = {**main['headers'], 'content-type': 'text/html; charset=utf-8'}
    rendered_fields, _ = extract_page(result)
    server_fields['initial_http_status'] = main.get('initial_status', main['status'])
    server_fields['redirects'] = main.get('redirects', [])
    rendered_fields['redirects'] = main.get('redirects', [])
    server_fields = bounded_metadata(server_fields)
    rendered_fields = bounded_metadata(rendered_fields)
    if main.get('googlebot_allowed') is False:
        rendered_fields['is_indexable'] = False
    elif main.get('googlebot_allowed') is None and rendered_fields.get('is_indexable'):
        rendered_fields['is_indexable'] = None
    privacy = response.get('privacy', {})
    visit.privacy = {key: value for key, value in privacy.items() if key != 'snapshots'}
    visit.privacy['storage_limit_per_snapshot'] = 1000
    visit.storage_observations.all().delete()
    StorageObservation.objects.bulk_create([StorageObservation(visit=visit, phase=item['phase'][:30], kind=item['kind'][:20], name=item['name'][:256], origin=item['origin'][:4096], metadata={**{key: value for key, value in item.get('metadata', {}).items() if key in ('path','expires','httpOnly','secure','sameSite','length')}, 'value_omitted': True}) for item in privacy.get('snapshots', [])[:3000]], batch_size=500)
    visit.resources.all().delete()
    visit.accessibility_issues.all().delete()
    visit.artifacts.all().delete()
    artifacts = [('server_html', main['headers'].get('content-type', 'text/html'), server_body),
                 ('rendered_dom', 'text/html; charset=utf-8', dom), ('screenshot', 'image/png', screenshot)]
    BrowserArtifact.objects.bulk_create([BrowserArtifact(visit=visit, kind=kind, content_type=content_type[:80], size=len(body), checksum=hashlib.sha256(body).hexdigest(), body=body) for kind, content_type, body in artifacts])
    rows = response.get('resources', [])[:run.config['max_requests']]
    BrowserResource.objects.bulk_create([BrowserResource(visit=visit, position=i, url=row['url'][:4096],
        resource_type=row.get('resource_type', 'other')[:40], status=row.get('status'),
        blocked_reason=(row.get('reason') or '')[:80], bytes=row.get('bytes') or 0,
        fetch_ms=row.get('fetch_ms'), details={key: row.get(key) for key in ('method', 'main_navigation', 'start_ms', 'end_ms', 'sha256', 'content_type', 'phase', 'url_redacted')}) for i, row in enumerate(rows)])
    a11y = response.get('accessibility', {})
    BrowserVisit.objects.filter(pk=visit.pk).update(status='processing')
    AccessibilityIssue.objects.bulk_create([AccessibilityIssue(visit=visit, rule=item['id'][:100], impact=(item.get('impact') or '')[:24], title=item['help'], details=item) for item in a11y.get('violations', [])[:100]])
    differences = {}
    for key in ('titles', 'meta_descriptions', 'h1', 'h2', 'canonicals', 'robots', 'word_count', 'text_hash'):
        if server_fields.get(key) != rendered_fields.get(key):
            differences[key] = {'server': server_fields.get(key), 'rendered': rendered_fields.get(key)}
    original_links = set(server_fields.get('outlinks', []))
    added_links = list(dict.fromkeys(link for link in rendered_fields.get('outlinks', []) if link not in original_links))
    differences['added_links'] = added_links[:200]
    differences['added_link_count'] = len(added_links)
    blocked = response.get('blocked', [])[:200]
    errors = response.get('errors', [])[:30]
    visit.status = 'incomplete' if response.get('timed_out') or blocked or errors or (run.config.get('privacy_enabled') and not privacy.get('complete')) else 'complete'
    visit.final_url = final_url
    visit.http_status = main['status']
    visit.measured_at = timezone.now()
    visit.server_facts = {key: value for key, value in server_fields.items() if key != 'text'}
    visit.rendered_facts = {key: value for key, value in rendered_fields.items() if key != 'text'}
    visit.differences = differences
    visit.metrics = response.get('metrics', {})
    visit.metrics['measurement_kind'] = 'Chromium diagnostic lab timings through validated transport; no Lighthouse score or field CWV'
    visit.errors = errors
    visit.duration_ms = response.get('duration_ms')
    visit.coverage = {'timed_out': bool(response.get('timed_out')), 'blocked': blocked,
        'resource_count': len(rows), 'scenario': run.config['scenario'], 'profile': run.config['profile'],
        'technology_globals': response.get('technology_globals', {}), 'policy_links': response.get('policy_links', []),
        'privacy_scenario': visit.scenario, 'privacy_action_complete': privacy.get('complete') if run.config.get('privacy_enabled') else None,
        'viewport': response.get('viewport'), 'transport': response.get('transport'), 'response_headers': main['headers'],
        'a11y_incomplete': a11y.get('incomplete', []), 'a11y_automatic_only': True,
        'images': response.get('images', [])[:500], 'links_truncated': rendered_fields.get('links_truncated', True),
        'image_inventory_limit': 500, 'image_count': response.get('image_count'), 'images_truncated': response.get('image_count', 0) > 500}
    visit.save()
    run.coverage = {**run.coverage, 'artifact_bytes': run.coverage.get('artifact_bytes', 0)+artifact_size}
    run.engine = {'playwright': '1.64.1', 'chromium': response.get('browser_version'), 'axe': a11y.get('version'),
        'axe_sha256': response.get('axe_sha256'), 'locale': 'nl-NL', 'timezone': 'Europe/Amsterdam', 'user_agent': response.get('user_agent', USER_AGENT),
        'device_scale_factor': 1, 'cache_policy': 'fresh context; HTTP cache disabled by routing',
        'cpu_profile': 'one container CPU; no mobile CPU emulation',
        'network_isolation': 'network_mode=none; Unix-socket broker only'}
    run.save(update_fields=['coverage', 'engine'])
    if visit.depth < run.config['max_depth']:
        available = run.config['max_pages'] - run.visits.values('url_hash').distinct().count()
        for link in rendered_fields.get('outlinks', []):
            if available <= 0:
                break
            try:
                url = normalize_url(link, run.config['allowed_hosts'])
            except ScanError:
                continue
            created_url = False
            for scenario in run.config.get('scenarios', ['baseline']):
                _, created = BrowserVisit.objects.get_or_create(run=run, url_hash=identity(url)[2], scenario=scenario, defaults={'url': url, 'depth': visit.depth+1, 'discovery': 'rendered_link'})
                created_url = created_url or created
            available -= int(created_url)
    return visit


@transaction.atomic
def finalize(run_id):
    run = BrowserRun.objects.select_related('batch').get(pk=run_id)
    Project.objects.select_for_update().get(pk=run.batch.project_id)
    run = check_run(run_id)
    batch = ImportBatch.objects.select_for_update().get(pk=run.batch_id)
    if batch.status in ('complete', 'incomplete'):
        return
    visits = list(run.visits.order_by('id'))
    def records():
        for visit in visits:
            yield {'locator': f'browser:visit:{visit.pk}', 'data': {'url': visit.url, 'final_url': visit.final_url or visit.url,
                'status': visit.server_facts.get('initial_http_status', visit.http_status), 'final_status': visit.http_status, 'content_type': 'text/html' if visit.http_status else '',
                'measured_at': visit.measured_at.isoformat() if visit.measured_at else None, 'depth': visit.depth,
                'rendering': 'browser', 'browser_visit_id': visit.pk, 'privacy_scenario': visit.scenario, **visit.rendered_facts,
                **({'error': 'Browsermeting onvolledig of mislukt.'} if visit.status == 'failed' else {})}}
    publish_records(batch_id=batch.pk, records=records(), derive_assessment=False)
    by_locator = dict(batch.records.values_list('locator', 'id'))
    for visit in visits:
        visit.source_record_id = by_locator[f'browser:visit:{visit.pk}']
    BrowserVisit.objects.bulk_update(visits, ['source_record'], batch_size=100)
    complete = sum(visit.status == 'complete' for visit in visits)
    batch.status = 'complete' if complete == len(visits) else 'incomplete'
    batch.warnings = ['Begrensde browsersteekproef; uitsluitend onderzochte pagina’s beoordeeld. Geen volledige toegankelijkheidsaudit of veldmeting.',
        'Performance bevat wachttijd door het ingestelde verzoektempo en de gevalideerde transportlaag.']
    batch.record_count = len(visits)
    batch.save(update_fields=['status', 'warnings', 'record_count'])
    run.coverage = {**run.coverage, 'visited': len(visits), 'complete': complete, 'incomplete_or_failed': len(visits)-complete,
        'visited_urls': len({v.url_hash for v in visits}), 'scenario_count': len(run.config.get('scenarios', ['baseline'])),
        'sample_limit_reached': len({v.url_hash for v in visits}) >= run.config['max_pages']}
    run.completed_at = timezone.now()
    run.save(update_fields=['coverage', 'completed_at'])
    from assessments.services import rebuild_batch
    basic_analysis = rebuild_batch(batch.pk)
    for visit in visits:
        screenshot = visit.artifacts.filter(kind='screenshot').defer('body').first()
        if screenshot:
            FindingEvidence.objects.filter(finding__result__run=basic_analysis, source_record_id=visit.source_record_id).update(browser_artifact=screenshot)
    definition, _ = AnalysisDefinition.objects.get_or_create(key='browser-basics', version='1', defaults={'method': 'rules', 'question': 'Welke toegankelijkheidsproblemen en kapotte browserresources zijn aangetroffen?'})
    analysis = AnalysisRun.objects.create(batch=batch, definition=definition, extractor_version='browser-v1', status='complete', completed_at=timezone.now(), coverage=run.coverage)
    counts = {}
    for visit in visits:
        result = AnalysisResult.objects.create(run=analysis, source_record_id=visit.source_record_id, outcome='unknown' if visit.status != 'complete' or visit.coverage.get('a11y_incomplete') else 'pass', answer={'browser_visit_id': visit.pk, 'coverage': visit.coverage, 'metrics': visit.metrics, 'differences': visit.differences, 'engine': run.engine})
        screenshot = visit.artifacts.filter(kind='screenshot').defer('body').first()
        problems = [('accessibility_' + issue.rule, 'high' if issue.impact in ('critical', 'serious') else 'medium', issue.title, issue.details) for issue in visit.accessibility_issues.all()]
        problems.extend(('broken_resource', 'medium', 'Browserresource geeft een HTTP-fout', {'source_url': visit.final_url or visit.url, 'destination': resource.url, 'status': resource.status, 'resource_type': resource.resource_type}) for resource in visit.resources.filter(status__gte=400))
        for rule, severity, title, details in problems:
            finding = Finding.objects.create(result=result, rule=rule[:80], severity=severity, title=title[:255], recommendation='Controleer het betrokken element of de resource en verifieer de oplossing.', details={'url': visit.final_url or visit.url, 'browser_visit_id': visit.pk, **details})
            FindingEvidence.objects.create(finding=finding, source_record_id=visit.source_record_id, browser_artifact=screenshot, role='page', snapshot=details)
            counts[rule] = counts.get(rule, 0)+1
            result.outcome = 'issue'
        result.save(update_fields=['outcome'])
    analysis.summary = counts
    analysis.save(update_fields=['summary'])

    from site_intelligence.services import schedule_enrichment
    transaction.on_commit(lambda: schedule_enrichment(batch.pk, batch.created_by_id))
