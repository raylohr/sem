from django.core.management.base import BaseCommand
from sources.models import ImportBatch
from assessments.services import rebuild_batch


class Command(BaseCommand):
    help = 'Herbouw paginametingen, links en analyses; optioneel uit opgeslagen HTML, zonder netwerkcrawl.'
    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--project')
        group.add_argument('--batch')
        parser.add_argument('--recover-documents', action='store_true')
    def handle(self, *args, **options):
        batches = ImportBatch.objects.filter(source__in=('semscan', 'crawler'), status__in=('complete', 'incomplete'))
        batches = batches.filter(pk=options['batch']) if options['batch'] else batches.filter(project_id=options['project'])
        count = 0
        for batch in batches.iterator():
            if batch.scan_kind != 'crawl':
                continue
            run = rebuild_batch(batch.pk, recover_documents=options['recover_documents'])
            count += 1
            self.stdout.write(f'{batch.pk}: {run.coverage["pages"]} pagina’s, analyse {run.pk}')
        self.stdout.write(f'{count} crawls geanalyseerd; oorspronkelijke bronrecords behouden.')
