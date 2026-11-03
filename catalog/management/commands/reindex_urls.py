from django.core.management.base import BaseCommand
from sources.models import ImportBatch
from catalog.services import index_batch


class Command(BaseCommand):
    help = "Rebuild derived URL observations from complete source batches without altering raw records"
    def add_arguments(self, parser):
        parser.add_argument("--project")
        parser.add_argument("--recover-documents", action="store_true", help="Herlees opgeslagen HTML zonder netwerkrequests")
    def handle(self, *args, **options):
        batches = ImportBatch.objects.filter(status__in=("complete","incomplete")).order_by("imported_at")
        if options["project"]:
            batches = batches.filter(project_id=options["project"])
        total = 0
        for batch_id in batches.values_list("id",flat=True).iterator():
            total += index_batch(batch_id, recover_documents=options["recover_documents"])
        self.stdout.write(f"Indexed {total} URL observations.")
