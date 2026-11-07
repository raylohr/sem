import csv
import io
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from projects.models import Project, Membership
from .models import ImportBatch
from .screamingfrog import parse_export, REQUIRED


def export(address='https://example.com/' , filename='renamed.csv'):
    stream = io.StringIO(newline='')
    headers = sorted(REQUIRED | {'Extra Column'})
    writer = csv.DictWriter(stream, fieldnames=headers)
    writer.writeheader()
    writer.writerow(dict.fromkeys(headers, '') | {
        'Address': address, 'Content Type': 'text/html; charset=UTF-8',
        'Status Code': '200', 'Indexability': 'Indexable', 'Title 1': 'Example',
        'Crawl Timestamp': '2026-11-06 10:00:00', 'Extra Column': 'Retained'})
    return SimpleUploadedFile(filename, stream.getvalue().encode('utf-8-sig'))


class DetectionTests(SimpleTestCase):
    def test_detects_structure_and_preserves_full_url_and_extra_columns(self):
        result = parse_export(export('https://example.com/Path/?a=1&b=2'))
        self.assertEqual(result['summary']['type'], 'internal_all')
        self.assertEqual(result['summary']['html_count'], 1)
        self.assertEqual(result['rows'][0]['Address'], 'https://example.com/Path/?a=1&b=2')
        self.assertEqual(result['rows'][0]['Extra Column'], 'Retained')
        self.assertIsNotNone(result['measured_at'].utcoffset())

    def test_rejects_other_exports_and_invalid_urls(self):
        for file in (SimpleUploadedFile('internal_all.csv', b'Source,Destination\na,b\n'),
                     export('javascript:alert(1)')):
            with self.assertRaises(ValueError):
                parse_export(file)


class ImportFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('importer')
        self.other = get_user_model().objects.create_user('other')
        self.project = Project.objects.create(name='Example', website='https://example.com/', created_by=self.user)
        Membership.objects.create(project=self.project, user=self.user, role='editor')
        self.client.force_login(self.user)
        self.url = reverse('ui:import', args=[self.project.pk])

    def upload(self):
        return self.client.post(self.url, {'file': export(), 'created_by': self.other.pk})

    def test_preview_confirm_attribution_duplicate_and_url_list(self):
        response = self.upload()
        batch = ImportBatch.objects.get()
        self.assertEqual(batch.created_by, self.user)
        self.assertEqual(batch.project, self.project)
        self.assertEqual(batch.status, 'pending')
        self.assertEqual(batch.records.get().data['Extra Column'], 'Retained')
        preview = response.url
        self.assertContains(self.client.get(preview), 'Screaming Frog')
        self.assertEqual(self.client.get(reverse('projects:sources:records', args=[self.project.pk, batch.pk])).status_code, 409)
        self.client.post(preview, {'action': 'confirm'})
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'complete')
        self.assertContains(self.client.get(reverse('ui:batch', args=[self.project.pk, batch.pk])), 'Example')
        empty = self.client.get(reverse('ui:batch', args=[self.project.pk, batch.pk]), {'q': 'no-match'})
        self.assertContains(empty, 'Geen URLs gevonden.')
        self.upload()
        self.assertEqual(ImportBatch.objects.count(), 1)

    def test_permissions_and_preview_ownership(self):
        preview = self.upload().url
        Membership.objects.create(project=self.project, user=self.other, role='editor')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(preview).status_code, 404)
        self.assertEqual(self.client.post(preview, {'action': 'confirm'}).status_code, 404)
        membership = Membership.objects.get(project=self.project, user=self.other)
        membership.role = 'viewer'
        membership.save()
        self.assertEqual(self.client.post(self.url, {'file': export()}).status_code, 403)
        membership.delete()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_reject_all_foreign_urls_and_cancel_valid_preview(self):
        response = self.client.post(self.url, {'file': export('https://elsewhere.com/')})
        self.assertContains(response, 'Geen URLs binnen de toegestane hosts')
        self.assertFalse(ImportBatch.objects.exists())
        response = self.upload()
        self.client.post(response.url, {'action': 'cancel'})
        self.assertFalse(ImportBatch.objects.exists())

    def test_invalid_file_creates_no_batch(self):
        response = self.client.post(self.url, {'file': SimpleUploadedFile('internal_all.csv', b'not a crawl')})
        self.assertContains(response, 'Bestand niet herkend')
        self.assertFalse(ImportBatch.objects.exists())


