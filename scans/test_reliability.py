import json
import tempfile
import time
from unittest.mock import patch,MagicMock
from datetime import datetime,timezone as tz
from email.utils import format_datetime
from django.test import SimpleTestCase,TestCase,Client,override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from projects.models import Project,Membership
from sources.models import ImportBatch,SourceRecord
from .models import ScanTarget,CrawlAttempt,CrawlURLCoverage
from .network import SafeClient,FetchResult,NetworkRequestError,ScanError
from .reliability import Attempts,retry_after
from .runners import crawl
from .work import ScanWork,ScanPause
from .tasks import process_scan


def client():
    value=SafeClient(['example.com'],check_access=lambda:None,connect_timeout=10,read_timeout=30)
    value.reliable_retries=True;value.max_attempts=3
    return value


def response(status=200,headers=None,body=b'<title>Page</title>'):
    return FetchResult('https://example.com/',status,headers or {'content-type':'text/html'},body)


class ReliabilityTests(SimpleTestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        override=override_settings(MEDIA_ROOT=directory.name);override.enable();self.addCleanup(override.disable)
        self.now=1000
        clock=patch('scans.reliability.time.time',side_effect=lambda:self.now);clock.start();self.addCleanup(clock.stop)

    def run_attempts(self,value):
        with ScanWork('reliability') as work:
            for _ in range(10):
                try:
                    rows,_=crawl(value,url='https://example.com/',max_urls=1,max_depth=0,work=work)
                    return list(rows),list(work.connection.execute('SELECT ordinal,state,payload FROM attempts ORDER BY ordinal'))
                except ScanPause as pause:self.now+=pause.delay_seconds
            self.fail('Retry scheduler did not terminate')

    def test_two_timeouts_then_success_keep_all_attempts_and_one_page(self):
        value=client()
        failure=NetworkRequestError('read_timeout',{'stage':'connect_or_headers'})
        with patch.object(value,'fetch',side_effect=[failure,failure,response()]) as fetch:
            rows,attempts=self.run_attempts(value)
        self.assertEqual(fetch.call_count,3);self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['data']['attempt_count'],3)
        self.assertTrue(rows[0]['data']['recovered_after_retry'])
        self.assertEqual([a[1] for a in attempts],['error','error','response'])
        self.assertEqual([json.loads(a[2])['read_timeout'] for a in attempts],[30,30,30])

    def test_permanent_timeout_is_bounded(self):
        value=client()
        with patch.object(value,'fetch',side_effect=NetworkRequestError('read_timeout',{})) as fetch:
            rows,attempts=self.run_attempts(value)
        self.assertEqual(fetch.call_count,3);self.assertEqual(len(attempts),3)
        self.assertEqual(rows[0]['data']['error_code'],'read_timeout')

    def test_404_and_403_are_not_retried_as_transport_errors(self):
        for status in (404,403):
            with self.subTest(status=status):
                value=client()
                with ScanWork('status'+str(status)) as work,patch.object(value,'fetch',return_value=response(status)) as fetch:
                    rows,_=crawl(value,url='https://example.com/',max_urls=1,max_depth=0,work=work)
                    self.assertEqual(len(rows),1);self.assertEqual(fetch.call_count,1)

    def test_unsafe_dns_is_not_retried(self):
        value=client()
        with patch.object(value,'fetch',side_effect=NetworkRequestError('unsafe_dns',{})) as fetch:
            rows,attempts=self.run_attempts(value)
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(rows[0]['data']['error_code'],'unsafe_dns')

    def test_retry_after_seconds_and_http_date(self):
        self.assertEqual(retry_after('60',1000),60)
        self.assertEqual(retry_after(format_datetime(datetime.fromtimestamp(1060,tz=tz.utc),usegmt=True),1000),60)
        self.assertEqual(retry_after('invalid',1000),0)

    def test_429_defers_without_sleeping_then_resumes(self):
        value=client()
        with patch.object(value,'fetch',side_effect=[response(429,{'retry-after':'60'}),response()]):
            with ScanWork('throttle') as work:
                with self.assertRaises(ScanPause) as pause:crawl(value,url='https://example.com/',max_urls=1,max_depth=0,work=work)
                self.assertGreaterEqual(pause.exception.delay_seconds,60)
                self.assertEqual(len(work.records),0)
                self.now+=pause.exception.delay_seconds
                rows,_=crawl(value,url='https://example.com/',max_urls=1,max_depth=0,work=work)
                self.assertEqual(len(rows),1)

    def test_retry_beyond_network_budget_preserves_failure(self):
        value=client();value.deadline=time.monotonic()+1
        with patch.object(value,'fetch',side_effect=NetworkRequestError('read_timeout',{})) as fetch:
            rows,attempts=self.run_attempts(value)
        self.assertEqual(fetch.call_count,1);self.assertEqual(len(rows),1)

    def test_retained_response_is_reused_after_resume_without_network(self):
        value=client()
        with ScanWork('retained') as work:
            work.add('crawl','https://example.com/',0)
            first=Attempts(work,value)
            with patch.object(value,'fetch',return_value=response(headers={'content-type':'text/html','set-cookie':'secret'})):
                bundle=first.perform('https://example.com/',first.prepare('https://example.com/'))
            first.retain('https://example.com/',bundle)
        with ScanWork('retained') as work,patch.object(value,'fetch') as fetch:
            rows,_=crawl(value,url='https://example.com/',max_urls=1,max_depth=0,work=work)
            self.assertEqual(len(rows),1);fetch.assert_not_called()
            self.assertEqual(work.connection.execute('SELECT count(*) FROM attempts').fetchone()[0],1)

    def test_retained_robots_block_is_not_reclassified_as_transport_failure(self):
        from .network import RobotsDenied
        value=client();blocked=RobotsDenied('blocked');blocked.googlebot_allowed=False
        with ScanWork('robot-cache') as work:
            work.add('crawl','https://example.com/',0)
            manager=Attempts(work,value)
            with patch.object(value,'fetch',side_effect=blocked):bundle=manager.perform('https://example.com/',manager.prepare('https://example.com/'))
            manager.retain('https://example.com/',bundle)
        with ScanWork('robot-cache') as work,patch.object(value,'fetch') as fetch:
            records,_=crawl(value,url='https://example.com/',max_urls=1,max_depth=0,work=work)
            data=next(iter(records))['data'];self.assertTrue(data['robots_blocked']);self.assertFalse(data['googlebot_allowed'])
            fetch.assert_not_called()

    def test_interrupted_attempts_are_bounded_and_not_fabricated_as_success(self):
        value=client();value.max_attempts=1
        with ScanWork('reliability') as work:Attempts(work,value).prepare('https://example.com/')
        with patch.object(value,'fetch') as fetch:
            rows,attempts=self.run_attempts(value)
        fetch.assert_not_called();self.assertEqual(attempts[0][1],'interrupted')
        self.assertEqual(rows[0]['data']['error_code'],'retry_exhausted')

    def test_budget_expiring_between_tasks_preserves_last_download_failure(self):
        value=client()
        with patch.object(value,'fetch',side_effect=NetworkRequestError('read_timeout',{})) as fetch,ScanWork('budget') as work:
            with self.assertRaises(ScanPause) as pause:crawl(value,url='https://example.com/',max_urls=1,max_depth=0,work=work)
            self.now+=pause.exception.delay_seconds
            value.deadline=time.monotonic()-1
            rows,_=crawl(value,url='https://example.com/',max_urls=1,max_depth=0,work=work)
            self.assertEqual(len(rows),1);self.assertEqual(fetch.call_count,1)
            self.assertEqual(next(iter(rows))['data']['error_code'],'read_timeout')

    def test_parallel_overload_finishes_already_running_downloads_before_deferring(self):
        value=client();value.concurrency=4
        def download(url):
            if url.endswith('/a'):return FetchResult(url,429,{'retry-after':'60'},b'')
            if url.endswith('/b'):
                time.sleep(.02);value.guard();return FetchResult(url,200,{'content-type':'text/html'},b'B')
            return response(body=b'<a href="/a">A</a><a href="/b">B</a>')
        with patch.object(value,'fetch',side_effect=download),ScanWork('parallel-retry') as work:
            with self.assertRaises(ScanPause):crawl(value,url='https://example.com/',max_urls=3,max_depth=2,work=work)
            self.assertEqual({r['data']['url'] for r in work.records},{'https://example.com/','https://example.com/b'})


