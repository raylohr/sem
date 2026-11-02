from django.core.management.base import BaseCommand
from django.db import transaction
from projects.models import Project
from sources.models import ImportBatch
from scans.outcomes import summarize, outcome_warning


class Command(BaseCommand):
    help = 'Review completed website crawls for retrieval failures; --apply corrects their status without deleting data.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        ids = ImportBatch.objects.filter(method='scan',status='complete',request_metadata__scan='crawl').values_list('pk',flat=True)
        changed = 0
        for batch_id in ids.iterator(chunk_size=100):
            with transaction.atomic():
                project_id = ImportBatch.objects.values_list('project_id',flat=True).get(pk=batch_id)
                Project.objects.select_for_update().get(pk=project_id)
                batch = ImportBatch.objects.select_for_update().get(pk=batch_id)
                if batch.status != 'complete':
                    continue
                summary = summarize({'data':data} for data in batch.records.values_list('data',flat=True).iterator(chunk_size=25))
                if not summary['incomplete']:
                    continue
                changed += 1
                self.stdout.write(f'{batch_id}: Onvolledig; {summary["request_failures"]} downloads mislukt, {summary["blocked_responses"]} geblokkeerd, {summary["server_errors"]} serverfouten')
                if options['apply']:
                    batch.status = 'incomplete'
                    batch.request_metadata = {**batch.request_metadata,'scan_outcome':summary}
                    batch.warnings = list(batch.warnings) + [outcome_warning(summary)]
                    batch.save(update_fields=['status','request_metadata','warnings'])
        self.stdout.write(f'{changed} scans ' + ('bijgewerkt.' if options['apply'] else 'gevonden; geen wijzigingen (--apply om toe te passen).'))
