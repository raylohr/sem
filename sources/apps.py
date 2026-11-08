from django.apps import AppConfig


class SourcesConfig(AppConfig):
    name = 'sources'

    def ready(self):
        from . import signals  # noqa: F401
