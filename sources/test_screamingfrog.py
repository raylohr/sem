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
