import csv
import io
from datetime import date

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from projects.models import Project, Membership
from .ahrefs import BASE_COLUMNS, TITLE_COLUMNS
from .file_detection import detect_export
from .models import ImportBatch
from .url_inventory import project_inventory


def export(urls=None, *, issue=False, enriched=False, encoding='utf-8-sig', delimiter=',', filename='gmw_pages_2026-10-29_18-07-01.csv'):
    headers = sorted(BASE_COLUMNS | (TITLE_COLUMNS if issue else {'No. of all inlinks', 'First found at'}))
    if enriched:
        headers += ['No. of backlinks', 'No. of referring domains', 'No. of keywords with Top 3 position']
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=headers, delimiter=delimiter)
    writer.writeheader()
    for url in urls or ['https://example.com/a', 'https://www.example.com/b', 'https://external.test/c']:
        row = dict.fromkeys(headers, '') | {
            'URL': url, 'PR': '96', 'Title': '' if issue else 'Een titel, met komma',
            'Content type': 'text/html; charset=utf-8', 'Is rendered page': 'false',
            'HTTP status code': '200', 'Organic traffic': '1152', 'Depth': '0',
            'Is indexable page': 'true'}
        if issue:
            row.update({'No. of titles': '0'})
        if enriched:
            row['No. of backlinks'] = '1545'
        writer.writerow(row)
    return SimpleUploadedFile(filename, stream.getvalue().encode(encoding))


class AhrefsDetectionTests(SimpleTestCase):
    def test_detects_pages_and_preserves_optional_columns_and_url(self):
        parsed = detect_export(export(['https://example.com/Path/?a=1&b=2'], enriched=True))
        self.assertEqual(parsed['source'], 'ahrefs')
        self.assertEqual(parsed['summary']['type'], 'ahrefs_site_audit_pages')
        self.assertEqual(parsed['summary']['suggested_date'], '2026-10-29')
        self.assertIsNone(parsed['measured_at'])
        self.assertEqual(parsed['rows'][0]['URL'], 'https://example.com/Path/?a=1&b=2')
        self.assertEqual(parsed['rows'][0]['No. of backlinks'], '1545')
        self.assertEqual(parsed['rows'][0]['Title'], 'Een titel, met komma')

    def test_detects_title_issue_by_columns_even_if_renamed(self):
        parsed = detect_export(export(issue=True, filename='renamed.csv'))
        self.assertEqual(parsed['summary']['type'], 'ahrefs_site_audit_missing_titles')
        self.assertIsNone(parsed['summary']['suggested_date'])
        self.assertEqual(parsed['rows'][0]['No. of titles'], '0')

    def test_utf16_tab_export(self):
        self.assertEqual(detect_export(export(encoding='utf-16', delimiter='\t'))['source'], 'ahrefs')

    def test_rejects_bad_url_malformed_rows_and_site_explorer(self):
        with self.assertRaisesMessage(ValueError, 'ongeldig URL'):
            detect_export(export(['javascript:alert(1)']))
        raw = export().read() + b'extra,broken,row\n'
        with self.assertRaisesMessage(ValueError, 'aantal kolommen'):
            detect_export(SimpleUploadedFile('pages.csv', raw))
        with self.assertRaisesMessage(ValueError, 'Site Explorer'):
            detect_export(SimpleUploadedFile('bbl.csv', b'UR,Page URL,Referring domains\n17,https://example.com,10\n'))


class AhrefsImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('ahrefs-user')
        self.project = Project.objects.create(name='Ahrefs', website='https://example.com', domain='example.com', created_by=self.user)
        Membership.objects.create(project=self.project, user=self.user, role='editor')
        self.client.force_login(self.user)
        self.url = reverse('ui:import', args=[self.project.pk])

    def test_preview_filters_hosts_and_confirmed_date_reaches_inventory(self):
        response = self.client.post(self.url, {'file': export(enriched=True)})
        batch = ImportBatch.objects.get()
        self.assertEqual(batch.source, 'ahrefs')
        self.assertEqual(batch.created_by, self.user)
        self.assertEqual(batch.record_count, 2)
        self.assertEqual(batch.request_metadata['excluded_rows'], 1)
        preview = self.client.get(response.url)
        self.assertContains(preview, 'Site Audit · Pagina’s')
        self.assertContains(preview, '2026-10-29')
        self.assertNotContains(preview, 'GSC gerapporteerd totaal')
        self.client.post(response.url, {'action': 'confirm', 'observed_on': '2026-10-29'})
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'complete')
        self.assertEqual(batch.request_metadata['date_basis'], 'user_confirmed')
        columns, rows = project_inventory(self.project)
        self.assertEqual([c['key'] for c in columns], ['ahrefs'])
        self.assertEqual(rows[0]['observations']['ahrefs']['date'], date(2026, 10, 29))
        detail = self.client.get(reverse('ui:batch', args=[self.project.pk, batch.pk]))
        self.assertContains(detail, 'Organisch verkeer (Ahrefs)')
        self.assertContains(detail, 'Een titel, met komma')
        self.assertNotContains(detail, 'Laatst gecrawld door Google')
        self.client.post(self.url, {'file': export(enriched=True)})
        self.assertEqual(ImportBatch.objects.count(), 1)

    def test_invalid_date_keeps_preview_and_empty_date_uses_import_fallback(self):
        response = self.client.post(self.url, {'file': export(issue=True)})
        invalid = self.client.post(response.url, {'action': 'confirm', 'observed_on': 'invalid'})
        self.assertEqual(invalid.status_code, 200)
        batch = ImportBatch.objects.get()
        self.assertEqual(batch.status, 'pending')
        self.client.post(response.url, {'action': 'confirm', 'observed_on': ''})
        batch.refresh_from_db()
        self.assertIsNone(batch.measured_at)
        _, rows = project_inventory(self.project)
        self.assertTrue(rows[0]['observations']['ahrefs']['fallback'])
        self.assertEqual(batch.request_metadata['type'], 'ahrefs_site_audit_missing_titles')

    def test_viewer_cannot_upload_and_all_foreign_rows_create_no_batch(self):
        response = self.client.post(self.url, {'file': export(['https://elsewhere.test/'])})
        self.assertContains(response, 'Geen URLs binnen de toegestane hosts')
        self.assertFalse(ImportBatch.objects.exists())
        Membership.objects.filter(project=self.project, user=self.user).update(role='viewer')
        self.assertEqual(self.client.post(self.url, {'file': export()}).status_code, 403)


class AhrefsExpectedColumnTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('ahrefs-columns')
        self.project = Project.objects.create(name='Kolommen', website='https://example.com', domain='example.com', created_by=self.user)
        Membership.objects.create(project=self.project, user=self.user, role='editor')
        self.client.force_login(self.user)
        self.url = reverse('ui:import', args=[self.project.pk])

    def test_pages_export_without_link_columns_warns_but_imports(self):
        from .ahrefs import EXPECTED_COLUMNS
        parsed = detect_export(export())
        self.assertEqual(parsed['summary']['missing_expected_columns'], list(EXPECTED_COLUMNS['ahrefs_site_audit_pages']))
        response = self.client.post(self.url, {'file': export()})
        batch = ImportBatch.objects.get()
        self.assertTrue(any('No. of backlinks' in warning for warning in batch.warnings))
        self.assertContains(self.client.get(response.url), 'Verwachte kolommen ontbreken')
        self.client.post(response.url, {'action': 'confirm', 'observed_on': '2026-10-29'})
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'complete')

    def test_partial_and_complete_column_selection(self):
        partial = detect_export(export(enriched=True))['summary']['missing_expected_columns']
        self.assertEqual(partial, ['No. of dofollow backlinks', 'No. of keywords with Top 10 position'])
        from .ahrefs import EXPECTED_COLUMNS
        headers = sorted(BASE_COLUMNS | {'No. of all inlinks', 'First found at'}) + list(EXPECTED_COLUMNS['ahrefs_site_audit_pages'])
        raw = (','.join(headers) + '\n' + ','.join('https://example.com/' if h == 'URL' else '1' for h in headers) + '\n').encode()
        self.assertEqual(detect_export(SimpleUploadedFile('full.csv', raw))['summary']['missing_expected_columns'], [])
        self.assertEqual(detect_export(export(issue=True))['summary']['missing_expected_columns'], [])
