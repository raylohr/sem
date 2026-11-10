"""Import all supported reports and all available rank history for linked projects."""
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from integrations.models import SERankingConnection
from integrations.seranking import ProjectClient, REPORTS, SERankingError, site_audits, verify_connection
from integrations.seranking_tasks import process_seranking
from projects.models import Project
from projects.permissions import can_manage
from sources.models import ImportBatch


from integrations.seranking_history import history_bounds, missing_periods

class Command(BaseCommand):
    help = 'Import all supported SE Ranking reports for active authorized connections; retain prior imports.'

    def add_arguments(self, parser):
        parser.add_argument('--project', help='Optional local project UUID; defaults to active linked projects only.')

    def handle(self, *args, **options):
        connections = SERankingConnection.objects.filter(active=True).select_related('project', 'owner')
        if options.get('project'):
            connections = connections.filter(project_id=options['project'])
        if not connections.exists():
            raise CommandError('Geen actieve SE Ranking-koppelingen gevonden.')
        client = ProjectClient()
        failures = []
        for connection in connections:
            if not can_manage(connection.owner, connection.project):
                failures.append(f'{connection.project.name}: eigenaar heeft geen beheerrechten.')
                continue
            self.stdout.write(f'Project: {connection.project.name}')
            try:
                verify_connection(client, connection)
            except SERankingError as error:
                failures.append(str(error))
                continue
            for report in REPORTS:
                if report not in ('rankings', 'audit_pages'):
                    self.run_report(connection, report, failures)
            try:
                checkpoints = client.get('checkpoints', site_id=connection.site_id)
                start, end = history_bounds(checkpoints)
                self.stdout.write(f'Beschikbare positiehistorie: {start} – {end}')
                covered = list(ImportBatch.objects.filter(seranking_connection=connection, source='seranking',
                    request_metadata__type='rankings', status='complete', period_start__isnull=False,
                    period_end__isnull=False).values_list('period_start', 'period_end'))
                for left, right in missing_periods(start, end, covered):
                    self.run_report(connection, 'rankings', failures, start=left, end=right,
                                    extra={'history_checkpoints': checkpoints})
            except (SERankingError, CommandError) as error:
                failures.append(str(error))
            try:
                for audit in site_audits(client, connection):
                    audit_id = int(audit['id'])
                    stamp = audit.get('last_update')
                    existing = ImportBatch.objects.filter(seranking_connection=connection, source='seranking',
                        request_metadata__type='audit_pages', request_metadata__audit_id=audit_id, status='complete')
                    if stamp and (existing.filter(request_metadata__audit_date=stamp).exists()
                                  or existing.filter(records__data__date=stamp).exists()):
                        self.stdout.write(f'audit_pages {audit_id}: bestaande complete import behouden')
                        continue
                    self.run_report(connection, 'audit_pages', failures,
                                    extra={'audit_id': audit_id, 'audit_date': stamp})
            except (SERankingError, ValueError, KeyError) as error:
                failures.append(str(error) if isinstance(error, SERankingError) else 'Ongeldige auditgegevens.')
        for failure in failures:
            self.stderr.write(failure)
        if failures:
            self.stdout.write(f'{len(failures)} rapport(en) niet beschikbaar of mislukt; overige imports zijn behouden.')

    def run_report(self, connection, report, failures, *, start=None, end=None, extra=None):
        label, scope = REPORTS[report]
        metadata = {'type': report, 'record_scope': scope, 'site_id': connection.site_id,
                    'site_name': connection.site_name, 'report_label': label, 'api_scope': 'project-only',
                    'import_scope': 'all-available-project-data', **(extra or {})}
        with transaction.atomic():
            project = Project.objects.select_for_update().get(pk=connection.project_id)
            if not can_manage(connection.owner, project) or not SERankingConnection.objects.filter(pk=connection.pk, active=True).exists():
                raise CommandError('SE Ranking-projecttoegang ingetrokken.')
            existing = ImportBatch.objects.filter(seranking_connection=connection, source='seranking',
                request_metadata__type=report, period_start=start, period_end=end,
                imported_at__date=timezone.localdate(), status__in=('complete', 'pending', 'processing', 'failed'))
            if report == 'audit_pages':
                existing = existing.filter(request_metadata__audit_id=metadata['audit_id'], request_metadata__audit_date=metadata.get('audit_date'))
            batch = existing.first()
            if batch is None:
                batch = ImportBatch.objects.create(project=project, created_by=connection.owner,
                    source='seranking', method='api', seranking_connection=connection,
                    label='SE Ranking · '+label, parser_version='seranking-project-v2',
                    period_start=start, period_end=end, request_metadata=metadata)
            if batch.status == 'failed':
                batch.status = 'pending'
                batch.error = ''
                batch.save(update_fields=['status', 'error'])
        if batch.status == 'pending':
            process_seranking(str(batch.pk))
        batch.refresh_from_db()
        self.stdout.write(f'{report}: {batch.status}, {batch.record_count} records, batch {batch.pk}')
        if batch.status == 'failed':
            failures.append(f'{report}: {batch.error}')
