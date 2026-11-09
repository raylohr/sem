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
    "django.contrib.gis", "django_otp", "django_otp.plugins.otp_static", "django_otp.plugins.otp_totp", "two_factor", "accounts.apps.AccountsConfig", "core", "projects", "sources", "integrations",
]
# Migration compatibility only; explicit labels preserve the existing migration graph.
INSTALLED_APPS += [
    "config.retired_apps.catalog.apps.CatalogMigrationsConfig",
    "config.retired_apps.scans.apps.ScansMigrationsConfig",
    "config.retired_apps.browser_scans.apps.BrowserScansMigrationsConfig",
    "config.retired_apps.assessments.apps.AssessmentsMigrationsConfig",
    "config.retired_apps.reporting.apps.ReportingMigrationsConfig",
    "config.retired_apps.site_intelligence.apps.SiteIntelligenceMigrationsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware", "accounts.middleware.AccountPolicyMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True,
    "OPTIONS": {"context_processors": ["django.template.context_processors.request",
    "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "config.wsgi.application"
DB_NAME = os.environ.get("DB_NAME", "sem_prod")
DB_USER = os.environ.get("DB_USER", "sem_prod_app")
if DB_NAME != "sem_prod":
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
}}
AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "nl"
USE_THOUSAND_SEPARATOR = True
NUMBER_GROUPING = 3
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
CSRF_FAILURE_VIEW = "core.views.csrf_failure"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
CSRF_TRUSTED_ORIGINS = ["https://sem.lohr.nl"]
# Set only once the proxy strips incoming forwarded headers and direct access is blocked.
if os.environ.get("TRUST_PROXY_HTTPS") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
ACCOUNT_EMAIL_ENABLED = os.environ.get("ACCOUNT_EMAIL_ENABLED", "0") == "1"
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend" if ACCOUNT_EMAIL_ENABLED else "django.core.mail.backends.dummy.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "0") == "1"
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://sem.lohr.nl")
if ACCOUNT_EMAIL_ENABLED and (not EMAIL_HOST or not DEFAULT_FROM_EMAIL or (EMAIL_USE_TLS and EMAIL_USE_SSL)):
    raise ImproperlyConfigured("Configure SMTP host/from address and exactly the intended TLS mode")
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
LOGIN_REDIRECT_URL = "/workspace/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

DATA_UPLOAD_MAX_MEMORY_SIZE = 21 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FILES = 1
FILE_UPLOAD_PERMISSIONS = 0o600

GSC_CLIENT_ID = os.environ.get("GSC_CLIENT_ID", "")
GSC_CLIENT_SECRET = os.environ.get("GSC_CLIENT_SECRET", "")
CELERY_BEAT_SCHEDULE = {
    "inventory-export-dispatch": {"task": "sources.tasks.dispatch_inventory_exports", "schedule": 30.0},
    "inventory-export-cleanup": {"task": "sources.tasks.cleanup_inventory_exports", "schedule": 3600.0},
    "inventory-rebuild": {"task": "sources.tasks.rebuild_pending_inventories", "schedule": 30.0},
    "gsc-daily-import": {"task": "integrations.tasks.schedule_daily_gsc", "schedule": 3600.0},
}

GSC_REDIRECT_URI = os.environ.get("GSC_REDIRECT_URI", "https://sem.lohr.nl/integrations/gsc/callback/")
INTEGRATION_TOKEN_KEY = os.environ.get("INTEGRATION_TOKEN_KEY", "")

CELERY_TASK_ROUTES = {}

CACHES = {"default": {"BACKEND":"django.core.cache.backends.redis.RedisCache",
    "LOCATION":os.environ.get("AUTH_CACHE_URL","redis://redis:6379/2"),
    "KEY_PREFIX":f"sem-{DB_NAME}","OPTIONS":{"socket_connect_timeout":2,"socket_timeout":2}}}
TWO_FACTOR_PATCH_ADMIN = False
TWO_FACTOR_REMEMBER_COOKIE_AGE = None
TWO_FACTOR_LOGIN_TIMEOUT = 600
OTP_TOTP_ISSUER = "TrafficStack"
OTP_TOTP_THROTTLE_FACTOR = 1
OTP_STATIC_THROTTLE_FACTOR = 1
PASSWORD_RESET_TIMEOUT = 3600
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# Tests must explicitly select the isolated test configuration.
TEST_RUNNER = "config.test_runner.IsolatedDatabaseTestRunner"

# SE Ranking is restricted by integrations.seranking to read-only Project endpoints.
SERANKING_API_KEY = os.environ.get("SERANKING_API_KEY", "")
