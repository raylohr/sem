from celery import shared_task
from django.core.exceptions import ValidationError,PermissionDenied
from .models import Dossier
from .services import assemble


@shared_task(ignore_result=True,acks_late=True,reject_on_worker_lost=True,soft_time_limit=600,time_limit=630)
def build_dossier(dossier_id):
    try:assemble(dossier_id)
    except Dossier.DoesNotExist:return
    except ValidationError as exc:
        Dossier.objects.filter(pk=dossier_id).exclude(status='complete').update(status='failed',error=' '.join(exc.messages)[:255])
    except PermissionDenied:
        Dossier.objects.filter(pk=dossier_id).exclude(status='complete').update(status='failed',error='Projecttoegang voor het opbouwen van het dossier is ingetrokken.')
    except Exception as exc:
        Dossier.objects.filter(pk=dossier_id).exclude(status='complete').update(status='failed',error='Dossieropbouw onderbroken ('+type(exc).__name__+'). De bronanalyses zijn behouden.')
