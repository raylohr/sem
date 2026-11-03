import logging
from celery import shared_task
from django.db import transaction
from django.utils import timezone
from projects.models import Project
from projects.permissions import can_edit
from .models import ReprocessingJob
from .services import rebuild_batch

logger = logging.getLogger(__name__)


@shared_task(ignore_result=True, acks_late=True, reject_on_worker_lost=True)
def process_reprocessing(job_id):
    try:
        with transaction.atomic():
            project_id = ReprocessingJob.objects.values_list('batch__project_id', flat=True).get(pk=job_id)
            project = Project.objects.select_for_update().get(pk=project_id)
            job = ReprocessingJob.objects.select_for_update().select_related('actor', 'batch').get(pk=job_id)
            if job.status not in ('pending', 'processing'):
                return
            if not can_edit(job.actor, project):
                job.status, job.error = 'failed', 'Projecttoegang ingetrokken.'
            else:
                job.status = 'processing'
                job.save(update_fields=['status'])
                job.run = rebuild_batch(job.batch_id, recover_documents=True)
                job.status = 'complete'
            job.completed_at = timezone.now()
            job.save(update_fields=['status', 'error', 'run', 'completed_at'])
    except ReprocessingJob.DoesNotExist:
        return
    except Exception:
        logger.error('assessment_reprocessing_failed job=%s', job_id)
        ReprocessingJob.objects.filter(pk=job_id).update(status='failed', error='Heranalyse mislukt. De vorige resultaten zijn behouden.', completed_at=timezone.now())
