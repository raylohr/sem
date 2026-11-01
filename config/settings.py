import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def secret(name):
    filename = os.environ.get(f"{name}_FILE")
    value = Path(filename).read_text().strip() if filename else os.environ.get(name, "")
    if not value:
        raise ImproperlyConfigured(f"Configure {name}_FILE or {name}")
    return value


SECRET_KEY = secret("DJANGO_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "django.contrib.gis", "accounts", "core", "projects",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True,
    "OPTIONS": {"context_processors": ["django.template.context_processors.request",
    "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "config.wsgi.application"
DB_NAME = os.environ.get("DB_NAME", "sem_test")
DB_USER = os.environ.get("DB_USER", "sem_test_app")
if DB_NAME not in {"sem_prod", "sem_test"}:
    raise ImproperlyConfigured("Unknown SEM database")
if DB_USER not in {f"{DB_NAME}_app", f"{DB_NAME}_migrate"}:
    raise ImproperlyConfigured("Database and account environment must match")
DATABASES = {"default": {
    "ENGINE": "django.contrib.gis.db.backends.postgis", "NAME": DB_NAME,
    "USER": DB_USER, "PASSWORD": secret("DB_PASSWORD"),
    "HOST": "db-pg", "PORT": "5432", "CONN_MAX_AGE": 60,
    "OPTIONS": {"hostaddr": "192.168.178.152", "sslmode": "verify-full",
                "sslrootcert": os.environ.get("DB_SSLROOTCERT", str(BASE_DIR / "ops/database/certs/db-pg.crt")),
                "connect_timeout": 5},
    "TEST": {"NAME": "sem_test"},
}}
AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "nl"
TIME_ZONE = "Europe/Amsterdam"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
CSRF_TRUSTED_ORIGINS = ["https://sem.lohr.nl"]
# Set only once the proxy strips incoming forwarded headers and direct access is blocked.
if os.environ.get("TRUST_PROXY_HTTPS") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_BROKER_CONNECTION_TIMEOUT = 3
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_TIME_LIMIT = 900
CELERY_TASK_SOFT_TIME_LIMIT = 840
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TIMEZONE = TIME_ZONE

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/projects/"
LOGOUT_REDIRECT_URL = "/accounts/login/"
