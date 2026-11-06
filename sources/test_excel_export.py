import hashlib
import io
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from openpyxl import Workbook

from projects.models import Project, Membership
from .file_detection import detect_export
from .models import ImportBatch
from .screamingfrog import REQUIRED


def xlsx_export(address='https://example.com/test/', multiple=False):
    book = Workbook()
    sheet = book.active
    sheet.title = 'Internal All'
    headers = sorted(REQUIRED)
    data = dict.fromkeys(headers, '') | {
        'Address': address, 'Content Type': 'text/html', 'Status Code': 200,
        'Indexability': 'Indexable', 'Title 1': 'Voorbeeld',
        'Crawl Timestamp': datetime(2026, 11, 6, 10, 0)}
    sheet.append(headers)
    sheet.append([data[key] for key in headers])
    if multiple:
        second = book.create_sheet('Another')
        second.append(headers)
        second.append([data[key] for key in headers])
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


class ExcelDetectionTests(SimpleTestCase):
    def test_real_xls_format_fixture_and_original_file_checksum(self):
        raw = (Path(__file__).parent / 'fixtures' / 'sf_internal_test.xls').read_bytes()
        result = detect_export(SimpleUploadedFile('renamed.bin', raw))
        self.assertEqual(result['source'], 'sf')
        self.assertEqual(result['summary']['file_format'], 'xls')
        self.assertEqual(result['summary']['worksheet'], '1 - All')
        self.assertEqual(result['rows'][0]['Address'], 'https://example.com/test/')
        self.assertEqual(result['rows'][0]['Status Code'], '200')
        self.assertEqual(result['checksum'], hashlib.sha256(raw).hexdigest())

    def test_xlsx_detected_by_contents_with_native_excel_date(self):
        raw = xlsx_export()
        result = detect_export(SimpleUploadedFile('renamed.zip', raw))
        self.assertEqual(result['summary']['file_format'], 'xlsx')
        self.assertEqual(result['summary']['html_count'], 1)
        self.assertEqual(result['rows'][0]['Status Code'], '200')
        self.assertEqual(result['rows'][0]['Crawl Timestamp'], '2026-11-06T10:00:00')
        self.assertEqual(result['checksum'], hashlib.sha256(raw).hexdigest())

    def test_rejects_multiple_matching_sheets_and_inflated_workbook(self):
        with self.assertRaisesMessage(ValueError, 'Meerdere Screaming Frog'):
            detect_export(SimpleUploadedFile('internal.xlsx', xlsx_export(multiple=True)))
        with patch('sources.excel_export.MAX_BYTES', 20), self.assertRaises(ValueError):
            detect_export(SimpleUploadedFile('internal.xlsx', xlsx_export()))

    def test_rejects_corrupt_excel(self):
        for raw in (b'PK not a zip', b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 invalid excel'):
            with self.assertRaises(ValueError):
                detect_export(SimpleUploadedFile('internal.xls', raw))


class ExcelImportFlowTests(TestCase):
    def test_excel_uses_preview_host_rules_user_and_deduplication(self):
        user = get_user_model().objects.create_user('excel-user')
        project = Project.objects.create(name='Excel', website='https://example.com', domain='example.com', created_by=user)
        Membership.objects.create(project=project, user=user, role='admin')
        self.client.force_login(user)
        url = reverse('ui:import', args=[project.pk])
        raw = xlsx_export()
        response = self.client.post(url, {'file': SimpleUploadedFile('internal.xlsx', raw)})
        batch = ImportBatch.objects.get()
        self.assertEqual(batch.created_by, user)
        self.assertEqual(batch.status, 'pending')
        self.assertEqual(batch.record_count, 1)
        self.assertContains(self.client.get(response.url), 'Screaming Frog')
        self.client.post(response.url, {'action': 'confirm'})
        self.client.post(url, {'file': SimpleUploadedFile('renamed.xlsx', raw)})
        self.assertEqual(ImportBatch.objects.count(), 1)
        rejected = self.client.post(url, {'file': SimpleUploadedFile('other.xlsx', xlsx_export('https://other.test/'))})
        self.assertContains(rejected, 'Geen URLs binnen de toegestane hosts')
        self.assertEqual(ImportBatch.objects.count(), 1)
