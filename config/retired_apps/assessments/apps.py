from django.apps import AppConfig


class AssessmentsMigrationsConfig(AppConfig):
    name = "config.retired_apps.assessments"
    label = "assessments"
    verbose_name = "Retired migrations: assessments"
    default_auto_field = "django.db.models.BigAutoField"
