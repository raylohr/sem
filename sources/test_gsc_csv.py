from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from projects.models import Project, Membership
from .file_detection import detect_export
from .models import ImportBatch


class GSCCSVDetectionTests(SimpleTestCase):
    def test_dutch_english_and_utf16_url_tables(self):
        for header, encoding, separator in [('Laatst gecrawld', 'utf-8-sig', ','),
                                             ('Last crawled', 'utf-16', '\t'),
                                             ('Laatst gecrawld', 'utf-8', ';')]:
            with self.subTest(header=header, encoding=encoding):
                raw = f'URL{separator}{header}\nhttps://example.com/a{separator}2026-10-13\n'.encode(encoding)
                result = detect_export(SimpleUploadedFile('renamed.csv', raw))
                self.assertEqual(result['source'], 'gsc')
                self.assertEqual(result['summary']['file_format'], 'csv')
                self.assertTrue(result['summary']['missing_report_context'])
                self.assertIsNone(result['summary']['reported_total'])
                self.assertEqual(result['rows'][0]['Last crawled'], '2026-10-13')

    def test_empty_and_malformed_tables_are_rejected(self):
        for raw in (b'URL,Last crawled\n', b'URL,Last crawled\nhttps://example.com,2026-10-13,extra\n'):
            with self.assertRaises(ValueError):
                detect_export(SimpleUploadedFile('Table.csv', raw))


class GSCCSVFlowTests(TestCase):
    def test_host_filter_preview_manual_reason_and_deduplication(self):
        user = get_user_model().objects.create_user('gsc-csv-user')
        project = Project.objects.create(name='CSV', website='https://example.com', domain='example.com', created_by=user)
        Membership.objects.create(project=project, user=user, role='admin')
        self.client.force_login(user)
        url = reverse('ui:import', args=[project.pk])
        raw = b'URL,Last crawled\nhttps://example.com/missing,2026-10-13\nhttps://elsewhere.test/a,2026-10-12\n'
        response = self.client.post(url, {'file': SimpleUploadedFile('Table.csv', raw)})
        batch = ImportBatch.objects.get()
        self.assertEqual(batch.created_by, user)
        self.assertEqual(batch.record_count, 1)
        self.assertEqual(batch.request_metadata['excluded_rows'], 1)
        self.assertContains(self.client.get(response.url), 'geen rapportmetadata')
        self.client.post(response.url, {'action':'confirm', 'report_status':'not_indexed', 'indexing_reason':'Niet gevonden (404)'})
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'complete')
        self.assertEqual(batch.records.get().data['gsc_indexing_reason'], 'Niet gevonden (404)')
        self.assertEqual(batch.records.get().data['gsc_report_status'], 'not_indexed')
        self.assertContains(self.client.get(reverse('ui:batch', args=[project.pk, batch.pk])), 'Niet gevonden (404)')
        self.client.post(url, {'file': SimpleUploadedFile('renamed.csv', raw)})
        self.assertEqual(ImportBatch.objects.count(), 1)
