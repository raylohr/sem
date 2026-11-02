import json
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from oauthlib.oauth2 import InvalidClientError, InvalidGrantError
from .tests import IntegrationTests, CONFIG
from .gsc import request_json, ConnectorError, SCOPE


class GoogleAPIErrorTests(SimpleTestCase):
    def fetch_error(self, reason):
        session = MagicMock()
        response = session.request.return_value.__enter__.return_value
        response.status_code = 403
        response.iter_content.return_value = [json.dumps({'error': {'details': [
            {'reason': reason, 'message': 'private-secret'}]}}).encode()]
        with self.assertRaises(ConnectorError) as caught:
            request_json(session, 'GET', 'https://www.googleapis.com/webmasters/v3/sites/')
        self.assertNotIn('private-secret', str(caught.exception))
        return str(caught.exception)

    def test_disabled_api_has_specific_actionable_error(self):
        self.assertIn('Activeer de API', self.fetch_error('SERVICE_DISABLED'))

    def test_missing_scope_has_specific_actionable_error(self):
        self.assertIn('leestoegang ontbreekt', self.fetch_error('ACCESS_TOKEN_SCOPE_INSUFFICIENT'))

    def test_unknown_reason_never_exposed(self):
        self.assertNotIn('untrusted-reason', self.fetch_error('untrusted-reason'))


@override_settings(**CONFIG)
class OAuthErrorTests(TestCase):
    setUp = IntegrationTests.setUp
    pending_oauth = IntegrationTests.pending_oauth

    def test_invalid_client_and_expired_grant_errors_do_not_expose_payload(self):
        for exception, expected in [(InvalidClientError(description='private-secret'), 'Google weigert de OAuth-client'),
                (InvalidGrantError(description='private-code'), 'verlopen of al gebruikt')]:
            self.pending_oauth()
            with patch('integrations.views.oauth_flow') as flow:
                flow.return_value.fetch_token.side_effect = exception
                response = self.client.get(reverse('integrations:gsc-callback'), {'state': 'expected', 'code': 'private-code'})
            self.assertEqual(response.status_code, 400)
            self.assertContains(response, expected, status_code=400)
            self.assertNotContains(response, 'private-secret', status_code=400)
            self.assertNotContains(response, 'private-code', status_code=400)
            self.assertContains(response, 'Terug naar Koppelingen', status_code=400)
            self.assertEqual(response['Referrer-Policy'], 'no-referrer')

    def test_no_properties_explains_account_access(self):
        self.pending_oauth()
        with patch('integrations.views.oauth_flow') as flow, patch('integrations.views.AuthorizedSession'), patch('integrations.views.list_properties', return_value=[]):
            flow.return_value.credentials.refresh_token = 'private-refresh'
            flow.return_value.credentials.granted_scopes = [SCOPE]
            response = self.client.get(reverse('integrations:gsc-callback'), {'state': 'expected', 'code': 'private-code'})
        self.assertContains(response, 'geen toegankelijke Search Console-properties', status_code=400)
        self.assertNotContains(response, 'private-refresh', status_code=400)
