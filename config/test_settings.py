"""Disposable database on an internal Docker network, without production credentials."""
from .settings import *

DATABASES = {'default': {
    'ENGINE':'django.db.backends.postgresql', 'HOST':'database', 'PORT':'5432',
    'NAME':'sem_checks', 'USER':'sem_checks',
    'PASSWORD':os.environ['SEM_CHECK_DB_PASSWORD'],
    'TEST':{'NAME':'test_sem_checks'},
}}
CACHES = {'default': {'BACKEND':'django.core.cache.backends.locmem.LocMemCache'}}
STORAGES['staticfiles'] = {'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
CELERY_BROKER_URL = 'memory://'
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1', 'sem.lohr.nl']

# Only the disposable test database builds synchronously.
INVENTORY_BUILD_INLINE = True
