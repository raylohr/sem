"""Re-extract evidence locally, then derive scoped, reproducible findings."""
from collections import Counter
from itertools import islice
import math
import hashlib
from types import SimpleNamespace
from django.db import transaction
from django.db.models import Count
from django.db.models.functions import MD5
from django.utils import timezone
from catalog.adapters import normalize_url
from catalog.models import URLObservation
from projects.models import Project
from sources.models import ImportBatch
from scans.extraction import extract_page
from scans.models import ScanDocument
from .r2 import settings_for
from .models import (PageMeasurement, LinkObservation, AnalysisDefinition, AnalysisRun,
                     AnalysisResult, Finding, FindingEvidence)

EXTRACTOR_VERSION = 'assessment-v8'
RULE_VERSION = '8'


def chunks(iterator, size=100):
    iterator = iter(iterator)
    while chunk := list(islice(iterator, size)):
        yield chunk


def integer(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def reextract(data, document=None):
    """Never rewrite source records; headers not retained historically stay unknown."""
    fields = dict(data)
    recovered = False
    if document and ('html' in document.content_type.lower()):
        recovered_fields, _ = extract_page(SimpleNamespace(url=document.url, status=document.status,
            headers={**data.get('response_headers',{}),'content-type': document.content_type}, body=bytes(document.body),
            measurements=data.get('transfer_measurements',{}),header_values=data.get('response_header_values',{}),header_values_observed=data.get('header_values_observed',False)))
        # The original robots/indexability facts include non-retained HTTP headers.
        for key in ('robots', 'robot_directives', 'is_indexable', 'indexability_reasons', 'indexability_basis'):
            recovered_fields.pop(key, None)
        recovered_fields['headers_observed'] = data.get('headers_observed', False)
        if not data.get('headers_observed'):
            recovered_fields.pop('response_headers', None)
        if not data.get('headers_observed') and data.get('canonical_sources'):
            recovered_fields['canonical_sources'] += [item for item in data['canonical_sources'] if item.get('source')=='header']
            recovered_fields['canonicals'] += [item['url'] for item in data['canonical_sources'] if item.get('source')=='header']
        fields.update(recovered_fields)
        recovered = True
    return fields, document, recovered


@transaction.atomic
def rebuild_batch(batch_id, *, recover_documents=False):
    project_id = ImportBatch.objects.values_list('project_id', flat=True).get(pk=batch_id)
    Project.objects.select_for_update().get(pk=project_id)
    batch = ImportBatch.objects.select_for_update().get(pk=batch_id)
    if batch.status not in ('complete', 'incomplete'):
        raise ValueError('Alleen gepubliceerde batches kunnen worden geanalyseerd.')
    if batch.scan_kind not in ('crawl', 'browser') or batch.source not in ('semscan', 'crawler'):
        return None
    from scans.activity import phase
    progress_batch=batch.pk if batch.scan_target_id else None
    progress_name='browser_analysis' if batch.scan_kind=='browser' else 'analysis'
    phase(progress_batch,progress_name,done=0,total=batch.record_count)
    # Existing analysis runs retain source-record evidence when measurements rebuild.
    PageMeasurement.objects.filter(batch=batch).delete()
    coverage = Counter()
    inputs = URLObservation.objects.filter(batch=batch).select_related('source_record').order_by('id')
    for observations in chunks(inputs.iterator(chunk_size=50), 10 if recover_documents else 50):
        documents = {}
        final_urls = [obs.source_record.data.get('final_url') or obs.source_record.data.get('url') for obs in observations]
        digests = [hashlib.md5(url.encode()).hexdigest() for url in final_urls if isinstance(url, str)]
        document_query = ScanDocument.objects.filter(batch=batch, url__in=final_urls).alias(url_digest=MD5('url')).filter(url_digest__in=digests)
        if not recover_documents:
            document_query = document_query.defer('body')
        for document in document_query.order_by('index').iterator(chunk_size=5):
            documents[(document.url, document.status)] = document
        artifacts = {}
        if recover_documents and batch.scan_kind == 'browser':
            from browser_scans.models import BrowserArtifact
            artifacts = {item.visit.source_record_id:item for item in BrowserArtifact.objects.filter(
                visit__source_record_id__in=[obs.source_record_id for obs in observations],kind='rendered_dom').select_related('visit')}
        pages = []
        link_rows = []
        reference_data = []
        for obs in observations:
            original = obs.source_record.data
            data = dict(original)
            document = documents.get((data.get('final_url') or data.get('url'), data.get('final_status', data.get('status'))))
            recovered = False
            if recover_documents:
                data, document, recovered = reextract(data, document)
            artifact = artifacts.get(obs.source_record_id)
            if artifact:
                evidence_document = SimpleNamespace(url=artifact.visit.final_url or artifact.visit.url, status=artifact.visit.http_status,
                    content_type='text/html; charset=utf-8',body=artifact.body)
                data, _, recovered = reextract(data, evidence_document)
            html = 'html' in str(data.get('content_type', '')).lower()
            # A network error is not an empty, successfully inspected HTML page.
            inspected = html and not data.get('error') and data.get('final_status', data.get('status')) is not None
            detailed = isinstance(data.get('link_details'), list)
            links = data.get('link_details') if detailed else [dict(url=value) for value in data.get('outlinks', []) if isinstance(value, str)]
            links_known = inspected and (detailed or isinstance(data.get('outlinks'), list))
            page_coverage = {'html_inspected': bool(inspected), 'links_known': bool(links_known),
                'link_details_known': bool(detailed), 'links_truncated': data.get('links_truncated'),
                'legacy_link_limit_unknown': links_known and 'links_truncated' not in data,
                'analysis_truncated': bool(data.get('analysis_truncated')), 'recovered_from_document': recovered,
                'document_id': document.pk if document else None, 'browser_artifact_id':artifact.pk if artifact else None,
                'headers_recovered': False, 'rendering': 'browser' if batch.scan_kind == 'browser' else 'server'}
            if page_coverage['legacy_link_limit_unknown'] and len(links) >= 2000:
                page_coverage['links_truncated'] = True
            coverage['pages'] += 1
            coverage['html_inspected'] += bool(inspected)
            coverage['unknown_links'] += not links_known
            coverage['truncated_links'] += bool(page_coverage['links_truncated'] or page_coverage['analysis_truncated'])
            coverage['legacy_link_limit_unknown'] += bool(page_coverage['legacy_link_limit_unknown'])
            coverage['documents_recovered'] += recovered
            names = ('titles', 'meta_descriptions', 'h1', 'h2', 'canonicals', 'robots', 'robot_directives',
                     'language', 'hreflang', 'hreflang_truncated', 'canonical_sources', 'heading_sequence', 'headings_truncated',
                     'images', 'images_truncated', 'image_count', 'resource_urls', 'jsonld', 'jsonld_count', 'jsonld_truncated',
                     'headers_observed', 'response_headers', 'robots_allowed', 'googlebot_allowed', 'error', 'error_code',
                     'redirects', 'protected_email_locators', 'privacy_scenario', 'indexability_reasons', 'text_truncated', 'text_extraction_basis', 'body_hash', 'link_count',
                     'discovery_sources','sitemap_url','link_depth_basis','exploration_depth','html_audit','document_structure',
                     'fragment_targets','fragment_targets_complete','main_content','structured_data','transfer_measurements','resource_discovery','content_audit','heading_evidence','link_name_evidence','social_metadata','metadata_evidence','aria_references','resource_details','headers_truncated','response_header_values','header_values_observed')
            facts = {key: data[key] for key in names if key in data}
            for field in ('titles', 'meta_descriptions'):
                values = facts.get(field)
                if isinstance(values, list) and values:
                    facts['normalized_' + field] = ' '.join(str(values[0]).split()).casefold()
            for field in ('titles', 'meta_descriptions', 'h1', 'h2'):
                if isinstance(facts.get(field), list):
                    facts[field + '_count'] = len(facts[field])
                    facts[field + '_lengths'] = [len(str(value)) for value in facts[field]]
            response = data.get('response_time')
            page = PageMeasurement(batch=batch, source_record_id=obs.source_record_id, url_id=obs.url_id,
                measured_at=obs.measured_at, http_status=integer(data.get('status')),
                final_http_status=integer(data.get('final_status', data.get('status'))),
                final_url=data.get('final_url') or obs.original_url, content_type=str(data.get('content_type', ''))[:255],
                is_indexable=data.get('is_indexable') if isinstance(data.get('is_indexable'), bool) else None,
                word_count=integer(data.get('word_count')), response_time_ms=round(response * 1000) if isinstance(response, (int, float)) and math.isfinite(response) and 0 <= response < 10**8 else None,
                depth=integer(data.get('depth')), text_hash=str(data.get('text_hash') or '')[:64],
                extractor_version=EXTRACTOR_VERSION, facts=facts, coverage=page_coverage)
            pages.append(page)
            link_rows.append(links if links_known else [])
            reference_data.append(data)
        PageMeasurement.objects.bulk_create(pages, batch_size=100)
        phase(progress_batch,progress_name,done=coverage['pages'],total=batch.record_count)
        from scans.references import classify
        from .references import build
        from .models import ReferenceObservation
        for page,data in zip(pages,reference_data):
            ReferenceObservation.objects.bulk_create(build(page,data,batch.request_metadata),batch_size=500)
        links_to_create = []
        for page, links in zip(pages, link_rows):
            for position, link in enumerate(links):
                if not isinstance(link, dict):
                    continue
                identity = normalize_url(link.get('url'))
                if identity is None:
                    coverage['ignored_link_schemes_or_invalid'] += 1
                    continue
                links_to_create.append(LinkObservation(source=page, position=position, destination=identity[0],
                    destination_hash=identity[2], anchor_text=link.get('anchor_text'), rel=link.get('rel'),
                    locator=str(link.get('locator', ''))[:100], discovery_mode='rendered' if batch.scan_kind == 'browser' else 'server',
                    element_path=str(link.get('element_path',''))[:1024],region=str(link.get('region',''))[:24],target=str(link.get('target',''))[:80],
                    details={**{'reference':classify(link.get('url',''),page.final_url,batch.request_metadata,download=link.get('download',False),raw_reference=link.get('raw_url'))},**{key:link[key] for key in ('raw_url','element_type','reference_type','aria_label','aria_labelledby','title_attribute','static_name','name_basis','name_status','visible_text','images','hidden') if key in link}}))
                if len(links_to_create) >= 1000:
                    LinkObservation.objects.bulk_create(links_to_create, batch_size=1000)
                    links_to_create = []
        LinkObservation.objects.bulk_create(links_to_create, batch_size=1000)
    from .references import evaluate
    evaluate(batch)
    coverage['reference_version']='references-v1'
    coverage['reference_inventory_truncated']=PageMeasurement.objects.filter(batch=batch,coverage__references_truncated=True).exists()
    result=analyze(batch, dict(coverage))
    ReferenceObservation.objects.filter(source__batch=batch).update(analysis_run=result)
    phase(progress_batch,progress_name,status='complete',done=coverage['pages'],total=batch.record_count)
    return result


def analyze(batch, coverage):
    from .references import prepare_legacy_links
    prepare_legacy_links(batch)
    definition, _ = AnalysisDefinition.objects.get_or_create(key='crawl-basics', version=RULE_VERSION,
        defaults={'question': 'Welke technische pagina-, canonical-, taal- en interne linkproblemen zijn aantoonbaar in deze metingen?',
                  'output_schema': {'outcome': ['issue', 'pass', 'unknown', 'not_applicable']}})
    run = AnalysisRun.objects.create(batch=batch, definition=definition, extractor_version=EXTRACTOR_VERSION,
        coverage={**coverage, 'batch_status_at_analysis': batch.status, 'batch_warnings': batch.warnings,
                  'input_kind': 'published source records and optional stored HTML','schema_profiles':batch.request_metadata.get('schema_profiles',['google-breadcrumb-v1','google-product-snippet-v1']),'structured_validator':'structured-profile-v1/schema.org-30.1',
                  'analysis_settings':settings_for(batch.request_metadata.get('analysis_settings')),
                  'scope': 'same batch only; external and unvisited destinations are not assumed reachable'})
    pages = PageMeasurement.objects.filter(batch=batch)
    from .advanced import load_target_index, broken_links
    target_index=load_target_index(batch)
    summary = Counter()
    duplicate_hashes = set(pages.filter(final_http_status=200).exclude(text_hash='').values('text_hash').annotate(n=Count('final_url', distinct=True)).filter(n__gt=1).values_list('text_hash', flat=True))
    duplicates = {}
    for field in ('titles', 'meta_descriptions'):
        key = 'normalized_' + field
        duplicates[field] = set(pages.filter(final_http_status=200, **{'facts__has_key': key}).exclude(**{'facts__' + key: ''}).values('facts__' + key).annotate(n=Count('final_url', distinct=True)).filter(n__gt=1).values_list('facts__' + key, flat=True))
    titles = {'titles': 'Titel ontbreekt', 'meta_descriptions': 'Meta description ontbreekt', 'h1': 'H1 ontbreekt'}
    for page_chunk in chunks(pages.order_by('id').iterator(chunk_size=100)):
        results, checks_by_record = [], {}
        for page in page_chunk:
            checks = {}
            if page.coverage['html_inspected'] and page.final_http_status == 200:
                for field in titles:
                    checks[field] = ('pass' if any(str(x).strip() for x in page.facts[field]) else 'issue') if field in page.facts else 'unknown'
                for field in ('titles', 'meta_descriptions', 'h1'):
                    checks['multiple_' + field] = ('issue' if len(page.facts[field]) > 1 else 'pass') if field in page.facts else 'unknown'
                for field in ('titles', 'meta_descriptions'):
                    checks['duplicate_' + field] = 'issue' if page.facts.get('normalized_' + field) in duplicates[field] else ('pass' if field in page.facts else 'unknown')
                checks['duplicate_text'] = 'issue' if page.text_hash in duplicate_hashes else ('pass' if page.text_hash else 'unknown')
            else:
                checks = {name: 'not_applicable' if page.final_http_status and page.final_http_status != 200 else 'unknown' for name in titles}
            checks['links_to_404'] = 'pass' if page.coverage['links_known'] and not any(page.coverage.get(key) for key in ('links_truncated', 'legacy_link_limit_unknown', 'analysis_truncated')) else 'unknown'
            outcome = 'issue' if 'issue' in checks.values() else ('unknown' if 'unknown' in checks.values() else ('pass' if 'pass' in checks.values() else 'not_applicable'))
            if outcome == 'pass' and (not page.coverage['links_known'] or page.coverage['links_truncated'] or page.coverage['legacy_link_limit_unknown'] or page.coverage['analysis_truncated']):
                outcome = 'unknown'
            results.append(AnalysisResult(run=run, source_record_id=page.source_record_id, outcome=outcome,
                answer={'checks': checks, 'measured_at': page.measured_at.isoformat() if page.measured_at else None,
                        'extracted_at': page.extracted_at.isoformat(), 'coverage': page.coverage, 'input': {'url': page.final_url, 'http_status': page.http_status, 'final_http_status': page.final_http_status, 'text_hash': page.text_hash, 'facts': page.facts}}))
            checks_by_record[page.source_record_id] = (page, checks)
        AnalysisResult.objects.bulk_create(results, batch_size=100)
        findings = []
        for result in results:
            page, checks = checks_by_record[result.source_record_id]
            extra_titles = {'duplicate_text': 'Identieke paginatekst', 'multiple_titles': 'Meerdere titels', 'multiple_meta_descriptions': 'Meerdere meta descriptions', 'multiple_h1': 'Meerdere H1-koppen', 'duplicate_titles': 'Dubbele titel', 'duplicate_meta_descriptions': 'Dubbele meta description'}
            for field, title in {**titles, **extra_titles}.items():
                if checks.get(field) != 'issue':
                    continue
                rule = field if field in extra_titles else 'missing_' + field
                input_field = field.removeprefix('multiple_').removeprefix('duplicate_')
                findings.append(Finding(result=result, rule=rule, severity='medium', title=title,
                    recommendation='Controleer de pagina en bepaal of inhoud of verwijzingen moeten worden aangepast.',
                    details={'url': page.final_url, 'field': field, 'value': page.text_hash if field == 'duplicate_text' else page.facts.get(input_field)}))
                summary[rule] += 1
        Finding.objects.bulk_create(findings, batch_size=500)
        FindingEvidence.objects.bulk_create([FindingEvidence(finding=finding, source_record_id=finding.result.source_record_id,
            document_id=checks_by_record[finding.result.source_record_id][0].coverage.get('document_id'),
            browser_artifact_id=checks_by_record[finding.result.source_record_id][0].coverage.get('browser_artifact_id'), role='page', snapshot=finding.details) for finding in findings], batch_size=500)
    for link_chunk in chunks(broken_links(batch,target_index), 500):
        results = {r.source_record_id: r for r in AnalysisResult.objects.filter(run=run, source_record_id__in={link.source.source_record_id for link in link_chunk})}
        findings = []
        finding_links = []
        for link in link_chunk:
            result = results[link.source.source_record_id]
            from urllib.parse import urlsplit
            protected = (urlsplit(link.destination).path == '/cdn-cgi/l/email-protection'
                and urlsplit(link.destination).netloc == urlsplit(link.source.final_url).netloc
                and link.locator in link.source.facts.get('protected_email_locators', []))
            if protected:
                if result.answer['checks']['links_to_404'] != 'issue':
                    result.answer['checks']['links_to_404'] = 'unknown'
                if result.outcome == 'pass': result.outcome = 'unknown'
                result.answer['cloudflare_email_review_required'] = True
                summary['protected_email_links'] += 1
                continue
            result.outcome = 'issue'
            result.answer['checks']['links_to_404'] = 'issue'
            finding_links.append(link)
            findings.append(Finding(result=result, rule='link_to_404', severity='high',
                title='Link leidt naar een 404', recommendation='Herstel of verwijder de link; controleer een eventuele redirectketen.',
                details={'source_url': link.source.final_url, 'destination': link.destination, 'anchor_text': link.anchor_text,
                         'rel': link.rel, 'locator': link.locator, 'target_status': link.target_status, 'target_final_status': link.target_final_status}))
        Finding.objects.bulk_create(findings, batch_size=500)
        evidence = []
        for finding, link in zip(findings, finding_links):
            evidence.extend([
                FindingEvidence(finding=finding, source_record_id=link.source.source_record_id, document_id=link.source.coverage.get('document_id'), role='source', snapshot=finding.details),
                FindingEvidence(finding=finding, source_record_id=link.target_record, document_id=link.target_document, role='target', snapshot={'url': link.destination, 'http_status': link.target_status, 'final_http_status': link.target_final_status})])
        FindingEvidence.objects.bulk_create(evidence, batch_size=500)
        AnalysisResult.objects.bulk_update(list(results.values()), ['outcome', 'answer'], batch_size=500)
        summary['link_to_404'] += len(findings)
    from .advanced import update_link_coverage
    update_link_coverage(run,batch,target_index)
    from .advanced import extend_analysis
    extend_analysis(run, batch, summary,target_index)
    from .r4 import attach as attach_resources
    attach_resources(run,batch,summary)
    from .references import attach_findings
    attach_findings(run,batch,summary)
    run.summary = dict(summary)
    run.status = 'complete'
    run.completed_at = timezone.now()
    run.save(update_fields=['summary', 'status', 'completed_at', 'coverage'])
    return run
