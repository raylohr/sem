from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test.runner import DiscoverRunner


class IsolatedDatabaseTestRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        database = settings.DATABASES['default']
        expected = {'ENGINE':'django.db.backends.postgresql', 'HOST':'database',
                    'NAME':'sem_checks', 'USER':'sem_checks'}
        if settings.SETTINGS_MODULE != 'config.test_settings' or any(
                database.get(key) != value for key, value in expected.items()) or database.get('OPTIONS'):
            raise ImproperlyConfigured('Run automated tests with ops/test.sh; production databases are forbidden.')
        return super().setup_databases(**kwargs)
