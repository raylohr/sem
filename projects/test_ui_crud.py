import tempfile
from unittest.mock import patch
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice
from accounts.models import User, AuditEvent
from sources.models import ImportBatch, SourceRecord, BatchDeletionEvent, PendingFileDeletion
from sources.services import publish_records
from sources.tasks import cleanup_files, process_upload
from integrations.models import GSCConnection, APIResponsePage
from scans.models import ScanTarget, ScanDocument
from catalog.models import ProjectURL, URLObservation
from .models import Project, Membership
from .services import create_project, update_project, delete_project

PASSWORD = 'Project-Management-Test-Password!91'


class ProjectCRUDTests(TestCase):
    def setUp(self):
        cache.clear()
        self.root = User.objects.create_superuser('project-root', 'root@example.test', PASSWORD)
        self.manager = User.objects.create_user('project-manager', password=PASSWORD)
        self.viewer = User.objects.create_user('project-reader')
        self.editor = User.objects.create_user('project-editor')
        self.outsider = User.objects.create_user('project-outsider')
        self.project = Project.objects.create(name='Voorbeeldproject', website='https://example.com', created_by=self.root)
        for user, role in [(self.manager, 'admin'), (self.viewer, 'viewer'), (self.editor, 'editor')]:
            Membership.objects.create(project=self.project, user=user, role=role)
        self.login_root()

    def login_root(self, client=None):
        client = client or self.client
        device, _ = TOTPDevice.objects.get_or_create(user=self.root, name='default')
        client.force_login(self.root)
        session = client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()

    def url(self, action, project=None):
        return reverse('ui:project_' + action, args=[(project or self.project).pk] if action != 'create' else [])

    def test_create_assigns_actor_and_admin_membership_and_logs(self):
        response = self.client.post(self.url('create'), {'name': 'Nieuw project', 'website': 'https://new.example.com'})
        project = Project.objects.get(name='Nieuw project')
        self.assertRedirects(response, reverse('ui:project', args=[project.pk]))
        self.assertEqual(project.created_by, self.root)
        self.assertTrue(Membership.objects.filter(project=project, user=self.root, role='admin').exists())
        self.assertTrue(AuditEvent.objects.filter(project=project, actor=self.root, action='project_created').exists())
        self.assertContains(self.client.get(reverse('ui:dashboard')), self.url('create'))

    def test_only_platform_admin_can_create_or_delete(self):
        self.client.force_login(self.manager)
        for action in ('create', 'delete'):
            self.assertEqual(self.client.get(self.url(action)).status_code, 403)
            self.assertEqual(self.client.post(self.url(action), {'confirmation_name': self.project.name, 'password': PASSWORD}).status_code, 403)
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_manager_can_edit_without_changing_creator_or_access(self):
        self.client.force_login(self.manager)
        response = self.client.post(self.url('edit'), {'name': 'Nieuwe naam', 'website': 'https://example.org'})
        self.assertEqual(response.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Nieuwe naam')
        self.assertEqual(self.project.created_by, self.root)
        self.assertTrue(Membership.objects.filter(project=self.project, user=self.manager, role='admin').exists())
        self.assertTrue(AuditEvent.objects.filter(project=self.project, actor=self.manager, action='project_updated').exists())
        self.assertNotContains(self.client.get(self.url('edit')), 'Project verwijderen')

    def test_viewer_editor_and_outsider_cannot_edit(self):
        for user, status in ((self.viewer,403), (self.editor,403), (self.outsider,404)):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url('edit')).status_code, status)
            self.assertEqual(self.client.post(self.url('edit'), {'name':'Changed', 'website':'https://example.org'}).status_code, status)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Voorbeeldproject')

    def test_invalid_forms_do_not_create_or_modify(self):
        count = Project.objects.count()
        for url in (self.url('create'), self.url('edit')):
            for data in ({}, {'name':'Invalid', 'website':'javascript:alert(1)'},
                         {'name':'Invalid', 'website':'https://user:secret@example.com'},
                         {'name':'Invalid', 'website':'ftp://example.com'}):
                self.assertEqual(self.client.post(url, data).status_code, 200)
        self.assertEqual(Project.objects.count(), count)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Voorbeeldproject')

    def test_delete_get_never_mutates_and_displays_counts(self):
        ImportBatch.objects.create(project=self.project, created_by=self.root, source='ahrefs', method='upload', label='Batch')
        response = self.client.get(self.url('delete'))
        self.assertContains(response, 'Project definitief verwijderen')
        self.assertContains(response, 'name="confirmation_name"')
        self.assertEqual(response.context['deletion_counts']['batches'], 1)
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_delete_rejects_wrong_name_or_password(self):
        for data in ({'confirmation_name':'Wrong', 'password':PASSWORD},
                     {'confirmation_name':self.project.name, 'password':'wrong'}, {}):
            self.assertEqual(self.client.post(self.url('delete'), data).status_code, 200)
            self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_delete_cleans_related_data_preserves_logs_and_other_project(self):
        other = Project.objects.create(name='Keep', website='https://keep.example.com', created_by=self.root)
        connection = GSCConnection.objects.create(project=self.project, owner=self.root, property_url='https://example.com', encrypted_refresh_token='not-a-real-token')
        target = ScanTarget.objects.create(project=self.project, created_by=self.root, name='Site', url='https://example.com', allowed_hosts=['example.com'])
        previous = BatchDeletionEvent.objects.create(project=self.project, batch_id='11111111-1111-1111-1111-111111111111', actor=self.root)
        event = AuditEvent.objects.create(actor=self.root, project=self.project, action='project_access_set', details={'role':'admin'})
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            batch = ImportBatch.objects.create(project=self.project, created_by=self.root, source='ahrefs', method='upload', label='Delete', gsc_connection=connection, scan_target=target)
            batch.file.save('fixture.csv', ContentFile(b'url\nhttps://example.com/\n'))
            path = batch.file.name
            publish_records(batch_id=batch.pk, records=[{'locator':'row:2','data':{'URL':'https://example.com/'}}])
            self.assertEqual(URLObservation.objects.filter(batch=batch).count(), 1)
            ScanDocument.objects.create(batch=batch,index=0,url='https://example.com',status=200,content_type='text/html',fetched_at=timezone.now(),body=b'example')
            APIResponsePage.objects.create(batch=batch,page_index=0,requested_at=timezone.now(),request_body={},payload={})
            with self.assertLogs('sources.tasks', level='ERROR'), \
                    patch('sources.tasks.cleanup_files.delay', side_effect=ConnectionError('broker unavailable')), \
                    self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(self.url('delete'), {'confirmation_name':self.project.name, 'password':PASSWORD})
            self.assertRedirects(response, reverse('ui:dashboard'))
            self.assertFalse(Project.objects.filter(pk=self.project.pk).exists())
            self.assertFalse(ImportBatch.objects.filter(pk=batch.pk).exists())
            self.assertFalse(ProjectURL.objects.filter(project_id=self.project.pk).exists())
            self.assertFalse(ScanTarget.objects.filter(pk=target.pk).exists())
            self.assertFalse(GSCConnection.objects.filter(pk=connection.pk).exists())
            self.assertTrue(PendingFileDeletion.objects.filter(path=path).exists())
            self.assertTrue(default_storage.exists(path))
            cleanup_files()
            self.assertFalse(default_storage.exists(path))
            self.assertFalse(PendingFileDeletion.objects.filter(path=path).exists())
            process_upload(str(batch.pk))  # Late queued upload must not recreate anything.
        previous.refresh_from_db();event.refresh_from_db()
        self.assertIsNone(previous.project_id)
        self.assertEqual(previous.project_identifier, self.project.pk)
        self.assertIsNone(event.project_id)
        self.assertEqual(event.details['project_id'], str(self.project.pk))
        self.assertTrue(BatchDeletionEvent.objects.filter(batch_id=batch.pk,project_identifier=self.project.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(actor=self.root,action='project_deleted',details__project_id=str(self.project.pk)).exists())
        self.assertTrue(Project.objects.filter(pk=other.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.manager.pk).exists())

    def test_services_reject_revoked_actor_and_changed_confirmation(self):
        User.objects.filter(pk=self.root.pk).update(auth_epoch=1)
        with self.assertRaises(PermissionDenied):
            create_project(actor=self.root,name='Blocked',website='https://example.com')
        self.root.refresh_from_db()
        with self.assertRaises(ValidationError):
            delete_project(actor=self.root,project_id=self.project.pk,confirmation_name='Old name')

    def test_admin_links_redirect_and_legacy_posts_cannot_bypass_services(self):
        for action, destination in [('add',self.url('create')), ('change',self.url('edit')), ('delete',self.url('delete'))]:
            url=reverse('admin:projects_project_'+action,args=[] if action=='add' else [self.project.pk])
            self.assertRedirects(self.client.get(url),destination,fetch_redirect_response=False)
            self.assertEqual(self.client.post(url,{'name':'Bypass','website':'https://bypass.example.com'}).status_code,403)

        for action in ('change', 'delete'):
            url=reverse('admin:projects_project_'+action, args=['not-a-uuid'])
            self.assertEqual(self.client.get(url).status_code,404)

    def test_mutations_require_csrf(self):
        client=Client(enforce_csrf_checks=True)
        self.login_root(client)
        for action in ('create','edit','delete'):
            self.assertEqual(client.post(self.url(action),{}).status_code,403)
