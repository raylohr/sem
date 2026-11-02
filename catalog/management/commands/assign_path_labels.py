import json
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from catalog.path_labels import assign_path_labels


class Command(BaseCommand):
    help = 'Classificeer URL’s met de vastgestelde expertisepaden; standaard alleen een dry run.'

    def add_arguments(self, parser):
        parser.add_argument('project_id')
        parser.add_argument('--actor', required=True)
        parser.add_argument('--language', choices=['nl', 'en', 'both'], default='en')
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        actor = get_user_model().objects.get(username=options['actor'], is_active=True)
        result = assign_path_labels(project_id=options['project_id'], actor=actor,
            language=options['language'], apply=options['apply'])
        self.stdout.write(json.dumps(result, ensure_ascii=False))
