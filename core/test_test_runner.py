from types import SimpleNamespace
from unittest.mock import patch
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from config.test_runner import IsolatedDatabaseTestRunner


class IsolatedRunnerTests(SimpleTestCase):
    def test_production_configuration_is_rejected_before_database_setup(self):
        configured = SimpleNamespace(SETTINGS_MODULE='config.settings', DATABASES={'default':{
            'ENGINE':'django.db.backends.postgresql', 'HOST':'db-pg', 'NAME':'sem_prod', 'USER':'sem_prod_app'}})
        with patch('config.test_runner.settings', configured), self.assertRaises(ImproperlyConfigured):
            IsolatedDatabaseTestRunner().setup_databases()

    def test_production_address_cannot_be_injected_into_isolated_settings(self):
        configured = SimpleNamespace(SETTINGS_MODULE='config.test_settings', DATABASES={'default':{
            'ENGINE':'django.db.backends.postgresql', 'HOST':'database', 'NAME':'sem_checks', 'USER':'sem_checks',
            'OPTIONS':{'hostaddr':'192.168.178.152'}}})
        with patch('config.test_runner.settings', configured), self.assertRaises(ImproperlyConfigured):
            IsolatedDatabaseTestRunner().setup_databases()

    def test_expected_isolated_configuration_is_allowed(self):
        configured = SimpleNamespace(SETTINGS_MODULE='config.test_settings', DATABASES={'default':{
            'ENGINE':'django.db.backends.postgresql', 'HOST':'database', 'NAME':'sem_checks', 'USER':'sem_checks'}})
        with patch('config.test_runner.settings', configured), patch(
                'django.test.runner.DiscoverRunner.setup_databases', return_value='isolated'):
            self.assertEqual(IsolatedDatabaseTestRunner().setup_databases(), 'isolated')
