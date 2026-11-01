import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from projects.models import Project, Membership
from .models import ImportBatch, PendingFileDeletion
from .parsers import parse_file, ImportFormatError
from .tasks import process_upload, cleanup_files

CSV = b'PR,URL,Title,HTTP status code\n10,https://example.test/,Home,200\n'


class ParserTests(SimpleTestCase):
    def test_utf16_tab_export(self):
        result = parse_file(('Page URL\tLinks to target\nhttps://example.test/\t12\n').encode('utf-16'), 'links.csv')
        self.assertEqual(result['source'], 'ahrefs')
        self.assertEqual(result['records'][0]['data']['values']['Links to target'], '12')

    def test_empty_screaming_frog_report_is_valid(self):
        result = parse_file(b'Address,Status Code\n', 'empty.csv')
        self.assertEqual(result['records'], [])
        self.assertTrue(any('Lege export' in w for w in result['warnings']))

    def test_unsupported_content_rejected(self):
        with self.assertRaises(ImportFormatError):
            parse_file(b'not an excel file', 'fake.xlsx')
        with self.assertRaises(ImportFormatError):
            parse_file(b'a,b\n1,2\n', 'unknown.csv')

    def test_column_overflow_rejected(self):
        with self.assertRaises(ImportFormatError):
            parse_file(b'Address,Status Code\na,200,extra\n', 'bad.csv')

    def test_gsc_metadata_and_export_limit(self):
        import openpyxl
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'Diagram'
        sheet.append(['Datum', "Betreffende pagina's"])
        sheet.append(['2026-10-12', 2207])
        sheet = workbook.create_sheet('Tabel')
        sheet.append(['URL', 'Laatst gecrawld'])
        sheet.append(['https://example.test/', '1970-01-01 00:00:00'])
        sheet = workbook.create_sheet('Metadata')
        sheet.append(['Property', 'Waarde'])
        sheet.append(['Probleem', 'Pagina met omleiding'])
        output = io.BytesIO()
        workbook.save(output)
        result = parse_file(output.getvalue(), 'report.xlsx')
        self.assertEqual(result['source'], 'gsc')
        self.assertEqual(result['problem'], 'Pagina met omleiding')
        self.assertTrue(any('Onvolledige' in w for w in result['warnings']))
        row = next(r for r in result['records'] if r['data']['sheet'] == 'Tabel')
        self.assertEqual(row['data']['values']['Laatst gecrawld'], '1970-01-01 00:00:00')
        self.assertEqual(row['data']['unknown_date_columns'], ['Laatst gecrawld'])


class UploadTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.directory.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        U = get_user_model()
        self.admin = U.objects.create_user('upload-admin')
        self.editor = U.objects.create_user('upload-editor')
        self.outsider = U.objects.create_user('upload-outsider')
        self.project = Project.objects.create(name='Uploads', website='https://example.test', created_by=self.admin)
        Membership.objects.create(project=self.project, user=self.admin, role='admin')
        Membership.objects.create(project=self.project, user=self.editor, role='editor')
        self.client.force_login(self.editor)

    def upload(self, **extra):
        return self.client.post(reverse('projects:sources:upload', args=[self.project.pk]),
            {'file': SimpleUploadedFile('sample.csv', CSV), **extra})

    def batch_url(self, name, batch):
        return reverse(f'projects:sources:{name}', args=[self.project.pk, batch.pk])

    def test_preview_does_not_store_file_or_batch(self):
        response = self.upload(preview='1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['record_count'], 1)
        self.assertEqual(ImportBatch.objects.count(), 0)
        self.assertEqual(list(Path(self.directory.name).rglob('*.csv')), [])

    def test_upload_process_download_and_records(self):
        with patch('sources.tasks.enqueue_upload') as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.upload()
            self.assertEqual(response.status_code, 202)
            enqueue.assert_called_once()
        batch = ImportBatch.objects.get()
        self.assertEqual(batch.created_by, self.editor)
        self.assertTrue(batch.file.storage.exists(batch.file.name))
        process_upload(str(batch.pk))
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'complete')
        self.assertEqual(batch.record_count, 1)
        self.assertEqual(batch.records.get().data['row'], 2)
        self.assertEqual(self.client.get(self.batch_url('records', batch)).json()['total'], 1)
        download = self.client.get(self.batch_url('download', batch))
        self.assertEqual(b''.join(download.streaming_content), CSV)

    def test_duplicate_blocks_second_batch_but_can_be_explicit(self):
        self.assertEqual(self.upload().status_code, 202)
        self.assertEqual(self.upload().status_code, 409)
        self.assertEqual(self.upload(allow_duplicate='1').status_code, 202)
        self.assertEqual(ImportBatch.objects.count(), 2)

    def test_unauthorized_download_and_upload(self):
        self.upload()
        batch = ImportBatch.objects.get()
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.batch_url('download', batch)).status_code, 404)
        self.assertEqual(self.upload().status_code, 404)

    def test_invalid_source_and_content_leave_no_batch(self):
        self.assertEqual(self.upload(source='gsc').status_code, 400)
        response = self.client.post(reverse('projects:sources:upload', args=[self.project.pk]),
            {'file': SimpleUploadedFile('fake.xlsx', b'fake')})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ImportBatch.objects.count(), 0)

    def test_deletion_queues_durable_file_cleanup(self):
        self.upload()
        batch = ImportBatch.objects.get()
        path = batch.file.path
        self.client.force_login(self.admin)
        with patch('sources.tasks.enqueue_cleanup'):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.client.post(self.batch_url('remove', batch)).status_code, 200)
        self.assertTrue(PendingFileDeletion.objects.filter(path=batch.file.name).exists())
        cleanup_files()
        self.assertFalse(Path(path).exists())
        self.assertEqual(PendingFileDeletion.objects.count(), 0)
        process_upload(str(batch.pk))
        self.assertFalse(ImportBatch.objects.exists())

    def test_worker_does_not_read_file_after_access_revocation(self):
        self.upload()
        batch = ImportBatch.objects.get()
        Membership.objects.filter(user=self.editor).delete()
        process_upload(str(batch.pk))
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'failed')
        self.assertEqual(batch.records.count(), 0)

    def test_retry_retains_original_importer(self):
        self.upload()
        batch = ImportBatch.objects.get()
        ImportBatch.objects.filter(pk=batch.pk).update(status='failed')
        self.client.force_login(self.admin)
        with patch('sources.tasks.enqueue_upload'):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.client.post(self.batch_url('retry', batch)).status_code, 202)
        batch.refresh_from_db()
        self.assertEqual(batch.created_by, self.editor)
        self.assertEqual(batch.status, 'pending')

    def test_upload_requires_csrf_token(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.editor)
        response = client.post(reverse('projects:sources:upload', args=[self.project.pk]),
            {'file': SimpleUploadedFile('sample.csv', CSV)})
        self.assertEqual(response.status_code, 403)

    def test_duplicate_detection_is_project_scoped(self):
        self.upload()
        second_project = Project.objects.create(name='Other', website='https://other.example', created_by=self.editor)
        Membership.objects.create(project=second_project, user=self.editor, role='editor')
        response = self.client.post(reverse('projects:sources:upload', args=[second_project.pk]),
            {'file': SimpleUploadedFile('same.csv', CSV)})
        self.assertEqual(response.status_code, 202)
