from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from django.urls import reverse


@override_settings(ALLOWED_HOSTS=['testserver'])
class HealthTests(SimpleTestCase):
    def test_health_does_not_require_database(self):
        response = self.client.get(reverse('health'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['service'], 'sem')

    def test_readiness_failure_does_not_expose_connection_details(self):
        with patch('core.views.connection') as database:
            database.cursor.side_effect = RuntimeError('private detail')
            response = self.client.get(reverse('ready'))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b'private detail', response.content)