class RetryPublicationTests(TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        override=override_settings(MEDIA_ROOT=directory.name);override.enable();self.addCleanup(override.disable)
        self.user=get_user_model().objects.create_user('retry-admin')
        self.project=Project.objects.create(name='Retries',website='https://example.com/',created_by=self.user)
        Membership.objects.create(project=self.project,user=self.user,role='admin')
        self.target=ScanTarget.objects.create(project=self.project,created_by=self.user,name='Site',url=self.project.website,allowed_hosts=['example.com'])
        self.batch=ImportBatch.objects.create(project=self.project,created_by=self.user,scan_target=self.target,source='semscan',method='scan',label='Crawl',request_metadata={
            'scan':'crawl','url':self.target.url,'allowed_hosts':['example.com'],'reliability_version':'page-retry-v1','max_attempts':3,
            'connect_timeout':10,'read_timeout':30,'max_urls':1,'max_depth':0,'duration':600})
        self.client.force_login(self.user)

    def test_real_task_defers_and_publishes_history_then_one_final_record(self):
        now=[1000]
        value=client();value.documents=[];value.robot_cache={}
        def factory(*args,**kwargs):value.documents=kwargs['document_sink'];return value
        with patch('scans.reliability.time.time',side_effect=lambda:now[0]),patch('scans.tasks.SafeClient',side_effect=factory),patch.object(value,'fetch',side_effect=[NetworkRequestError('read_timeout',{}),response()]),patch('scans.tasks.process_scan.apply_async') as enqueue:
            process_scan(str(self.batch.pk))
            self.batch.refresh_from_db();self.assertEqual(self.batch.status,'processing')
            self.assertEqual(self.batch.crawl_attempts.count(),1);self.assertEqual(self.batch.records.count(),0)
            self.assertGreaterEqual(enqueue.call_args.kwargs['countdown'],2)
            now[0]+=enqueue.call_args.kwargs['countdown']
            process_scan(str(self.batch.pk))
        self.batch.refresh_from_db();self.assertEqual(self.batch.status,'complete',self.batch.error)
        self.assertEqual(self.batch.records.count(),1);self.assertEqual(self.batch.crawl_attempts.count(),2)
        self.assertEqual(self.batch.request_metadata['download_attempts']['recovered_urls'],1)
        self.assertEqual(self.batch.crawl_url_coverage.get().outcome,'success')
        for name in ('attempts','coverage'):
            self.assertEqual(self.client.get(reverse('projects:scans:'+name,args=[self.project.pk,self.batch.pk])).status_code,200)

    def parent(self):
        self.batch.status='incomplete';self.batch.save(update_fields=['status'])
        record=SourceRecord.objects.create(batch=self.batch,locator='failed:0',data={'url':'https://example.com/failed','error':'timeout','error_code':'read_timeout','depth':4,'measured_at':timezone.now().isoformat()})
        return record

    def test_followup_is_immutable_project_scoped_and_pending_idempotent(self):
        record=self.parent();original=dict(record.data)
        url=reverse('projects:scans:retry-downloads',args=[self.project.pk,self.batch.pk])
        with patch('scans.retry_views.enqueue_scan'),self.captureOnCommitCallbacks(execute=True):
            first=self.client.post(url,'{}',content_type='application/json')
            second=self.client.post(url,'{}',content_type='application/json')
        self.assertEqual(first.status_code,202,first.content);self.assertEqual(second.status_code,202)
        self.assertEqual(first.json()['id'],second.json()['id'])
        child=ImportBatch.objects.exclude(pk=self.batch.pk).get()
        self.assertEqual(child.retry_targets.get().snapshot['depth'],4)
        self.assertEqual(child.request_metadata['read_timeout'],30)
        record.refresh_from_db();self.assertEqual(record.data,original)

    def test_retry_rejects_viewer_cross_project_and_missing_csrf(self):
        self.parent();url=reverse('projects:scans:retry-downloads',args=[self.project.pk,self.batch.pk])
        viewer=get_user_model().objects.create_user('retry-viewer')
        Membership.objects.create(project=self.project,user=viewer,role='viewer');self.client.force_login(viewer)
        self.assertEqual(self.client.post(url,'{}',content_type='application/json').status_code,403)
        self.client.force_login(self.user)
        csrf=Client(enforce_csrf_checks=True);csrf.force_login(self.user)
        self.assertEqual(csrf.post(url,'{}',content_type='application/json').status_code,403)
        other=Project.objects.create(name='Other',website='https://other.com/',created_by=self.user)
        self.assertEqual(self.client.get(reverse('projects:scans:attempts',args=[other.pk,self.batch.pk])).status_code,404)

    def test_timeout_bounds_are_validated(self):
        self.parent();url=reverse('projects:scans:retry-downloads',args=[self.project.pk,self.batch.pk])
        self.assertEqual(self.client.post(url,json.dumps({'read_timeout':121}),content_type='application/json').status_code,400)

    def test_regular_historical_crawls_remain_selectable_in_dossier_form(self):
        from reporting.forms import DossierForm
        self.batch.status='complete';self.batch.save(update_fields=['status'])
        form=DossierForm(project=self.project)
        self.assertTrue(form.fields['crawl'].queryset.filter(pk=self.batch.pk).exists())
        self.batch.request_metadata['retry_only']=True;self.batch.save(update_fields=['request_metadata'])
        self.assertFalse(DossierForm(project=self.project).fields['crawl'].queryset.filter(pk=self.batch.pk).exists())

    def test_coverage_filters_not_visited_and_exports_frozen_inventory(self):
        for n,outcome in enumerate(['not_visited','success','download_error']):
            CrawlURLCoverage.objects.create(batch=self.batch,url='https://example.com/'+str(n),url_hash=str(n),in_sitemap=True,in_links=False,outcome=outcome)
        url=reverse('projects:scans:coverage',args=[self.project.pk,self.batch.pk])
        page=self.client.get(url,{'source':'sitemap_only','outcome':'not_visited'})
        self.assertContains(page,'https://example.com/0');self.assertNotContains(page,'https://example.com/1')
        response=self.client.get(url,{'outcome':'not_visited','download':'1'})
        output=b''.join(response.streaming_content).decode()
        self.assertIn('https://example.com/0',output);self.assertNotIn('https://example.com/1',output)

    def test_unvisited_sitemap_and_depth_excluded_link_are_in_inventory(self):
        self.batch.request_metadata.update(discover_sitemaps=True,max_urls=2,max_depth=0)
        self.batch.save(update_fields=['request_metadata'])
        value=client()
        def download(url,**kwargs):
            if url.endswith('robots.txt'):return FetchResult(url,200,{},b'Sitemap: https://example.com/map.xml')
            if url.endswith('map.xml'):return FetchResult(url,200,{},b'<urlset><url><loc>https://example.com/a</loc></url><url><loc>https://example.com/b</loc></url></urlset>')
            return FetchResult(url,200,{'content-type':'text/html'},b'<a href="/not-queued">Link</a>' if url=='https://example.com/' else b'page')
        def factory(*args,**kwargs):value.documents=kwargs['document_sink'];return value
        with patch('scans.tasks.SafeClient',side_effect=factory),patch.object(value,'fetch',side_effect=download):process_scan(str(self.batch.pk))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status,'incomplete',self.batch.error)
        inventory=self.batch.request_metadata['url_inventory']
        self.assertEqual(inventory,{'found':4,'queued':3,'attempted':2,'successful':2})
        self.assertFalse(self.batch.crawl_url_coverage.get(url='https://example.com/not-queued').queued)
        self.assertEqual(self.batch.crawl_url_coverage.get(url='https://example.com/b').outcome,'not_visited')

    def test_followup_task_preserves_original_and_does_not_expand_links(self):
        record=self.parent();original=dict(record.data)
        with patch('scans.retry_views.enqueue_scan'),self.captureOnCommitCallbacks(execute=True):
            result=self.client.post(reverse('projects:scans:retry-downloads',args=[self.project.pk,self.batch.pk]),'{}',content_type='application/json')
        self.assertEqual(result.status_code,202)
        child=ImportBatch.objects.exclude(pk=self.batch.pk).get()
        value=client()
        def factory(*args,**kwargs):value.documents=kwargs['document_sink'];return value
        with patch('scans.tasks.SafeClient',side_effect=factory),patch.object(value,'fetch',return_value=FetchResult('https://example.com/failed',200,{'content-type':'text/html'},b'<a href="/extra">Extra</a>')) as fetch:
            process_scan(str(child.pk))
        child.refresh_from_db();record.refresh_from_db()
        self.assertEqual(child.status,'complete',child.error);self.assertEqual(fetch.call_count,1)
        self.assertEqual(record.data,original)
        self.assertEqual(child.records.get().data['depth'],4)
        self.assertEqual(child.request_metadata['download_attempts']['recovered_followup_urls'],1)
        from reporting.services import create_dossier
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):create_dossier(self.project,self.user,child.pk)
