from celery import shared_task
from django.core.exceptions import PermissionDenied
from django.db import transaction
from projects.models import Project
from projects.permissions import can_edit
from sources.models import ImportBatch
from sources.services import publish_records
from .models import ScanTarget, ScanDocument
from .network import SafeClient, ScanError
from .outcomes import summarize, outcome_warning
from .runners import crawl, sitemap
from .work import ScanWork, ScanPause, ScanBusy
from .limits import SLICE_SECONDS, scan_budget, DEFAULT_SCAN_SETTINGS
from django.utils import timezone
from itertools import islice
import time
import logging

logger = logging.getLogger(__name__)


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
    from .scope import host_scope
    batch.current_host_scope=host_scope(target)
    from .host_rules import navigation_hosts
    current_hosts=navigation_hosts(batch.current_host_scope) if metadata.get('include_subdomains') else set(batch.current_host_scope['allowed_hosts'])
    if any(host not in current_hosts for host in metadata.get('allowed_hosts', [])):
        raise PermissionDenied
    if metadata.get('include_subdomains') and any(host in navigation_hosts(metadata) for host in set(batch.current_host_scope['blocked_hosts'])-set(metadata.get('blocked_hosts',[]))):
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
    from .telemetry import snapshot
    progress = {'live':snapshot(work.connection),'records':len(work.records),'documents':len(work.documents),
                'heartbeat':timezone.now().isoformat(),'started_at':work.started,'resources':work.get('resource_progress',{})}
    metadata = {**batch.request_metadata,'scan_progress':progress}
    ImportBatch.objects.filter(pk=batch.pk,status='processing').update(request_metadata=metadata)


@shared_task(ignore_result=True,acks_late=True,reject_on_worker_lost=True,
             soft_time_limit=3540,time_limit=3600)
