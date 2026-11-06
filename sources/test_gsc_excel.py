import io
from datetime import datetime
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from openpyxl import Workbook
from projects.models import Project, Membership
from .file_detection import detect_export
from .models import ImportBatch
from .url_inventory import project_inventory


def workbook(*, english=False, reason='Niet gevonden (404)', urls=None, stamp=None, overview=False):
    book = Workbook()
    chart = book.active
    chart.title = 'Chart' if english else 'Diagram'
    chart.append(['Date' if english else 'Datum', 'Affected pages' if english else "Betreffende pagina's"])
    chart.append(['2026-10-12', 240])
    if not overview:
        table = book.create_sheet('Table' if english else 'Tabel')
        table.append(['URL', 'Last crawled' if english else 'Laatst gecrawld'])
        for url in urls or ['https://example.com/missing', 'https://outside.test/missing']:
            table.append([url, stamp or datetime(2026, 10, 13)])
    meta = book.create_sheet('Metadata')
    meta.append(['Property', 'Value' if english else 'Waarde'])
    meta.append(['Sitemap', 'All known pages'])
    if reason:
        meta.append(['Issue' if english else 'Probleem', reason])
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


class GSCExcelDetectionTests(SimpleTestCase):
    def test_dutch_xlsx_detects_reason_from_data_and_preserves_raw_columns(self):
        parsed = detect_export(SimpleUploadedFile('renamed.xlsx', workbook()))
        self.assertEqual(parsed['source'], 'gsc')
        self.assertEqual(parsed['summary']['report_status'], 'not_indexed')
        self.assertEqual(parsed['summary']['indexing_reason'], 'Niet gevonden (404)')
        self.assertEqual(parsed['summary']['reported_total'], 240)
        self.assertEqual(parsed['summary']['report_date'], '2026-10-12')
        row = parsed['rows'][0]
        self.assertEqual(row['Laatst gecrawld'], '2026-10-13T00:00:00')
        self.assertEqual(row['Last crawled'], '2026-10-13')
        self.assertEqual(row['gsc_indexing_reason'], 'Niet gevonden (404)')

    def test_english_xlsx_and_legacy_xls(self):
        parsed = detect_export(SimpleUploadedFile('unknown.xlsx', workbook(english=True, reason='Page with redirect')))
        self.assertEqual(parsed['summary']['indexing_reason'], 'Page with redirect')
        self.assertEqual(parsed['summary']['report_status'], 'not_indexed')
        raw = (Path(__file__).parent / 'fixtures' / 'gsc_coverage_test.xls').read_bytes()
        parsed = detect_export(SimpleUploadedFile('unknown.xls', raw))
        self.assertEqual(parsed['summary']['file_format'], 'xls')
        self.assertEqual(parsed['rows'][0]['Last crawled'], '2026-10-13')
        self.assertEqual(parsed['summary']['indexing_reason'], 'Niet gevonden (404)')

    def test_missing_crawl_date_is_not_an_observation_in_1970(self):
        raw = workbook(reason='Gevonden - momenteel niet geïndexeerd', stamp=datetime(1970,1,1))
        parsed = detect_export(SimpleUploadedFile('unknown.xlsx', raw))
        self.assertEqual(parsed['summary']['unknown_crawl_dates'], 2)
        self.assertEqual(parsed['rows'][0]['Last crawled'], '')
        self.assertEqual(parsed['rows'][0]['Laatst gecrawld'], '1970-01-01T00:00:00')

    def test_overview_is_recognized_but_cannot_be_a_url_import(self):
        with self.assertRaisesMessage(ValueError, 'zonder URL-lijst'):
            detect_export(SimpleUploadedFile('coverage.xlsx', workbook(overview=True)))


class GSCExcelFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('gsc-excel-user')
        self.project = Project.objects.create(name='GSC Excel', website='https://example.com', domain='example.com', created_by=self.user)
        Membership.objects.create(project=self.project, user=self.user, role='admin')
        self.client.force_login(self.user)
        self.url = reverse('ui:import', args=[self.project.pk])

    def test_preview_confirmation_stores_metadata_reason_and_shows_it_in_details(self):
        response = self.client.post(self.url, {'file': SimpleUploadedFile('renamed.xlsx', workbook())})
        batch = ImportBatch.objects.get()
        self.assertEqual(batch.created_by, self.user)
        self.assertEqual(batch.record_count, 1)
        self.assertEqual(batch.request_metadata['excluded_rows'], 1)
        self.assertContains(self.client.get(response.url), 'Indexeringsreden uit GSC Metadata:')
        self.client.post(response.url, {'action':'confirm', 'indexing_reason':'Wrong', 'report_status':'indexed'})
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'complete')
        self.assertEqual(batch.request_metadata['indexing_reason'], 'Niet gevonden (404)')
        self.assertEqual(batch.request_metadata['report_status'], 'not_indexed')
        self.assertEqual(batch.records.get().data['gsc_indexing_reason'], 'Niet gevonden (404)')
        detail = self.client.get(reverse('ui:batch', args=[self.project.pk, batch.pk]))
        self.assertContains(detail, 'Niet gevonden (404)')
        _, rows = project_inventory(self.project)
        self.assertEqual(rows[0]['observations']['gsc']['date'].isoformat(), '2026-10-13')

    def test_invalid_url_is_filtered_without_losing_valid_rows(self):
        raw = workbook(urls=['https://example.com/a', 'https://example.com/bad\\path'])
        response = self.client.post(self.url, {'file': SimpleUploadedFile('export.xlsx', raw)})
        batch = ImportBatch.objects.get()
        self.assertEqual(batch.record_count, 1)
        self.assertEqual(batch.request_metadata['invalid_url_rows'], 1)
        self.assertEqual(batch.request_metadata['excluded_rows'], 1)
        self.assertContains(self.client.get(response.url), 'ongeldige URL-rijen')

    def test_unknown_date_uses_import_fallback_and_source_remains_gsc(self):
        raw = workbook(reason='Gevonden - momenteel niet geïndexeerd', stamp=datetime(1970,1,1))
        response = self.client.post(self.url, {'file': SimpleUploadedFile('export.xlsx', raw)})
        self.client.post(response.url, {'action':'confirm'})
        _, rows = project_inventory(self.project)
        self.assertTrue(rows[0]['observations']['gsc']['fallback'])
        self.assertGreater(rows[0]['observations']['gsc']['date'].year, 1970)
