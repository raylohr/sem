"""Apply snapshot classification retroactively. Dry-run unless --apply is given; never deletes imports."""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from projects.models import Project
from sources.snapshots import apply_plan, measured_day, plan_project


class Command(BaseCommand):
    help = 'Classificeer bestaande imports (actief, vervangen, redundant, ...). Standaard alleen een rapport.'

    def add_arguments(self, parser):
        parser.add_argument('--project', action='append', default=[],
                            help='Project-ID of (deel van de) naam; herhaalbaar. Standaard alle projecten.')
        parser.add_argument('--apply', action='store_true', help='Schrijf de statussen weg (standaard: dry-run).')

    def handle(self, *args, **options):
        projects = Project.objects.order_by('name')
        if options['project']:
            selected = set()
            for term in options['project']:
                matches = list(projects.filter(pk=term)) if self.is_uuid(term) else list(projects.filter(name__icontains=term))
                if not matches:
                    raise CommandError(f'Geen project gevonden voor "{term}".')
                selected.update(project.pk for project in matches)
            projects = projects.filter(pk__in=selected)
        mode = 'WEGSCHRIJVEN' if options['apply'] else 'DRY-RUN (er wordt niets gewijzigd)'
        self.stdout.write(f'Snapshotclassificatie · {mode}\n')
        changed_total = 0
        for project in projects:
            outcome = plan_project(project)
            if not outcome:
                continue
            self.stdout.write(self.style.MIGRATE_HEADING(f'\n{project.name} ({project.pk})'))
            by_dataset = {}
            for item in outcome.values():
                by_dataset.setdefault(item['batch'].dataset_key, []).append(item)
            for key, items in sorted(by_dataset.items()):
                self.stdout.write(f'  Dataset {key}')
                for item in sorted(items, key=lambda entry: (measured_day(entry['batch']), entry['batch'].imported_at)):
                    batch = item['batch']
                    current = batch.snapshot_status or 'niet geclassificeerd'
                    changed = current != item['status']
                    changed_total += changed
                    self.stdout.write(f"    {'*' if changed else ' '} {timezone.localtime(batch.imported_at):%d-%m-%Y %H:%M} · "
                                      f"{batch.record_count} records · meetmoment {measured_day(batch):%d-%m-%Y} · {batch.label}")
                    self.stdout.write(f"        {current} → {item['status']}: {item['reason']}")
                    for warning in item['warnings']:
                        self.stdout.write(self.style.WARNING(f"        ! {warning['message']}"))
            if options['apply']:
                apply_plan(project, outcome)
                self.stdout.write(self.style.SUCCESS('  Weggeschreven; het URL-overzicht wordt opnieuw opgebouwd.'))
        self.stdout.write(f'\n{changed_total} import(s) krijgen een andere status (* gemarkeerd).')
        if not options['apply']:
            self.stdout.write('Draai opnieuw met --apply om dit weg te schrijven. Er worden nooit imports verwijderd.')

    @staticmethod
    def is_uuid(value):
        import uuid
        try:
            uuid.UUID(value)
        except ValueError:
            return False
        return True
