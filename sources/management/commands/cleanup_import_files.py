from django.core.management.base import BaseCommand
from sources.tasks import cleanup_files


class Command(BaseCommand):
    help = "Retry durable pending import-file deletions without requiring the broker"
    def handle(self, *args, **options):
        cleanup_files()
        self.stdout.write("Pending file cleanup completed.")
