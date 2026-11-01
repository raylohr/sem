import datetime
import json
import time
from unittest.mock import MagicMock, patch
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from projects.models import Project, Membership
from sources.models import ImportBatch
from sources.services import delete_batch
from .models import GSCConnection, APIResponsePage
from .gsc import encrypt_token, decrypt_token, fetch_search_analytics, ConnectorError
from .tasks import process_gsc

TEST_KEY = Fernet.generate_key().decode()
CONFIG = {'GSC_CLIENT_ID': 'client-test', 'GSC_CLIENT_SECRET': 'secret-test', 'INTEGRATION_TOKEN_KEY': TEST_KEY}
ROW = {'keys': ['2026-10-01', 'https://example.test/'], 'clicks': 12, 'impressions': 100, 'ctr': 0.12, 'position': 3.5}


@override_settings(**CONFIG)
class ConnectorTests(SimpleTestCase):
    def test_tokens_encrypted_and_roundtrip(self):
        encrypted = encrypt_token('private-refresh')
        self.assertNotIn('private-refresh', encrypted)
        self.assertEqual(decrypt_token(encrypted), 'private-refresh')
        with self.assertRaises(ConnectorError):
            decrypt_token('broken')

    @patch('integrations.gsc.request_json')
    def test_pagination_preserves_raw_pages_and_date_provenance(self, fetch):
        fetch.side_effect = [{'rows': [ROW, ROW]}, {'rows': [ROW]}, {'rows': []}]
        guard = MagicMock()
        with patch('integrations.gsc.PAGE_SIZE', 2):
            records, pages, warnings = fetch_search_analytics(MagicMock(), property_url='sc-domain:example.test',
                start=datetime.date(2026, 10, 1), end=datetime.date(2026, 10, 2), check_access=guard)
        self.assertEqual(len(records), 3)
        self.assertEqual(len(pages), 3)
        self.assertEqual(guard.call_count, 3)
        self.assertEqual(pages[1]['request_body']['startRow'], 2)
        self.assertEqual(pages[2]['request_body']['startRow'], 0)
        self.assertEqual(records[0]['data']['raw'], ROW)
        self.assertIn('sc-domain%3Aexample.test', fetch.call_args.args[2])
        self.assertTrue(any('geen volledige' in w for w in warnings))

    @patch('integrations.gsc.request_json')
    def test_total_cap_is_explicit(self, fetch):
        fetch.side_effect = [{'rows': [ROW, ROW]}, {'rows': [ROW]}]
        with patch('integrations.gsc.PAGE_SIZE', 2), patch('integrations.gsc.MAX_TOTAL_ROWS', 3):
            records, pages, warnings = fetch_search_analytics(MagicMock(), property_url='https://example.test/',
                start=datetime.date(2026, 10, 1), end=datetime.date(2026, 10, 1), check_access=lambda: None)
        self.assertEqual(len(records), 3)
        self.assertEqual(pages[1]['request_body']['rowLimit'], 1)
        self.assertTrue(any('Importgrens' in w for w in warnings))

    def test_access_guard_runs_before_network(self):
        with patch('integrations.gsc.request_json') as fetch:
            with self.assertRaises(PermissionDenied):
                fetch_search_analytics(MagicMock(), property_url='sc-domain:example.test',
                    start=datetime.date(2026, 10, 1), end=datetime.date(2026, 10, 1),
                    check_access=MagicMock(side_effect=PermissionDenied))
            fetch.assert_not_called()


