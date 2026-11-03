"""Protect strict HTML defaults while keeping unknown and audit source data reachable."""
import csv
import io
import zipfile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from projects.models import Project, Membership
from sources.models import ImportBatch
from sources.services import publish_records
from .forms import URLFilterForm


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}, 'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}})
class ContentFilterTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user('content-filter-owner')
        self.project = Project.objects.create(name='Content filters', website='https://example.com/', created_by=self.actor)
        Membership.objects.create(project=self.project, user=self.actor, role='admin')
        self.client.force_login(self.actor)
        self.api = reverse('projects:urls:list', args=[self.project.pk])
        self.html = reverse('ui:catalog:overview', args=[self.project.pk])
        self.export = reverse('ui:catalog:export', args=[self.project.pk])

    def publish(self, source, rows):
        batch = ImportBatch.objects.create(project=self.project, created_by=self.actor, source=source,
            method='upload', label=source)
        publish_records(batch_id=batch.pk, records=[{'locator': str(i), 'data': data} for i, data in enumerate(rows)])
        return batch

    def export_rows(self, **filters):
        response = self.client.get(self.export, filters)
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content))) as archive:
            rows = []
            for name in archive.namelist():
                if name.startswith('Data-') and name.endswith('.csv'):
                    rows.extend(csv.DictReader(io.StringIO(archive.read(name).decode('utf-8-sig'))))
            return rows

    def test_html_default_excludes_unknown_and_known_non_html_with_visible_explanation(self):
        self.publish('ahrefs', [{'values': {'URL': 'https://example.com/page', 'Content type': 'text/html'}},
            {'values': {'URL': 'https://example.com/image', 'Content type': 'image/png'}},
            {'values': {'URL': 'https://example.com/document', 'Content type': 'application/pdf'}}])
        self.publish('gsc', [{'url': 'https://example.com/unknown'}])
        response = self.client.get(self.api).json()
        self.assertEqual([row['url'] for row in response['urls']], ['https://example.com/page'])
        self.assertEqual(response['excluded_unknown_content_observations'], 1)
        html = self.client.get(self.html)
        self.assertContains(html, '1 bronmeting heeft een onbekend bestandstype')
        self.assertContains(html, 'content=unknown')
        self.assertEqual([row['normalized_url'] for row in self.export_rows()], ['https://example.com/page'])

    def test_explicit_all_and_unknown_keep_values_missing_empty_and_json_null_distinct(self):
        batch = self.publish('gsc', [{'url': 'https://example.com/' + path} for path in ('missing', 'empty', 'null')])
        batch.url_observations.filter(original_url__endswith='/empty').update(facts={'content_type': ''})
        batch.url_observations.filter(original_url__endswith='/null').update(facts={'content_type': None})
        self.publish('ahrefs', [{'values': {'URL': 'https://example.com/pdf', 'Content type': 'application/pdf'}}])
        unknown = self.client.get(self.api, {'content': 'unknown'}).json()
        self.assertEqual(unknown['total'], 3)
        self.assertEqual(self.client.get(self.api, {'content': ''}).json()['total'], 4)
        self.assertEqual(self.client.get(self.api, {'content': 'other'}).json()['total'], 1)
        self.assertEqual(len(self.export_rows(content='unknown')), 3)
        self.assertEqual(len(self.export_rows(content='')), 4)
        self.assertEqual(len(self.export_rows(content='other')), 1)

    def test_unknown_counter_obeys_same_measurement_source_and_host_filters(self):
        batch = self.publish('gsc', [{'url': 'https://example.com/a'}, {'url': 'https://foreign.example/b'}])
        self.publish('ahrefs', [{'values': {'URL': 'https://example.com/a', 'Content type': 'text/html'}}])
        self.assertEqual(self.client.get(self.api).json()['excluded_unknown_content_observations'], 1)
        self.assertEqual(self.client.get(self.api, {'source': 'ahrefs'}).json()['excluded_unknown_content_observations'], 0)
        self.assertEqual(self.client.get(self.api, {'host': ''}).json()['excluded_unknown_content_observations'], 2)
        self.assertEqual(self.client.get(self.api, {'batch': batch.pk}).json()['excluded_unknown_content_observations'], 1)

    def test_subtree_includes_original_ahrefs_from_audit_without_applying_page_mime_filter(self):
        self.publish('audit', [
            {'sheet': 'AHREFS site structure', 'values': {'URL': 'https://example.com/services/', 'Current organic traffic': 100, 'Concrete actie': 'Private assessment'}},
            {'sheet': 'Audit', 'values': {'URL': 'https://example.com/page', 'Concrete actie': 'Private assessment'}},
        ])
        self.assertEqual(self.client.get(self.api).json()['total'], 0)
        subtree = self.client.get(self.api, {'scope': 'subtree'}).json()
        self.assertEqual(subtree['total'], 1)
        self.assertEqual(subtree['urls'][0]['sources'], ['audit'])
        form = URLFilterForm({'scope': 'subtree', 'content': 'html'}, project=self.project)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['content'], '')
        rows = self.export_rows(scope='subtree')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['raw.values.Current organic traffic'], '100')
        self.assertFalse(any('actie' in key.lower() for key in rows[0]))
        self.assertEqual(self.export_rows(source='audit', content=''), [])

    def test_explicit_audit_batch_does_not_leak_foreign_project_data(self):
        foreign = Project.objects.create(name='Foreign', website='https://other.example/', created_by=self.actor)
        batch = ImportBatch.objects.create(project=foreign, source='audit', method='upload', label='Foreign', created_by=self.actor)
        for endpoint in (self.api, self.export):
            self.assertEqual(self.client.get(endpoint, {'batch': batch.pk, 'scope': 'subtree'}).status_code, 400)
        self.assertEqual(self.client.get(self.api, {'source': 'invalid'}).status_code, 400)
