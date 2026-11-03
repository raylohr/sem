import hashlib
from celery import shared_task
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.utils import timezone
from browser_scans.models import BrowserRun
from scans.network import normalize_host, ScanError
from urllib.parse import urlsplit
from .models import EnrichmentRun, DomainObservation, DetectionRule
from .services import check_run, profile, fetch_policies, privacy_analysis
from .detection import detect_visit, detect_domain
from .network import observe_domain


@shared_task(ignore_result=True,acks_late=True,reject_on_worker_lost=True,soft_time_limit=110,time_limit=125)
def enrich(run_id):
    lock=int(hashlib.sha256(('intel:'+str(run_id)).encode()).hexdigest()[:15],16)
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s)',[lock]);acquired=cursor.fetchone()[0]
    if not acquired:return
    try:
        run=check_run(run_id)
        if run.status in ('complete','incomplete'):return
        browser=BrowserRun.objects.get(batch_id=run.batch_id)
        run.status='processing';run.started_at=run.started_at or timezone.now()
        if (timezone.now()-run.started_at).total_seconds()>7200:
            raise ScanError('Totale verrijkingslimiet bereikt.')
        selected = DetectionRule.objects.filter(pk__in=run.coverage['rule_ids']) if 'rule_ids' in run.coverage else DetectionRule.objects.filter(active=True)
        rules=list(selected.select_related('technology'))
        run.coverage={**run.coverage,'rule_versions':sorted({rule.version for rule in rules}),'rule_ids':[rule.pk for rule in rules]}
        run.save(update_fields=['status','started_at','coverage'])
        if not run.coverage.get('technologies_extracted'):
            for visit in browser.visits.all().iterator(chunk_size=10):
                check_run(run_id)
                detect_visit(run,visit,rules)
            hosts=set(browser.config['allowed_hosts'])
            for url in browser.visits.values_list('resources__url',flat=True).iterator():
                if len(hosts)>=30:break
                if not url:continue
                try:hosts.add(normalize_host(urlsplit(url).hostname))
                except (ScanError,ValueError,TypeError):continue
            DomainObservation.objects.bulk_create([DomainObservation(run=run,hostname=host,coverage={'status':'pending'}) for host in sorted(hosts)[:30]],ignore_conflicts=True)
            run.coverage={**run.coverage,'technologies_extracted':True,'domain_limit':30,'domains_truncated':len(hosts)>=30}
            run.save(update_fields=['coverage'])
        domain=run.domains.filter(coverage__status='pending').order_by('id').first()
        if domain:
            result=observe_domain(domain.hostname,lambda:check_run(run_id))
            check_run(run_id)
            domain.measured_at=timezone.now()
            domain.records=result['records'];domain.network=result['network'];domain.coverage={'status':'complete',**result['coverage']}
            domain.save(update_fields=['records','network','coverage','measured_at'])
            detect_domain(run,domain,rules)
        if run.domains.filter(coverage__status='pending').exists():
            enrich.apply_async(args=[run_id],countdown=1)
            return
        fetch_policies(run,browser)
        privacy_analysis(run,browser)
        check_run(run_id)
        run.european_profile=profile(run)
        run.status='incomplete' if run.coverage.get('detections_truncated') or run.coverage.get('html_detection_truncated') or run.coverage.get('domains_truncated') or any(len(domain.coverage)>1 for domain in run.domains.all()) else 'complete'
        run.completed_at=timezone.now();run.error=''
        run.save(update_fields=['european_profile','status','completed_at','error'])
    except EnrichmentRun.DoesNotExist:return
    except PermissionDenied:
        EnrichmentRun.objects.filter(pk=run_id).update(status='failed',error='Bron, projecttoegang of scandoel ingetrokken.')
    except Exception as exc:
        EnrichmentRun.objects.filter(pk=run_id).update(status='failed',error='Verrijking onderbroken ('+type(exc).__name__+'); herstart behoudt de reeds vastgelegde waarnemingen.')
    finally:
        with connection.cursor() as cursor:cursor.execute('SELECT pg_advisory_unlock(%s)',[lock])
