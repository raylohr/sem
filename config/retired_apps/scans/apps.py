from django.apps import AppConfig


class ScansMigrationsConfig(AppConfig):
    name = "config.retired_apps.scans"
    label = "scans"
    verbose_name = "Retired migrations: scans"
    default_auto_field = "django.db.models.BigAutoField"
