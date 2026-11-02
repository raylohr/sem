import json
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from catalog.labels import import_labels


class Command(BaseCommand):
    help = 'Importeer herleidbare labels uit een bestaande contentauditbron.'

    def add_arguments(self, parser):
        parser.add_argument('batch_id')
        parser.add_argument('--actor', required=True, help='Gebruikersnaam met projectbewerkrechten')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        actor = get_user_model().objects.get(username=options['actor'], is_active=True)
        result = import_labels(batch_id=options['batch_id'], actor=actor, dry_run=options['dry_run'])
        self.stdout.write(json.dumps(result, ensure_ascii=False))