def process_scan(batch_id):
    try:
        batch = check_scan(batch_id)
        if batch.status in ('complete','incomplete'):
            return
        with ScanWork(batch_id) as work:
            # Recheck after taking the cross-process file lock.
            batch = check_scan(batch_id)
            if batch.status in ('complete','incomplete'):
                work.clean = True
                return
            ImportBatch.objects.filter(pk=batch_id).exclude(status__in=['complete','incomplete','cancelled']).update(status='processing',error='')
            config = batch.request_metadata
            _, transfer = scan_budget(config['scan'], config['max_urls'])
            remaining = max(0,config['duration']-(time.time()-work.started))
            from .host_rules import navigation_hosts
            client = SafeClient(navigation_hosts(config),check_access=lambda:check_scan(batch_id),
                batch_id=batch_id,duration=remaining,slice_seconds=SLICE_SECONDS,max_transfer=config.get('max_transfer',transfer),
                document_sink=work.documents,robot_cache=work.get('robots',{}),
                respect_robots=config.get('respect_robots',True),
                connect_timeout=config.get('connect_timeout',5),read_timeout=config.get('read_timeout',5),
                concurrency=config.get('concurrency',DEFAULT_SCAN_SETTINGS['concurrency']),delay=1/config.get('requests_per_second',DEFAULT_SCAN_SETTINGS['requests_per_second']),shared_rate=True)
            client.transferred = work.get('transferred',0)
            from .scope import HostDiscovery, registered_domain
            client.host_discovery = HostDiscovery(batch)
            client.discover_sitemaps = config.get('discover_sitemaps',False)
            client.separate_resources=bool(config.get('resource_version') and config.get('check_resources'))
            client.reliable_retries=bool(config.get('reliability_version'))
            client.max_attempts=config.get('max_attempts',3)
            work.set('configured_rps',config.get('requests_per_second',1))
            work.set('configured_concurrency',config.get('concurrency',1))
            slowdown=work.get('retry_slowdown',1)
            delay=getattr(client,'delay',1)
            client.delay=max(delay,slowdown/config.get('requests_per_second',1)) if isinstance(delay,(int,float)) else 1
            client.concurrency=max(1,config.get('concurrency',1)//slowdown)
            if (config.get('retry_of_batch') or config.get('browser_followup_run')) and not work.get('retry_targets_initialized'):
                import json
                from .network import normalize_url
                for target in batch.retry_targets.order_by('id').iterator(chunk_size=500):
                    normalized=normalize_url(target.url,navigation_hosts(config))
                    work.add('crawl',normalized,0)
                    with work.connection:work.connection.execute('INSERT OR REPLACE INTO retry_origins VALUES (?,?)',(normalized,json.dumps(target.snapshot)))
                if config.get('retry_of_batch'):
                    work.set('retry_only',True);work.set('retry_of_batch',config['retry_of_batch'])
                else:work.set('browser_followup_run',config['browser_followup_run'])
                work.set('retry_targets_initialized',True)
            client.decoded_bytes = work.get('decoded_bytes',0)
            checkpoint(batch,work)
            runner = crawl if config['scan']=='crawl' else sitemap
            if config.get('discover_sitemaps'):
                from .seeding import initialize
                initialize(work)
            try:
                if work.get('page_phase_done'):
                    records,warnings=work.records,work.get('page_phase_warnings',[])
                else:
                    records,warnings = runner(client,url=config['url'],max_urls=config['max_urls'],max_depth=config['max_depth'],work=work)
                    from browser_scans.followups import annotate
                    annotate(work)
                    from .activity import phase
                    phase(batch.pk,'pages',status='partial' if work.get('network_budget_ended') else 'complete',done=len(work.records),total=len(work.records))
                    work.set('page_phase_done',True);work.set('page_phase_warnings',list(warnings))
                if config.get('resource_version') and (config.get('check_resources') or config.get('check_external_links')) and not work.get('resources_done'):
                    from .resources import run as run_resources
                    run_resources(batch,work,config,lambda:check_scan(batch_id))
                resource_summary=work.get('resource_progress',{})
                if resource_summary.get('discovery_incomplete') or any(outcome not in ('success','http_error','response') for counts in resource_summary.get('outcomes',{}).values() for outcome in counts):
                    warnings=list(warnings)+['Bestands- of externe meting gedeeltelijk: bekijk afzonderlijke budgetten, blokkades en onbekende uitkomsten bij Bestanden en externe links.']
            except ScanPause as pause:
                client.close()
                checkpoint(batch,work,client)
                check_scan(batch_id)
                try:
                    process_scan.apply_async(args=[str(batch_id)],countdown=pause.delay_seconds)
                except Exception:
                    ImportBatch.objects.filter(pk=batch_id,status='processing').update(status='failed',error='Vervolgscan kon niet worden ingepland; opnieuw starten hervat het checkpoint.')
                return
            finally:
                client.close()
                if config.get('reliability_version'):
                    from .reliability import publish_attempts
                    publish_attempts(batch,work)
            checkpoint(batch,work,client)
            summary = summarize(records) if config['scan'] == 'crawl' else None
            if summary and config.get('discover_sitemaps'):
                limited=not work.get('seed_maps_done',False) or work.get('seed_maps_truncated',False) or bool(work.next('crawl')) or work.get('network_budget_ended',False)
                summary['discovery_limited']=bool(limited)
                summary['incomplete']=summary['incomplete'] or bool(limited)
                if limited:
                    warnings=list(warnings)+['Onvolledige URL-dekking: sitemapontdekking of de pagina-wachtrij is begrensd. Bekijk de sitemapdekking en crawllimieten.']
            from .models import HostPolicy
            pending=[p.host for p in HostPolicy.objects.filter(project_id=batch.project_id,navigation='pending').order_by('host')
                     if p.host not in config['allowed_hosts'] and registered_domain(p.host)==client.host_discovery.domain][:100]
            if config.get('include_subdomains'):pending=[]
            if pending:
                warnings=list(warnings)+['Het toegestane deel is onderzocht. Andere subdomeinen wachten op beoordeling bij Websitescans: '+', '.join(pending)+'. Start na toestemming een nieuwe crawl.']
            if summary and summary['incomplete']:
                warnings = list(warnings) + [outcome_warning(summary)]
            from .activity import phase
            if config['scan']=='crawl':phase(batch.pk,'analysis',done=0,total=len(work.records))
            with transaction.atomic():
                Project.objects.select_for_update().get(pk=batch.project_id)
                batch = ImportBatch.objects.select_for_update().get(pk=batch_id)
                check_scan(batch_id)
                if batch.status in ('complete','incomplete'):
                    work.clean = True
                    return
                publish_records(batch_id=batch_id,records=records,derive_assessment=False)
                documents = iter(client.documents)
                index = 0
                while chunk := list(islice(documents,5)):
                    ScanDocument.objects.bulk_create([ScanDocument(batch=batch,index=index+i,**document) for i,document in enumerate(chunk)],batch_size=5)
                    index += len(chunk)
                # publish_records used its own model instance; retain its record count
                # and set a terminal outcome atomically with records/documents.
                batch.refresh_from_db()
                batch.warnings = warnings
                if summary:
                    batch.request_metadata = {**batch.request_metadata,'scan_outcome':summary}
                    if summary['incomplete']:
                        batch.status = 'incomplete'
                batch.request_metadata={**batch.request_metadata,'host_scope_report':{'pending_hosts':pending,
                    'allowed_hosts':config['allowed_hosts'],'blocked_hosts':config.get('blocked_hosts',[])}}
                if config.get('reliability_version'):
                    batch.request_metadata['download_attempts']={
                        'total':batch.crawl_attempts.count(),'retried_urls':batch.crawl_attempts.filter(ordinal__gt=1).values('url_hash').distinct().count(),
                        'recovered_urls':sum(bool(record.get('data',{}).get('recovered_after_retry')) for record in records),
                        'recovered_followup_urls':sum(bool(record.get('data',{}).get('recovered_from_previous_scan')) for record in records),
                        'definition':'logical page fetch including redirects; robots and sitemap requests are not separate page attempts'}
                if config.get('discover_sitemaps'):
                    batch.request_metadata['sitemap_discovery']={
                        'completed':work.get('seed_maps_done',False),'documents':work.get('seed_maps_found',0),
                        'truncated':work.get('seed_maps_truncated',False),'provenance_truncated':work.get('provenance_truncated',False),
                        'urls':work.connection.execute('SELECT count(*) FROM crawl_origins WHERE sitemap=1').fetchone()[0],
                        'sitemap_only':work.connection.execute("SELECT count(*) FROM crawl_origins o JOIN frontier f ON f.url=o.url AND f.kind='crawl' WHERE o.sitemap=1 AND o.link=0 AND o.start=0 AND f.done=1").fetchone()[0],
                        'sitemap_and_links':work.connection.execute("SELECT count(*) FROM crawl_origins o JOIN frontier f ON f.url=o.url AND f.kind='crawl' WHERE o.sitemap=1 AND o.link=1 AND f.done=1").fetchone()[0],
                        'visited_sitemap_urls':work.connection.execute("SELECT count(*) FROM crawl_origins o JOIN frontier f ON f.url=o.url AND f.kind='crawl' WHERE o.sitemap=1 AND f.done=1").fetchone()[0]}
                batch.save(update_fields=['warnings','request_metadata','status'])
                if config.get('resource_version'):
                    batch.request_metadata['resource_scan']=work.get('resource_progress',{'resources_enabled':False,'external_enabled':False})
                    batch.save(update_fields=['request_metadata'])
                    if work.get('resources_done'):
                        from .resources import publish as publish_resources
                        publish_resources(batch,work)
                if config['scan'] == 'crawl':
                    if config.get('reliability_version'):
                        from .coverage import publish_coverage
                        publish_coverage(batch,work)
                        from django.db.models import Count,Q
                        batch.request_metadata['url_inventory']=batch.crawl_url_coverage.aggregate(found=Count('id'),queued=Count('id',filter=Q(queued=True)),
                            attempted=Count('id',filter=Q(attempted=True)),successful=Count('id',filter=Q(outcome='success')))
                        batch.save(update_fields=['request_metadata'])
                    from assessments.services import rebuild_batch
                    rebuild_batch(batch.pk)
                    if config.get('browser_phase') and not config.get('retry_only'):
                        from browser_scans.workflow import schedule_after_crawl
                        transaction.on_commit(lambda:schedule_after_crawl(batch.pk))
            work.clean = True
    except (ImportBatch.DoesNotExist,ScanCancelled,ScanBusy):
        return
    except PermissionDenied:
        ImportBatch.objects.filter(pk=batch_id).exclude(status__in=['complete','incomplete','cancelled']).update(status='failed',error='Projecttoegang of scandoel ingetrokken.')
    except ScanError as exc:
        ImportBatch.objects.filter(pk=batch_id).exclude(status__in=['complete','incomplete','cancelled']).update(status='failed',error=str(exc))
    except Exception as exc:
        from .diagnostics import describe_exception
        _, details = describe_exception(exc, 'scan_task')
        logger.error('scan_task_failed batch=%s exceptions=%s', batch_id, ','.join(details['exception_chain']))
        ImportBatch.objects.filter(pk=batch_id).exclude(status__in=['complete','incomplete','cancelled']).update(status='failed',error='Scan mislukt; opnieuw starten hervat het opgeslagen checkpoint.')


def enqueue_scan(batch_id):
    try:
        process_scan.delay(str(batch_id))
    except Exception:
        pass  # Pending batches remain visible and can be retried.
