from celery import shared_task
from django.core.exceptions import PermissionDenied
from django.db import transaction
from projects.models import Project
from projects.permissions import can_edit
from sources.models import ImportBatch
from sources.services import publish_records
from .models import ScanTarget, ScanDocument
from .network import SafeClient, ScanError
from .runners import crawl, sitemap
from .work import ScanWork, ScanPause, ScanBusy
from .limits import SLICE_SECONDS, scan_budget
from django.utils import timezone
from itertools import islice
import time


class ScanCancelled(PermissionDenied):
    pass


def check_scan(batch_id):
    batch = ImportBatch.objects.select_related('created_by', 'project', 'scan_target').get(pk=batch_id)
    if batch.status == 'cancelled':
        raise ScanCancelled
    if not can_edit(batch.created_by, batch.project):
        raise PermissionDenied
    target = batch.scan_target
    metadata = batch.request_metadata
    if not target or not target.active or target.project_id != batch.project_id:
        raise PermissionDenied
    if not set(metadata.get('allowed_hosts', [])).issubset(set(target.allowed_hosts)):
        raise PermissionDenied
    return batch


def checkpoint(batch, work, client=None):
    if client is not None:
        for key in ('transferred','decoded_bytes'):
            value = getattr(client,key,0)
            if isinstance(value,(int,float)):
                work.set(key,value)
        if isinstance(client.robot_cache,dict):
            work.set('robots',client.robot_cache)
    progress = {'records':len(work.records),'documents':len(work.documents),
                'heartbeat':timezone.now().isoformat(),'started_at':work.started}
    metadata = {**batch.request_metadata,'scan_progress':progress}
    ImportBatch.objects.filter(pk=batch.pk,status='processing').update(request_metadata=metadata)


@shared_task(ignore_result=True,acks_late=True,reject_on_worker_lost=True,
             soft_time_limit=3540,time_limit=3600)
def process_scan(batch_id):
    try:
        batch = check_scan(batch_id)
        if batch.status == 'complete':
            return
        with ScanWork(batch_id) as work:
            # Recheck after taking the cross-process file lock.
            batch = check_scan(batch_id)
            if batch.status == 'complete':
                work.clean = True
                return
            ImportBatch.objects.filter(pk=batch_id).exclude(status__in=['complete','cancelled']).update(status='processing',error='')
            config = batch.request_metadata
            _, transfer = scan_budget(config['scan'], config['max_urls'])
            remaining = max(0,config['duration']-(time.time()-work.started))
            client = SafeClient(config['allowed_hosts'],check_access=lambda:check_scan(batch_id),
                duration=remaining,slice_seconds=SLICE_SECONDS,max_transfer=config.get('max_transfer',transfer),
                document_sink=work.documents,robot_cache=work.get('robots',{}))
            client.transferred = work.get('transferred',0)
            client.decoded_bytes = work.get('decoded_bytes',0)
            checkpoint(batch,work)
            runner = crawl if config['scan']=='crawl' else sitemap
            try:
                records,warnings = runner(client,url=config['url'],max_urls=config['max_urls'],max_depth=config['max_depth'],work=work)
            except ScanPause:
                checkpoint(batch,work,client)
                check_scan(batch_id)
                try:
                    process_scan.apply_async(args=[str(batch_id)],countdown=1)
                except Exception:
                    ImportBatch.objects.filter(pk=batch_id,status='processing').update(status='failed',error='Vervolgscan kon niet worden ingepland; opnieuw starten hervat het checkpoint.')
                return
            checkpoint(batch,work,client)
            with transaction.atomic():
                Project.objects.select_for_update().get(pk=batch.project_id)
                batch = ImportBatch.objects.select_for_update().get(pk=batch_id)
                check_scan(batch_id)
                if batch.status == 'complete':
                    work.clean = True
                    return
                publish_records(batch_id=batch_id,records=records)
                documents = iter(client.documents)
                index = 0
                while chunk := list(islice(documents,5)):
                    ScanDocument.objects.bulk_create([ScanDocument(batch=batch,index=index+i,**document) for i,document in enumerate(chunk)],batch_size=5)
                    index += len(chunk)
                batch.warnings = warnings
                batch.save(update_fields=['warnings'])
            work.clean = True
    except (ImportBatch.DoesNotExist,ScanCancelled,ScanBusy):
        return
    except PermissionDenied:
        ImportBatch.objects.filter(pk=batch_id).exclude(status__in=['complete','cancelled']).update(status='failed',error='Projecttoegang of scandoel ingetrokken.')
    except ScanError as exc:
        ImportBatch.objects.filter(pk=batch_id).exclude(status__in=['complete','cancelled']).update(status='failed',error=str(exc))
    except Exception:
        ImportBatch.objects.filter(pk=batch_id).exclude(status__in=['complete','cancelled']).update(status='failed',error='Scan mislukt; opnieuw starten hervat het opgeslagen checkpoint.')


def enqueue_scan(batch_id):
    try:
        process_scan.delay(str(batch_id))
    except Exception:
        pass  # Pending batches remain visible and can be retried.
