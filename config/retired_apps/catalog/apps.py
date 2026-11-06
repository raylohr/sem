from django.apps import AppConfig


class CatalogMigrationsConfig(AppConfig):
    name = "config.retired_apps.catalog"
    label = "catalog"
    verbose_name = "Retired migrations: catalog"
    default_auto_field = "django.db.models.BigAutoField"
