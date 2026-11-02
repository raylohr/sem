from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from cryptography.fernet import Fernet
from accounts.tests import verified_login
from accounts.models import AuditEvent
from projects.models import Project
from .models import GSCOAuthSettings, GSCConnection
from .gsc import configured, decrypt_token, encrypt_token, oauth_flow, authorized_session

CLIENT_ID = '12345-example.apps.googleusercontent.com'
OTHER_ID = '98765-other.apps.googleusercontent.com'


@override_settings(GSC_CLIENT_ID='', GSC_CLIENT_SECRET='', INTEGRATION_TOKEN_KEY=Fernet.generate_key().decode())
class OAuthSettingsTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('settings-admin', 'settings@example.test', 'Test-secret-92!')
        verified_login(self.client, self.admin)
        self.url = reverse('ui:gsc-settings')
        self.data = {'client_id': CLIENT_ID, 'client_secret': 'private-secret', 'enabled': 'on'}

    def save_settings(self):
        self.assertEqual(self.client.post(self.url, self.data).status_code, 302)
        return GSCOAuthSettings.objects.get()

    def connection(self):
        project = Project.objects.create(name='Settings', website='https://example.test/', created_by=self.admin)
        return GSCConnection.objects.create(project=project, owner=self.admin, property_url='sc-domain:example.test',
            active=True, daily_enabled=True, encrypted_refresh_token=encrypt_token('refresh'))

    def test_admin_saves_encrypted_secret_and_audits_without_secrets(self):
        saved = self.save_settings()
        self.assertEqual(decrypt_token(saved.encrypted_client_secret), 'private-secret')
        self.assertNotIn('private-secret', saved.encrypted_client_secret)
        self.assertTrue(configured())
        self.assertEqual(saved.updated_by, self.admin)
        event = AuditEvent.objects.get(action='gsc_oauth_updated')
        self.assertNotIn('private-secret', str(event.details))
        response = self.client.get(self.url)
        self.assertContains(response, 'Opgeslagen')
        self.assertNotContains(response, 'private-secret')
        self.assertNotContains(response, saved.encrypted_client_secret)
        self.assertIn('no-store', response['Cache-Control'])

    def test_blank_secret_preserves_value_and_invalid_post_never_echoes_secret(self):
        saved = self.save_settings()
        self.client.post(self.url, {'client_id': CLIENT_ID, 'enabled': 'on'})
        saved.refresh_from_db()
        self.assertEqual(decrypt_token(saved.encrypted_client_secret), 'private-secret')
        response = self.client.post(self.url, {'client_id': 'bad', 'client_secret': 'never-display-this'})
        self.assertContains(response, 'Vul de client-ID')
        self.assertNotContains(response, 'never-display-this')
        saved.refresh_from_db()
        self.assertEqual(saved.client_id, CLIENT_ID)

    def test_client_change_requires_new_secret_and_explicit_disconnect(self):
        self.save_settings()
        connection = self.connection()
        self.client.post(self.url, {'client_id': OTHER_ID, 'client_secret': 'new-secret', 'enabled': 'on'})
        connection.refresh_from_db()
        self.assertTrue(connection.active)
        self.assertEqual(GSCOAuthSettings.objects.get().client_id, CLIENT_ID)
        self.client.post(self.url, {'client_id': OTHER_ID, 'client_secret': 'new-secret',
            'enabled': 'on', 'disconnect_existing': 'on'})
        connection.refresh_from_db()
        self.assertFalse(connection.active)
        self.assertFalse(connection.daily_enabled)
        self.assertEqual(connection.encrypted_refresh_token, '')
        self.assertEqual(GSCOAuthSettings.objects.get().client_id, OTHER_ID)

    def test_runtime_oauth_and_worker_use_stored_client(self):
        self.save_settings()
        with patch('integrations.gsc.Flow.from_client_config') as flow:
            oauth_flow()
            self.assertEqual(flow.call_args.args[0]['web']['client_id'], CLIENT_ID)
            self.assertEqual(flow.call_args.args[0]['web']['client_secret'], 'private-secret')
        connection = self.connection()
        with patch('integrations.gsc.AuthorizedSession') as session:
            authorized_session(connection)
            self.assertEqual(session.call_args.args[0].client_id, CLIENT_ID)

    @override_settings(GSC_CLIENT_ID=CLIENT_ID, GSC_CLIENT_SECRET='environment-secret')
    def test_disabled_database_config_does_not_fall_back_to_environment(self):
        self.save_settings()
        self.client.post(self.url, {'client_id': CLIENT_ID})
        self.assertFalse(configured())

    def test_non_platform_user_and_unverified_admin_cannot_configure(self):
        user = get_user_model().objects.create_user('ordinary-settings')
        self.client.force_login(user)
        self.assertEqual(self.client.post(self.url, self.data).status_code, 403)
        self.assertEqual(self.client.get(reverse('ui:gsc-setup-guide')).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url, self.data).status_code, 302)
        self.assertFalse(GSCOAuthSettings.objects.exists())

    def test_settings_post_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        verified_login(client, self.admin)
        self.assertEqual(client.post(self.url, self.data).status_code, 403)
        self.assertFalse(GSCOAuthSettings.objects.exists())

    def test_setup_guide_shows_exact_callback_and_links(self):
        response = self.client.get(reverse('ui:gsc-setup-guide'))
        self.assertContains(response, 'https://sem.lohr.nl/integrations/gsc/callback/')
        self.assertContains(response, 'Web application')
        self.assertContains(response, 'Google Cloud Console')

    def test_oauth_callback_rejects_configuration_changed_mid_flow(self):
        import time
        from .gsc import configuration_fingerprint
        self.save_settings()
        connection = self.connection()
        session = self.client.session
        session['gsc_oauth'] = {'state': 'state', 'verifier': 'verifier', 'user_id': self.admin.pk,
            'project_id': str(connection.project_id), 'property': connection.property_url,
            'started': time.time(), 'configuration': configuration_fingerprint()}
        session.save()
        self.client.post(self.url, {**self.data, 'client_secret': 'rotated-secret'})
        with patch('integrations.views.oauth_flow') as flow:
            response = self.client.get(reverse('integrations:gsc-callback'), {'state': 'state', 'code': 'code'})
        self.assertEqual(response.status_code, 400)
        flow.assert_not_called()
