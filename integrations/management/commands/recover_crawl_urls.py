from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from integrations.crawl_recovery import recover_urls


class Command(BaseCommand):
    help='Herstel bewaarde links, inventaris en sitemaps in dezelfde lopende crawl.'

    def add_arguments(self,parser):
        parser.add_argument('--crawl',required=True)
        parser.add_argument('--actor',required=True,type=int)

    def handle(self,*args,**options):
        actor=get_user_model().objects.get(pk=options['actor'],is_active=True)
        result=recover_urls(actor=actor,crawl_id=options['crawl'],progress=lambda values:self.stdout.write(str(values)))
        self.stdout.write(self.style.SUCCESS(str(result)))
