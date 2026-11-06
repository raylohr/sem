from django.apps import AppConfig


class BrowserScansMigrationsConfig(AppConfig):
    name = "config.retired_apps.browser_scans"
    label = "browser_scans"
    verbose_name = "Retired migrations: browser_scans"
    default_auto_field = "django.db.models.BigAutoField"
