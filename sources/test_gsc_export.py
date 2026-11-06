import csv
import io
import zipfile
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from projects.models import Project, Membership
from .file_detection import detect_export
from .models import ImportBatch
from .services import publish_records


def zip_export(urls=None, filename='site-Coverage-Valid-2026-11-06.zip'):
    stream = io.BytesIO()
    urls = urls or ['https://example.com/a', 'https://www.example.com/b',
                    'https://blog.example.com/c', 'https://example.com.evil.test/d']
    table = io.StringIO(newline='')
    writer = csv.writer(table)
    writer.writerow(['URL', 'Last crawled'])
    writer.writerows((url, '2026-11-01') for url in urls)
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('Table.csv', table.getvalue())
        archive.writestr('Chart.csv', 'Date,Affected pages\n2026-11-01,4660\n')
        archive.writestr('Metadata.csv', 'Property,Value\nSitemap,All known pages\n')
    return SimpleUploadedFile(filename, stream.getvalue())


class ZIPDetectionTests(SimpleTestCase):
    def test_detects_zip_structure_and_preserves_context(self):
        parsed = detect_export(zip_export(filename='renamed.zip'))
        self.assertEqual(parsed['source'], 'gsc')
        self.assertEqual(parsed['summary']['reported_total'], 4660)
        self.assertEqual(parsed['summary']['report_date'], '2026-11-01')
        self.assertEqual(parsed['summary']['report_status'], 'unknown')
        self.assertEqual(parsed['rows'][0]['Last crawled'], '2026-11-01')

    def test_rejects_unrelated_and_oversized_zip(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('other.csv', 'a,b')
        with self.assertRaises(ValueError):
            detect_export(SimpleUploadedFile('other.zip', stream.getvalue()))
        with patch('sources.gsc_export.MAX_BYTES', 5), self.assertRaises(ValueError):
            detect_export(zip_export())


class GSCImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('gsc-file-user')
        self.project = Project.objects.create(name='Example', website='https://example.com/', domain='example.com', created_by=self.user)
        Membership.objects.create(project=self.project, user=self.user, role='admin')
        self.client.force_login(self.user)
        self.url = reverse('ui:import', args=[self.project.pk])

    def test_filters_file_rows_and_keeps_report_total_and_user(self):
        response = self.client.post(self.url, {'file': zip_export()})
        batch = ImportBatch.objects.get()
        self.assertEqual(batch.record_count, 2)
        self.assertEqual(batch.created_by, self.user)
        self.assertEqual(batch.request_metadata['excluded_rows'], 2)
        self.assertEqual(batch.request_metadata['reported_total'], 4660)
        self.assertContains(self.client.get(response.url), '2 toegestane URLs')
        self.client.post(response.url, {'action': 'confirm', 'report_status': 'indexed'})
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'complete')
        detail = self.client.get(reverse('ui:batch', args=[self.project.pk, batch.pk]))
        self.assertContains(detail, 'https://example.com/a')
        self.assertContains(detail, '2026-11-01')
        self.client.post(self.url, {'file': zip_export()})
        self.assertEqual(ImportBatch.objects.count(), 1)

    def test_subdomain_option_and_rule_change_before_confirmation(self):
        self.project.include_subdomains = True
        self.project.save()
        response = self.client.post(self.url, {'file': zip_export()})
        batch = ImportBatch.objects.get()
        self.assertEqual(batch.record_count, 3)
        self.project.include_subdomains = False
        self.project.save()
        self.client.post(response.url, {'action': 'confirm', 'report_status': 'indexed'})
        batch.refresh_from_db()
        self.assertEqual(batch.record_count, 2)
        self.assertEqual(batch.request_metadata['excluded_rows'], 2)

    def test_api_publication_uses_same_host_rules(self):
        batch = ImportBatch.objects.create(project=self.project, source='gsc', method='api', label='API', created_by=self.user)
        records = [{'locator': str(i), 'data': {'url': url}} for i,url in enumerate([
            'https://example.com/a', 'https://www.example.com/b', 'https://blog.example.com/c', 'https://example.com.evil.test'])]
        publish_records(batch_id=batch.pk, records=records)
        batch.refresh_from_db()
        self.assertEqual(batch.record_count, 2)
        self.assertEqual(batch.request_metadata['host_filter']['excluded_rows'], 2)

    def test_screamingfrog_file_uses_same_filter(self):
        from .test_screamingfrog import export
        first = export('https://example.com/a').read().decode('utf-8-sig')
        second = export('https://elsewhere.test/a').read().decode('utf-8-sig')
        rows = first + second.split('\r\n', 1)[1]
        response = self.client.post(self.url, {'file': SimpleUploadedFile('internal_all.csv', rows.encode())})
        self.assertEqual(response.status_code, 302)
        batch = ImportBatch.objects.get()
        self.assertEqual(batch.record_count, 1)
        self.assertEqual(batch.request_metadata['excluded_rows'], 1)
