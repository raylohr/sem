"""One explicit, idempotent baseline browser phase after a completed HTML crawl."""
from django.db import transaction
from projects.models import Project
from sources.models import ImportBatch
from assessments.models import PageMeasurement
from .services import create_run


def schedule_after_crawl(batch_id):
    run=None
    try:
        project_id=ImportBatch.objects.values_list('project_id',flat=True).get(pk=batch_id)
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=project_id)
            source=ImportBatch.objects.select_for_update(of=('self',)).select_related('scan_target','created_by').get(pk=batch_id)
            config=source.request_metadata
            if not config.get('browser_phase') or config.get('retry_only') or source.scan_kind!='crawl' or source.status not in ('complete','incomplete'):return
            if config.get('browser_followup_id'):return
            if not PageMeasurement.objects.filter(batch=source,final_http_status=200,coverage__html_inspected=True).exists():
                source.request_metadata={**config,'browser_phase_status':'skipped_no_html'};source.save(update_fields=['request_metadata']);return
            run=create_run(source,source.created_by,{'max_pages':config.get('browser_max_pages',20),'timeout_seconds':config.get('browser_timeout_seconds',30),
                'profile':'desktop','scroll_steps':6,'expand_details':True,'requests_per_second':config.get('browser_requests_per_second',5)})
            run.config['automatic_from_crawl']=True;run.save(update_fields=['config'])
            source.request_metadata={**config,'browser_followup_id':str(run.pk),'browser_phase_status':'scheduled'}
            source.save(update_fields=['request_metadata'])
        from .tasks import process_browser
        process_browser.delay(str(run.pk))
    except Exception:
        if run:ImportBatch.objects.filter(pk=run.batch_id,status='pending').update(status='failed',error='Automatische browserfase kon niet worden ingepland; hervat via Browseronderzoek.')
        source=ImportBatch.objects.filter(pk=batch_id).first()
        if source:
            source.request_metadata={**source.request_metadata,'browser_phase_status':'failed'};source.save(update_fields=['request_metadata'])
