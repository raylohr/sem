from django.test import TestCase, override_settings
from django.urls import reverse
from .tests import IntegrationTests, CONFIG


@override_settings(**CONFIG)
class WorkInstructionAccessTests(TestCase):
    setUp = IntegrationTests.setUp

    def test_reader_can_read_project_guide_without_settings_or_secrets(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('ui:gsc-project-guide', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('ui:connections', args=[self.project.pk]))
        self.assertContains(response, reverse('ui:catalog:overview', args=[self.project.pk]))
        self.assertNotContains(response, reverse('ui:gsc-settings'))
        self.assertNotContains(response, self.connection.encrypted_refresh_token)
        self.assertIn('no-store', response['Cache-Control'])

    def test_project_guide_denies_foreign_user_and_global_guide_hides_project(self):
        self.client.force_login(self.other)
        response = self.client.get(reverse('ui:gsc-project-guide', args=[self.project.pk]))
        self.assertEqual(response.status_code, 404)
        response = self.client.get(reverse('ui:gsc-guide'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, str(self.project.pk))
        self.assertNotContains(response, self.project.website)

    def test_anonymous_guide_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('ui:gsc-guide')).status_code, 302)