@override_settings(**CONFIG)
class IntegrationTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.owner = U.objects.create_user('gsc-owner')
        self.editor = U.objects.create_user('gsc-editor')
        self.viewer = U.objects.create_user('gsc-viewer')
        self.other = U.objects.create_user('gsc-other')
        self.project = Project.objects.create(name='GSC', website='https://example.test/', created_by=self.owner)
        for user, role in [(self.owner, 'admin'), (self.editor, 'editor'), (self.viewer, 'viewer')]:
            Membership.objects.create(project=self.project, user=user, role=role)
        self.connection = GSCConnection.objects.create(project=self.project, owner=self.owner,
            property_url='sc-domain:example.test', encrypted_refresh_token=encrypt_token('private-refresh'))
        self.client.force_login(self.owner)

    def url(self, name, with_connection=False):
        args = [self.project.pk]
        if with_connection:
            args.append(self.connection.pk)
        return reverse('projects:connections:'+name, args=args)

    def make_batch(self):
        return ImportBatch.objects.create(project=self.project, source='gsc', method='api', created_by=self.editor,
            label='GSC', gsc_connection=self.connection, period_start='2026-10-01', period_end='2026-10-01',
            request_metadata={'property': self.connection.property_url})

    def pending_oauth(self, **extra):
        session = self.client.session
        session['gsc_oauth'] = {'state': 'expected', 'verifier': 'verifier', 'user_id': self.owner.pk,
            'project_id': str(self.project.pk), 'property': self.connection.property_url, 'started': time.time(), **extra}
        session.save()

    def test_connection_listing_never_contains_token(self):
        response = self.client.get(self.url('gsc-list'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'private-refresh', response.content)
        self.assertNotIn(b'encrypted_refresh_token', response.content)

    def test_only_admin_can_start_oauth(self):
        self.client.force_login(self.editor)
        self.assertEqual(self.client.post(self.url('gsc-connect'), '{}', content_type='application/json').status_code, 403)

    @override_settings(GSC_CLIENT_ID='')
    def test_missing_config_is_cleanly_disabled(self):
        with patch('integrations.views.oauth_flow') as flow:
            self.assertEqual(self.client.post(self.url('gsc-connect'), '{}', content_type='application/json').status_code, 503)
            flow.assert_not_called()

    def test_wrong_oauth_state_cannot_exchange_code(self):
        self.pending_oauth()
        with patch('integrations.views.oauth_flow') as flow:
            response = self.client.get(reverse('integrations:gsc-callback'), {'state': 'wrong', 'code': 'private-code'})
            self.assertEqual(response.status_code, 400)
            flow.assert_not_called()
            self.assertNotIn(b'private-code', response.content)

    def test_expired_oauth_cannot_exchange_code(self):
        self.pending_oauth(started=time.time()-601)
        with patch('integrations.views.oauth_flow') as flow:
            self.assertEqual(self.client.get(reverse('integrations:gsc-callback'), {'state': 'expected', 'code': 'private-code'}).status_code, 400)
            flow.assert_not_called()

    def test_oauth_success_encrypts_refresh_and_consumes_state(self):
        self.pending_oauth()
        with patch('integrations.views.oauth_flow') as flow, patch('integrations.views.AuthorizedSession'), patch('integrations.views.list_properties', return_value=[self.connection.property_url]):
            credentials = flow.return_value.credentials
            credentials.refresh_token = 'new-private-refresh'
            from .gsc import SCOPE
            credentials.granted_scopes = [SCOPE]
            response = self.client.get(reverse('integrations:gsc-callback'), {'state': 'expected', 'code': 'private-code'})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        saved = GSCConnection.objects.exclude(pk=self.connection.pk).get()
        self.assertEqual(decrypt_token(saved.encrypted_refresh_token), 'new-private-refresh')
        self.assertNotIn('gsc_oauth', self.client.session)

    def test_callback_rechecks_admin_membership(self):
        self.pending_oauth()
        Membership.objects.filter(user=self.owner).update(role='viewer')
        with patch('integrations.views.oauth_flow') as flow:
            self.assertEqual(self.client.get(reverse('integrations:gsc-callback'), {'state': 'expected', 'code': 'private-code'}).status_code, 403)
            flow.assert_not_called()

    def test_editor_can_create_batch_with_original_actor_and_selection(self):
        self.client.force_login(self.editor)
        with patch('integrations.views.enqueue_gsc') as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(self.url('gsc-import', True), json.dumps({'start': '2026-10-01', 'end': '2026-10-03'}), content_type='application/json')
            self.assertEqual(response.status_code, 202)
            enqueue.assert_called_once()
        batch = ImportBatch.objects.get()
        self.assertEqual(batch.created_by, self.editor)
        self.assertEqual(batch.request_metadata['timezone'], 'America/Los_Angeles')
        self.assertEqual(batch.request_metadata['property'], self.connection.property_url)

    def test_viewer_cannot_import_or_disconnect(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(self.url('gsc-import', True)).status_code, 403)
        self.assertEqual(self.client.post(self.url('gsc-disconnect', True)).status_code, 403)

    def test_foreign_project_cannot_list_connections(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url('gsc-list')).status_code, 404)

    def test_disconnection_erases_token_and_blocks_worker(self):
        batch = self.make_batch()
        self.assertEqual(self.client.post(self.url('gsc-disconnect', True)).status_code, 200)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.encrypted_refresh_token, '')
        with patch('integrations.tasks.authorized_session') as session:
            process_gsc(str(batch.pk))
            session.assert_not_called()
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'failed')

    def test_worker_publishes_raw_pages_atomically_and_only_once(self):
        from django.utils import timezone
        batch = self.make_batch()
        page = {'page_index': 0, 'requested_at': timezone.now(), 'request_body': {'startDate': '2026-10-01'}, 'payload': {'rows': [ROW]}}
        with patch('integrations.tasks.authorized_session'), patch('integrations.tasks.fetch_search_analytics', return_value=([{'locator': 'api:0:0', 'data': {'raw': ROW}}], [page], ['Limited data'])) as fetch:
            process_gsc(str(batch.pk))
            process_gsc(str(batch.pk))
            fetch.assert_called_once()
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'complete')
        self.assertEqual(batch.records.count(), 1)
        self.assertEqual(batch.api_pages.get().payload['rows'], [ROW])
        self.assertEqual(batch.warnings, ['Limited data'])
        delete_batch(batch_id=batch.pk, project=self.project, actor=self.owner)
        self.assertEqual(APIResponsePage.objects.count(), 0)
        self.assertTrue(GSCConnection.objects.filter(pk=self.connection.pk).exists())

    def test_owner_role_revocation_blocks_api_calls(self):
        batch = self.make_batch()
        Membership.objects.filter(user=self.owner).update(role='viewer')
        with patch('integrations.tasks.authorized_session') as session:
            process_gsc(str(batch.pk))
            session.assert_not_called()
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'failed')

    def test_invalid_period_rejected(self):
        response = self.client.post(self.url('gsc-import', True), json.dumps({'start': '2026-01-01', 'end': '2026-12-31'}), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_publish_failure_rolls_back_records_and_pages(self):
        from django.utils import timezone
        batch = self.make_batch()
        page = {'page_index': 0, 'requested_at': timezone.now(), 'request_body': {}, 'payload': {'rows': [ROW]}}
        with patch('integrations.tasks.authorized_session'), patch('integrations.tasks.fetch_search_analytics', return_value=([{'locator': 'api:0:0', 'data': {'raw': ROW}}], [page], [])), patch('integrations.tasks.APIResponsePage.objects.bulk_create', side_effect=RuntimeError('test failure')):
            process_gsc(str(batch.pk))
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'failed')
        self.assertEqual(batch.records.count(), 0)
        self.assertEqual(batch.api_pages.count(), 0)

    def test_access_revoked_during_fetch_prevents_publication(self):
        batch = self.make_batch()
        def fetch(*args, **kwargs):
            Membership.objects.filter(user=self.editor).delete()
            return [{'locator': 'api:0:0', 'data': {'raw': ROW}}], [], []
        with patch('integrations.tasks.authorized_session'), patch('integrations.tasks.fetch_search_analytics', side_effect=fetch):
            process_gsc(str(batch.pk))
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'failed')
        self.assertEqual(batch.records.count(), 0)

    def test_api_batch_retry_uses_api_worker(self):
        batch = self.make_batch()
        url = reverse('projects:sources:retry', args=[self.project.pk, batch.pk])
        with patch('integrations.tasks.enqueue_gsc') as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.client.post(url).status_code, 202)
            enqueue.assert_called_once_with(batch.pk)
