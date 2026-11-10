from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from integrations.crawl_service import start_crawl


class Command(BaseCommand):
    help = 'Start an asynchronous Playwright crawl using saved project configuration.'

    def add_arguments(self,parser):
        parser.add_argument('--project',required=True)
        parser.add_argument('--actor',required=True,type=int)
        parser.add_argument('--max-pages',type=int)
        parser.add_argument('--max-depth',type=int)
        parser.add_argument('--max-minutes',type=int)

    def handle(self,*args,**options):
        try:
            actor=get_user_model().objects.get(pk=options['actor'],is_active=True)
            overrides={field:options[key] for key,field in (('max_pages','max_pages'),('max_depth','max_depth'),
                ('max_minutes','max_duration_minutes')) if options.get(key) is not None}
            crawl=start_crawl(actor=actor,project_id=options['project'],overrides=overrides)
        except (ValidationError,get_user_model().DoesNotExist) as error:
            raise CommandError(str(error)) from None
        self.stdout.write(f'Crawl gepland: {crawl.pk}')
