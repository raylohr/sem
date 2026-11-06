import json
from types import SimpleNamespace
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from projects.models import Project, Membership
from sources.models import ImportBatch, SourceRecord
from sources.services import publish_records, delete_batch
from scans.extraction import extract_page
from scans.models import ScanDocument
from catalog.services import index_batch
from catalog.models import ProjectURL
from .models import PageMeasurement, LinkObservation, AnalysisRun, AnalysisResult, Finding, ReprocessingJob
from .services import rebuild_batch
from .tasks import process_reprocessing


class ExtractionTests(SimpleTestCase):
    def extract(self, html):
        return extract_page(SimpleNamespace(url='https://example.com/base', status=200,
            headers={'content-type': 'text/html'}, body=html.encode()))[0]

    def test_svg_titles_are_accessible_graphic_labels_not_document_titles(self):
        fields = self.extract('<head><title>Page title</title></head><body><svg><title>Chart label</title><svg><title>Nested</title></svg></svg><h1>Heading</h1>')
        self.assertEqual(fields['titles'], ['Page title'])
        self.assertEqual(fields['h1'], ['Heading'])
        self.assertEqual(self.extract('<title>One</title><title>Two</title>')['titles'], ['One','Two'])

    def test_anchor_details_duplicates_base_and_robots_provenance(self):
        fields = self.extract('<base href="/docs/"><meta name="googlebot" content="noindex"><a href="x" rel="nofollow sponsored">Read <b>more</b></a><a href="x"><img alt="Photo"></a>')
        self.assertEqual(fields['outlinks'], ['https://example.com/docs/x'] * 2)
        self.assertEqual(fields['link_details'][0]['anchor_text'], 'Read more')
        self.assertEqual(fields['link_details'][0]['rel'], ['nofollow', 'sponsored'])
        self.assertEqual(fields['link_details'][1]['anchor_text'], 'Photo')
        self.assertEqual(fields['robot_directives'][0]['agent'], 'googlebot')
        self.assertIn('noindex', fields['indexability_reasons'])

    def test_structured_metadata_obeys_existing_budget(self):
        from scans.large import bounded_metadata
        fields = self.extract('<meta name="robots" content="' + 'x' * 1500000 + '"><a href="/' + 'a' * 100000 + '">' + 'z' * 100000 + '</a>')
        bounded = bounded_metadata(fields)
        self.assertLessEqual(len(json.dumps(bounded).encode()), 1024**2)
        self.assertTrue(bounded['analysis_truncated'])
        self.assertTrue(bounded['links_truncated'])

    def test_link_cap_is_explicit(self):
        fields = self.extract('<a href="/x">x</a>' * 2001)
        self.assertEqual(len(fields['outlinks']), 2000)
        self.assertEqual(fields['link_count'], 2001)
        self.assertTrue(fields['links_truncated'])


class AssessmentTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.owner = users.objects.create_user('assessment-owner')
        self.viewer = users.objects.create_user('assessment-viewer')
        self.outsider = users.objects.create_user('assessment-other')
        self.project = Project.objects.create(name='Assessment', website='https://example.com/', created_by=self.owner)
        Membership.objects.create(project=self.project, user=self.owner, role='admin')
        Membership.objects.create(project=self.project, user=self.viewer, role='viewer')

    def batch(self, source='semscan', kind='crawl'):
        return ImportBatch.objects.create(project=self.project, source=source, method='scan', created_by=self.owner,
            label='Crawl', request_metadata={'scan': kind})

    def page(self, path='/', status=200, **overrides):
        data = {'url': 'https://example.com' + path, 'final_url': 'https://example.com' + path,
            'status': status, 'final_status': status, 'content_type': 'text/html',
            'measured_at': '2026-11-03T10:00:00+00:00', 'titles': ['Title'], 'meta_descriptions': ['Description'],
            'h1': ['Heading'], 'h2': [], 'outlinks': [], 'link_details': [], 'links_truncated': False,
            'word_count': 12, 'response_time': 0.125, 'depth': 0}
        data.update(overrides)
        return data

    def publish(self, batch, pages):
        publish_records(batch_id=batch.pk, records=[{'locator': f'page:{i}', 'data': data} for i, data in enumerate(pages)])
        return batch.analysis_runs.latest('started_at')

    def test_protected_email_404_is_reviewable_and_real_broken_links_keep_correct_evidence(self):
        fields = extract_page(SimpleNamespace(url='https://example.com/',status=200,headers={'content-type':'text/html'},
            body=b'<title>Title</title><meta name="description" content="Description"><h1>Heading</h1><a class="__cf_email__" href="/cdn-cgi/l/email-protection#abc">Email</a><a href="/broken">Broken</a>'))[0]
        batch = self.batch()
        run = self.publish(batch,[self.page(**fields),self.page('/cdn-cgi/l/email-protection',404),self.page('/broken',404)])
        findings = Finding.objects.filter(result__run=run,rule='link_to_404')
        self.assertEqual(findings.count(),1)
        self.assertEqual(findings.get().details['destination'],'https://example.com/broken')
        self.assertTrue(findings.get().evidence.filter(role='target',snapshot__url='https://example.com/broken').exists())
        self.assertEqual(run.summary['protected_email_links'],1)
        # An ordinary link to that same path is still a measured broken link.
        batch2=self.batch()
        run2=self.publish(batch2,[self.page(link_details=[{'url':'https://example.com/cdn-cgi/l/email-protection'}]),self.page('/cdn-cgi/l/email-protection',404)])
        self.assertEqual(Finding.objects.filter(result__run=run2,rule='link_to_404').count(),1)

    def test_same_batch_404_redirect_and_unmeasured_links(self):
        batch = self.batch()
        links = [{'url': 'https://example.com/missing', 'anchor_text': 'Broken', 'rel': ['nofollow'], 'locator': 'a[1]'},
                 {'url': 'https://example.com/redirect', 'anchor_text': 'Redirect'}, {'url': 'https://other.example/unknown'}]
        run = self.publish(batch, [self.page(link_details=links), self.page('/missing', 404),
            self.page('/redirect', 301, final_status=404, final_url='https://example.com/missing')])
        findings = Finding.objects.filter(result__run=run, rule='link_to_404')
        self.assertEqual(findings.count(), 2)
        self.assertEqual(findings.first().evidence.count(), 2)
        self.assertEqual(findings.first().details['anchor_text'], 'Broken')
        result = run.results.get(source_record__data__url='https://example.com/')
        self.assertEqual(result.outcome, 'issue')
        self.assertEqual(result.answer['links']['unmeasured_targets'], 0)
        self.assertEqual(PageMeasurement.objects.get(batch=batch, http_status=200).response_time_ms, 125)
        # Another batch's 404 must never supply evidence for this crawl.
        another = self.batch()
        self.publish(another, [self.page(link_details=[{'url': 'https://example.com/missing'}])])
        self.assertFalse(Finding.objects.filter(result__run__batch=another, rule='link_to_404').exists())
        self.assertGreater(another.analysis_runs.get().results.get().answer['links']['unmeasured_targets'], 0)

    def test_reextract_keeps_raw_records_dates_and_original_noindex(self):
        batch = self.batch()
        data = self.page(outlinks=['https://example.com/missing'], is_indexable=False, robots=['noindex'])
        data.pop('link_details'); data.pop('links_truncated')
        self.publish(batch, [data, self.page('/missing', 404)])
        raw_before = list(batch.records.values_list('data', flat=True))
        document = ScanDocument.objects.create(batch=batch, index=0, url=data['url'], status=200,
            content_type='text/html', fetched_at=timezone.now(), body=b'<title>Recovered</title><meta name="description" content="D"><h1>H</h1><a href="/missing" rel="nofollow">Evidence</a>')
        run = rebuild_batch(batch.pk, recover_documents=True)
        page = PageMeasurement.objects.get(batch=batch, http_status=200)
        self.assertEqual(page.facts['titles'], ['Recovered'])
        self.assertFalse(page.is_indexable)
        self.assertEqual(page.measured_at.isoformat(), '2026-11-03T10:00:00+00:00')
        self.assertEqual(page.links.get().anchor_text, 'Evidence')
        self.assertEqual(list(batch.records.values_list('data', flat=True)), raw_before)
        finding = Finding.objects.get(result__run=run, rule='link_to_404')
        self.assertEqual(finding.evidence.get(role='source').document_id, document.pk)
        self.assertEqual(batch.analysis_runs.count(), 2)

    def test_unknown_failed_page_not_reported_as_missing_metadata(self):
        run = self.publish(self.batch(), [{'url': 'https://example.com/', 'error': 'timeout'}])
        self.assertEqual(run.results.get().outcome, 'unknown')
        self.assertFalse(Finding.objects.filter(result__run=run).exists())
        self.assertEqual(run.coverage['unknown_links'], 1)

    def test_metadata_and_duplicate_text_with_empty_hash_excluded(self):
        batch = self.batch()
        run = self.publish(batch, [self.page(titles=[], meta_descriptions=[], h1=[], text_hash='a' * 64),
            self.page('/copy', text_hash='a' * 64), self.page('/empty')])
        self.assertEqual(run.summary['duplicate_text'], 2)
        self.assertEqual(run.summary['missing_titles'], 1)
        self.assertEqual(run.summary['missing_meta_descriptions'], 1)
        self.assertEqual(run.summary['missing_h1'], 1)
        self.assertEqual(run.results.count(), 3)

    def test_reindex_replaces_measurements_preserves_previous_analysis(self):
        batch = self.batch()
        original = self.publish(batch, [self.page(link_details=[{'url': 'https://example.com/x'}]), self.page('/x', 404)])
        original_evidence = list(Finding.objects.filter(result__run=original).order_by('id').values_list('details', flat=True))
        index_batch(batch.pk)
        self.assertEqual(PageMeasurement.objects.filter(batch=batch).count(), 2)
        self.assertEqual(LinkObservation.objects.filter(source__batch=batch).count(), 1)
        self.assertEqual(list(Finding.objects.filter(result__run=original).order_by('id').values_list('details', flat=True)), original_evidence)

    def test_batch_deletion_cascades_and_other_batch_survives(self):
        batch = self.batch(); other = self.batch()
        run = self.publish(batch, [self.page(titles=[])])
        self.publish(other, [self.page()])
        with patch('sources.tasks.enqueue_cleanup'):
            delete_batch(batch_id=batch.pk, project=self.project, actor=self.owner)
        self.assertFalse(AnalysisRun.objects.filter(pk=run.pk).exists())
        self.assertFalse(PageMeasurement.objects.filter(batch_id=batch.pk).exists())
        self.assertEqual(other.analysis_runs.count(), 1)
        self.assertEqual(ProjectURL.objects.filter(project=self.project).count(), 1)

    def test_rebuild_rolls_back_measurements_on_analysis_failure(self):
        batch = self.batch(); run = self.publish(batch, [self.page()])
        page_id = PageMeasurement.objects.get(batch=batch).pk
        with patch('assessments.services.analyze', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                rebuild_batch(batch.pk)
        self.assertTrue(PageMeasurement.objects.filter(pk=page_id).exists())
        self.assertEqual(batch.analysis_runs.count(), 1)

    def test_views_evidence_export_and_permissions(self):
        batch = self.batch(); run = self.publish(batch, [self.page(titles=[])])
        ScanDocument.objects.create(batch=batch, index=0, url='https://example.com/', status=200,
            content_type='text/html', fetched_at=timezone.now(), body=b'<h1>H</h1>')
        run = rebuild_batch(batch.pk, recover_documents=True)
        finding = Finding.objects.filter(result__run=run).first()
        page = PageMeasurement.objects.get(batch=batch)
        urls = [reverse('ui:assessments:overview', args=[self.project.pk]),
                reverse('ui:assessments:run', args=[self.project.pk, run.pk]),
                reverse('ui:assessments:finding', args=[self.project.pk, finding.pk]),
                reverse('ui:assessments:page', args=[self.project.pk, page.pk]),
                reverse('ui:catalog:detail', args=[self.project.pk, page.url_id])]
        self.client.force_login(self.viewer)
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertIn('no-store', response['Cache-Control'])
        export_url = reverse('ui:assessments:export', args=[self.project.pk, run.pk])
        response = self.client.get(export_url)
        rows = [json.loads(row) for row in b''.join(response.streaming_content).decode().splitlines()]
        self.assertEqual(rows[0]['run_id'], str(run.pk))
        self.assertTrue(any(row['type'] == 'result' for row in rows))
        post = reverse('ui:assessments:reprocess', args=[self.project.pk, batch.pk])
        self.assertEqual(self.client.post(post).status_code, 403)
        self.client.force_login(self.outsider)
        for url in urls + [export_url]:
            self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(post).status_code, 404)

    def test_reprocessing_duplicate_delivery_and_revoked_access(self):
        batch = self.batch(); self.publish(batch, [self.page()])
        self.client.force_login(self.owner)
        post = reverse('ui:assessments:reprocess', args=[self.project.pk, batch.pk])
        self.assertEqual(self.client.get(post).status_code, 405)
        with patch('assessments.views.process_reprocessing.delay') as enqueue:
            self.assertEqual(self.client.post(post).status_code, 302)
            self.assertEqual(self.client.post(post).status_code, 302)
            self.assertEqual(enqueue.call_count, 1)
        job = ReprocessingJob.objects.get(batch=batch)
        process_reprocessing(str(job.pk)); process_reprocessing(str(job.pk))
        self.assertEqual(batch.analysis_runs.count(), 2)
        job.refresh_from_db(); self.assertEqual(job.status, 'complete')
        revoked = ReprocessingJob.objects.create(batch=batch, actor=self.owner)
        Membership.objects.filter(project=self.project, user=self.owner).delete()
        process_reprocessing(str(revoked.pk))
        revoked.refresh_from_db(); self.assertEqual(revoked.status, 'failed')
        self.assertEqual(batch.analysis_runs.count(), 2)

    def test_non_crawl_source_is_not_analyzed(self):
        batch = self.batch(kind='sitemap')
        publish_records(batch_id=batch.pk, records=[])
        self.assertFalse(batch.analysis_runs.exists())

    def test_duplicate_metadata_and_multiple_headings_are_distinct_rules(self):
        run = self.publish(self.batch(), [self.page(titles=['Same', 'Extra'], h1=['A', 'B']), self.page('/second', titles=['Same'])])
        self.assertEqual(run.summary['duplicate_titles'], 2)
        self.assertEqual(run.summary['multiple_titles'], 1)
        self.assertEqual(run.summary['multiple_h1'], 1)

    def test_redirect_alias_does_not_create_duplicate_content_issue(self):
        run = self.publish(self.batch(), [self.page(text_hash='a' * 64),
            self.page('/old', status=301, final_status=200, final_url='https://example.com/', text_hash='a' * 64)])
        self.assertNotIn('duplicate_text', run.summary)
        self.assertNotIn('duplicate_titles', run.summary)

    def test_unknown_link_coverage_never_becomes_a_pass(self):
        data = self.page(text_hash='b' * 64)
        data.pop('links_truncated')
        data.pop('link_details')
        run = self.publish(self.batch(), [data])
        self.assertIn('unknown', run.results.get().answer['checks'].values())

    def test_malicious_content_is_escaped_in_evidence(self):
        data = self.page(titles=[], link_details=[{'url': 'https://example.com/missing', 'anchor_text': '<script>alert(1)</script>'}])
        run = self.publish(self.batch(), [data, self.page('/missing', 404)])
        finding = Finding.objects.get(result__run=run, rule='link_to_404')
        self.client.force_login(self.owner)
        response = self.client.get(reverse('ui:assessments:finding', args=[self.project.pk, finding.pk]))
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        response = self.client.get(reverse('ui:assessments:run', args=[self.project.pk, run.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Link leidt naar een 404')

    def test_csrf_required_and_queue_failure_can_be_retried(self):
        from django.test import Client
        batch = self.batch(); self.publish(batch, [self.page()])
        endpoint = reverse('ui:assessments:reprocess', args=[self.project.pk, batch.pk])
        client = Client(enforce_csrf_checks=True); client.force_login(self.owner)
        self.assertEqual(client.post(endpoint).status_code, 403)
        self.client.force_login(self.owner)
        with patch('assessments.views.process_reprocessing.delay', side_effect=RuntimeError('offline')):
            self.assertEqual(self.client.post(endpoint).status_code, 302)
        self.assertEqual(ReprocessingJob.objects.get(batch=batch).status, 'failed')
        with patch('assessments.views.process_reprocessing.delay') as enqueue:
            self.client.post(endpoint)
            self.assertEqual(enqueue.call_count, 1)
