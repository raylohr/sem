from django.apps import AppConfig


class ReportingMigrationsConfig(AppConfig):
    name = "config.retired_apps.reporting"
    label = "reporting"
    verbose_name = "Retired migrations: reporting"
    default_auto_field = "django.db.models.BigAutoField"
