import getpass
from django.core.management.base import BaseCommand,CommandError
from accounts.models import User
from accounts.services import reset_mfa


class Command(BaseCommand):
    help='Console recovery: revoke MFA and sessions, requiring enrollment on next login'
    def add_arguments(self,parser):
        parser.add_argument('--username',required=True)
        parser.add_argument('--reason',required=True)
        parser.add_argument('--yes',action='store_true')
    def handle(self,*args,**options):
        try:user=User.objects.get(username=options['username'])
        except User.DoesNotExist:raise CommandError('Gebruiker niet gevonden.')
        if not options['yes'] and input(f"2FA en sessies intrekken voor {user.get_username()}? Typ RESET: ")!='RESET':
            raise CommandError('Afgebroken.')
        reset_mfa(actor=None,user_id=user.pk,reason=options['reason'],operator=getpass.getuser())
        self.stdout.write('2FA en sessies ingetrokken; opnieuw instellen is verplicht.')
