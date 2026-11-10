"""Atomic source observations and private documents for a crawl."""
from django.db import transaction
from django.utils import timezone

from sources.models import ImportBatch, SourceRecord, CrawlPageArtifact


def archive_page(crawl,row,data,documents):
    # Caller holds the crawl/URL locks and has rechecked access and attempt token.
    if not crawl.batch_id or row.kind!='page':
        return
    batch=ImportBatch.objects.select_for_update().get(pk=crawl.batch_id)
    if batch.status!='processing':
        return
    stamp=timezone.now()
    observation={key:value for key,value in data.items() if key not in ('links',)}
    observation.update(url=row.url,measured_at=data.get('captured_at') or stamp.isoformat(),
        crawl_id=str(crawl.pk),crawl_status=row.status,depth=row.depth,provenance=row.provenance,
        archived_documents=[key for key in ('server_html','rendered_html','text') if documents is not None and data.get(key+'_bytes',0)>0],
        discovered_from=row.discovered_from,link_count=len(data.get('links',[])),error_detail=row.error)
    record,_=SourceRecord.objects.update_or_create(batch=batch,locator='page:'+row.url_hash,
                                                  defaults={'data':observation})
    if documents is not None:
        CrawlPageArtifact.objects.update_or_create(record=record,defaults={**documents,'captured_at':stamp})


@transaction.atomic
def finish_archive(crawl):
    if not crawl.batch_id or crawl.status in ('pending','preparing','running'):
        return
    batch=ImportBatch.objects.select_for_update().filter(pk=crawl.batch_id,status='processing').first()
    if not batch:
        return
    from projects.permissions import can_manage
    from .playwright_config import project_domain
    authorized=(can_manage(crawl.created_by,crawl.project) and project_domain(crawl.project)==crawl.domain
                and crawl.project.include_subdomains==crawl.configuration['include_subdomains'])
    status=crawl.status if authorized else 'cancelled'
    batch.status=status
    batch.record_count=batch.records.count()
    batch.measured_at=crawl.started_at or crawl.created_at
    batch.error=crawl.error
    batch.warnings=(["Crawl is onvolledig; zie dekking en foutmeldingen."] if status=='incomplete' else [])
    batch.request_metadata={**batch.request_metadata,'finished_at':crawl.finished_at.isoformat() if crawl.finished_at else None,
        'crawl_status':crawl.status,'limit_reached':crawl.limit_reached,'notes':crawl.notes,
        'coverage':{kind:{state:crawl.urls.filter(kind=kind,status=state).count()
                         for state in ('complete','failed','skipped','pending','queued','processing')}
                    for kind in ('page','external')}}
    batch.save(update_fields=['status','record_count','measured_at','error','warnings','request_metadata'])