class LocalizedExportTests(SimpleTestCase):
    def localized(self, upload):
        from .screamingfrog import DUTCH_HEADERS
        text = upload.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(text))
        reverse_headers = {v: k for k, v in DUTCH_HEADERS.items()}
        stream = io.StringIO(newline='')
        writer = csv.writer(stream, delimiter=';')
        columns = list(reversed(reader.fieldnames))
        writer.writerow([reverse_headers.get(key, key) for key in columns])
        for row in reversed(list(reader)):
            writer.writerow(['Indexeerbaar' if key == 'Indexability' else row[key] for key in columns])
        return SimpleUploadedFile('intern.csv', stream.getvalue().encode('utf-8-sig'))

    def test_language_delimiter_and_order_do_not_change_content_hash(self):
        from .screamingfrog import content_checksum
        en = parse_export(export())
        nl = parse_export(self.localized(export()))
        self.assertEqual(en['rows'], nl['rows'])
        self.assertEqual(content_checksum(en['rows']), content_checksum(nl['rows']))
        self.assertIn('Adres', nl['summary']['original_columns'])
        self.assertIn('Address', nl['summary']['columns'])
        self.assertNotEqual(en['checksum'], nl['checksum'])

    def test_changed_crawl_and_extra_data_are_distinct(self):
        from .screamingfrog import content_checksum
        rows = parse_export(export())['rows']
        changed = [dict(rows[0], **{'Crawl Timestamp': '2026-11-07T10:00:00+01:00'})]
        self.assertNotEqual(content_checksum(rows), content_checksum(changed))
        changed = [dict(rows[0], **{'Extra Column': 'Changed'})]
        self.assertNotEqual(content_checksum(rows), content_checksum(changed))
        self.assertEqual(content_checksum(rows + changed), content_checksum(changed + rows))

    def test_alias_collision_rejected(self):
        text = export().read().decode('utf-8-sig').replace('Address,', 'Adres,Address,')
        with self.assertRaises(ValueError):
            parse_export(SimpleUploadedFile('ambiguous.csv', text.encode()))

    def test_dutch_xlsx_uses_same_schema(self):
        import openpyxl
        from .file_detection import detect_export
        from .screamingfrog import content_checksum
        nl = self.localized(export()).read().decode('utf-8-sig')
        book = openpyxl.Workbook()
        for row in csv.reader(io.StringIO(nl), delimiter=';'):
            book.active.append(row)
        stream = io.BytesIO()
        book.save(stream)
        result = detect_export(SimpleUploadedFile('intern.xlsx', stream.getvalue()))
        self.assertEqual(content_checksum(result['rows']), content_checksum(parse_export(export())['rows']))
        self.assertIn('Adres', result['summary']['original_columns'])


class LocalizedImportFlowTests(ImportFlowTests):
    def test_localized_duplicate_reuses_existing_batch(self):
        self.upload()
        batch = ImportBatch.objects.get()
        preview = reverse('ui:import-preview', args=[self.project.pk, batch.pk])
        self.client.post(preview, {'action': 'confirm'})
        response = self.client.post(self.url, {'file': LocalizedExportTests().localized(export())})
        self.assertRedirects(response, reverse('ui:batch', args=[self.project.pk, batch.pk]))
        self.assertEqual(ImportBatch.objects.count(), 1)

    def test_legacy_import_without_content_hash_is_recognized(self):
        self.upload()
        batch = ImportBatch.objects.get()
        batch.request_metadata.pop('content_checksum')
        batch.save(update_fields=['request_metadata'])
        response = self.client.post(self.url, {'file': LocalizedExportTests().localized(export())})
        self.assertEqual(response.url, reverse('ui:import-preview', args=[self.project.pk, batch.pk]))
        self.assertEqual(ImportBatch.objects.count(), 1)


class TimestampFingerprintTests(SimpleTestCase):
    def test_equivalent_instants_match_without_changing_stored_timestamp(self):
        from .screamingfrog import content_checksum
        rows = parse_export(export())['rows']
        original = rows[0]['Crawl Timestamp']
        utc_rows = [dict(rows[0], **{'Crawl Timestamp': '2026-11-06T09:00:00Z'})]
        self.assertEqual(content_checksum(rows), content_checksum(utc_rows))
        self.assertEqual(rows[0]['Crawl Timestamp'], original)
