import json
from django.core.exceptions import PermissionDenied
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from projects.models import Project, Membership
from .models import ImportBatch, SourceRecord, BatchDeletionEvent
from .services import publish_records


class BatchTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.admin = U.objects.create_user('admin')
        self.editor = U.objects.create_user('editor')
        self.viewer = U.objects.create_user('viewer')
        self.other = U.objects.create_user('other')
        self.project = Project.objects.create(name='A', website='https://a.example', created_by=self.admin)
        self.hidden = Project.objects.create(name='B', website='https://b.example', created_by=self.other)
        for user, role in [(self.admin, 'admin'), (self.editor, 'editor'), (self.viewer, 'viewer')]:
            Membership.objects.create(project=self.project, user=user, role=role)
        self.batch = self.make_batch('First')
        self.second = self.make_batch('Second')
        self.client.force_login(self.admin)

    def make_batch(self, label):
        return ImportBatch.objects.create(project=self.project, source='gsc', method='upload', label=label, created_by=self.editor)

    def remove_url(self, project=None):
        return reverse('projects:sources:remove', args=[(project or self.project).pk, self.batch.pk])

    def test_provenance_cannot_be_spoofed(self):
        self.client.force_login(self.editor)
        body = {'source': 'gsc', 'method': 'api', 'label': 'GSC', 'created_by': self.other.pk, 'project': str(self.hidden.pk), 'status': 'complete'}
        response = self.client.post(reverse('projects:sources:create', args=[self.project.pk]), json.dumps(body), content_type='application/json')
        self.assertEqual(response.status_code, 201)
        batch = ImportBatch.objects.get(pk=response.json()['id'])
        self.assertEqual(batch.created_by, self.editor)
        self.assertEqual(batch.project, self.project)
        self.assertIsNotNone(batch.imported_at)
        self.assertIsNone(batch.measured_at)
        self.assertEqual(batch.status, 'pending')

    def test_viewer_cannot_create_or_delete(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(reverse('projects:sources:create', args=[self.project.pk])).status_code, 403)
        self.assertEqual(self.client.post(self.remove_url()).status_code, 403)

    def test_editor_cannot_delete(self):
        self.client.force_login(self.editor)
        self.assertEqual(self.client.post(self.remove_url()).status_code, 403)

    def test_foreign_project_cannot_be_used_to_delete(self):
        self.assertEqual(self.client.post(self.remove_url(self.hidden)).status_code, 404)
        self.assertTrue(ImportBatch.objects.filter(pk=self.batch.pk).exists())

    def test_delete_only_own_batch_records_and_log_minimal_event(self):
        for batch in (self.batch, self.second):
            publish_records(batch_id=batch.pk, records=[{'locator': 'row:2', 'data': {'url': 'https://a.example'}}])
        self.assertEqual(self.client.post(self.remove_url()).status_code, 200)
        self.assertFalse(SourceRecord.objects.filter(batch_id=self.batch.pk).exists())
        self.assertEqual(self.second.records.count(), 1)
        event = BatchDeletionEvent.objects.get(batch_id=self.batch.pk)
        self.assertEqual(event.actor, self.admin)
        self.assertEqual(event.project, self.project)
        with self.assertRaises(ImportBatch.DoesNotExist):
            publish_records(batch_id=self.batch.pk, records=[])

    def test_revoked_worker_owner_cannot_publish(self):
        Membership.objects.filter(project=self.project, user=self.editor).delete()
        with self.assertRaises(PermissionDenied):
            publish_records(batch_id=self.batch.pk, records=[])
        self.assertEqual(self.batch.records.count(), 0)

    def test_retry_does_not_duplicate_records(self):
        records = [{'locator': 'row:2', 'data': {'x': 1}}]
        publish_records(batch_id=self.batch.pk, records=records)
        publish_records(batch_id=self.batch.pk, records=records)
        self.assertEqual(self.batch.records.count(), 1)

    def test_invalid_period_rejected(self):
        response = self.client.post(reverse('projects:sources:create', args=[self.project.pk]), json.dumps({'source': 'gsc', 'method': 'upload', 'label': 'Invalid', 'period_start': '2026-11-02', 'period_end': '2026-11-01'}), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_outsider_cannot_list_sources(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('projects:sources:overview', args=[self.project.pk])).status_code, 404)

    def test_viewer_sees_origin_and_time(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('projects:sources:overview', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['can_delete'])
        self.assertEqual(response.json()['batches'][0]['user_id'], self.editor.pk)
        self.assertTrue(response.json()['batches'][0]['imported_at'])
