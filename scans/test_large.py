import datetime
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from django.contrib.auth import get_user_model
from projects.models import Project,Membership
from sources.models import ImportBatch,PendingFileDeletion
from sources.services import delete_batch
from sources.tasks import cleanup_files
from .models import ScanTarget
from .network import SafeClient,FetchResult,ScanBudgetExceeded
from .runners import crawl,sitemap
from .tasks import process_scan
from .work import ScanWork,ScanPause,ScanBusy,work_paths,stale_scan


class CheckpointTests(SimpleTestCase):
    def test_crawl_resumes_without_duplicate_records(self):
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory):
            client=MagicMock();client.allowed_hosts={'example.com'}
            def fetch(url):
                number=int(url.rsplit('/',1)[-1] or 0)
                if number==3: raise ScanPause
                return FetchResult(url,200,{'content-type':'text/html'},f'<a href="/{number+1}">next</a>'.encode())
            client.fetch.side_effect=fetch
            with ScanWork('resume') as work:
                with self.assertRaises(ScanPause):
                    crawl(client,url='https://example.com/0',max_urls=6,max_depth=100,work=work)
                self.assertEqual(len(work.records),3)
            client.fetch.side_effect=lambda url:FetchResult(url,200,{'content-type':'text/html'},f'<a href="/{int(url.rsplit("/",1)[-1])+1}">next</a>'.encode())
            with ScanWork('resume') as work:
                records,_=crawl(client,url='https://example.com/0',max_urls=6,max_depth=100,work=work)
                self.assertEqual([row['data']['url'] for row in records],[f'https://example.com/{i}' for i in range(6)])
                self.assertEqual(len({row['locator'] for row in records}),6)
                work.clean=True
            self.assertFalse((Path(directory)/work_paths('resume')[0]).exists())

    def test_sitemap_continuation_deduplicates_urls(self):
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory):
            client=MagicMock();client.allowed_hosts={'example.com'}
            body=('<urlset>'+''.join(f'<url><loc>https://example.com/{i}</loc></url>' for i in range(400))+'</urlset>').encode()
            client.fetch.return_value=FetchResult('https://example.com/sitemap.xml',200,{},body)
            client.guard.side_effect=[None,ScanPause]
            with ScanWork('sitemap') as work:
                with self.assertRaises(ScanPause):
                    sitemap(client,url='https://example.com/sitemap.xml',max_urls=100000,max_depth=20,work=work)
                self.assertEqual(len(work.records),100)
            client.guard.side_effect=None
            with ScanWork('sitemap') as work:
                rows,_=sitemap(client,url='https://example.com/sitemap.xml',max_urls=100000,max_depth=20,work=work)
                self.assertEqual(len(rows),400)
                self.assertEqual(len({row['locator'] for row in rows}),400)

    def test_raw_document_sink_is_disk_backed_and_lossless(self):
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory):
            with ScanWork('documents') as work:
                work.documents.append({'url':'https://example.com','status':200,'content_type':'text/html','body':b'x'*100000,'fetched_at':timezone.now()})
            with ScanWork('documents') as work:
                self.assertEqual(len(work.documents),1)
                self.assertEqual(next(iter(work.documents))['body'],b'x'*100000)
                with self.assertRaises(ScanBusy):
                    with ScanWork('documents'): pass

    def test_oversized_analysis_is_bounded_while_raw_documents_remain_available(self):
        from .large import bounded_metadata
        import json
        fields={'titles':['x'*2000000],'outlinks':['https://example.com/'+'x'*10000]*2000,'text':'x'*100000}
        bounded=bounded_metadata(fields)
        self.assertTrue(bounded['analysis_truncated'])
        self.assertLess(len(json.dumps(bounded).encode()),1024**2)

    def test_slice_deadline_is_not_a_scan_failure(self):
        client=SafeClient(['example.com'],check_access=lambda:None,slice_seconds=1)
        client.slice_deadline=time.monotonic()-1
        with self.assertRaises(ScanPause):client.guard()
        client.deadline=time.monotonic()-1
        with self.assertRaises(ScanBudgetExceeded):client.guard()

    def test_resumed_robots_policy_does_not_need_another_download(self):
        cache={'https://example.com':{'status':200,'body':'User-agent: *\nDisallow: /blocked\n'}}
        client=SafeClient(['example.com'],check_access=lambda:None,robot_cache=cache)
        with patch.object(client,'_request') as request:
            from .network import RobotsDenied
            with self.assertRaises(RobotsDenied):client.fetch('https://example.com/blocked')
            request.assert_not_called()


class ContinuationTests(TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        override=override_settings(MEDIA_ROOT=self.directory.name);override.enable();self.addCleanup(override.disable)
        self.user=get_user_model().objects.create_user('continuation-user')
        self.project=Project.objects.create(name='Continuation',website='https://example.com',created_by=self.user)
        Membership.objects.create(project=self.project,user=self.user,role='admin')
        self.target=ScanTarget.objects.create(project=self.project,created_by=self.user,name='Site',url='https://example.com',allowed_hosts=['example.com'])
        self.batch=ImportBatch.objects.create(project=self.project,created_by=self.user,scan_target=self.target,source='crawler',method='scan',label='Large',request_metadata={'scan':'crawl','url':'https://example.com/0','max_urls':6,'max_depth':100,'duration':600,'allowed_hosts':['example.com']})

    def test_pause_schedules_continuation_and_publishes_only_at_completion(self):
        def partial(client,*,work,**kwargs):
            work.records.append({'locator':'crawl:0','data':{'url':'https://example.com/0'}})
            raise ScanPause
        with patch('scans.tasks.SafeClient'),patch('scans.tasks.crawl',side_effect=partial),patch('scans.tasks.process_scan.apply_async') as enqueue:
            process_scan(str(self.batch.pk));enqueue.assert_called_once()
        self.batch.refresh_from_db();self.assertEqual(self.batch.status,'processing')
        self.assertEqual(self.batch.request_metadata['scan_progress']['records'],1)
        self.assertEqual(self.batch.records.count(),0)
        def complete(client,*,work,**kwargs):return work.records,[]
        with patch('scans.tasks.SafeClient'),patch('scans.tasks.crawl',side_effect=complete):
            process_scan(str(self.batch.pk))
        self.batch.refresh_from_db();self.assertEqual(self.batch.status,'complete')
        self.assertEqual(self.batch.record_count,1)
        self.assertFalse((Path(self.directory.name)/work_paths(self.batch.pk)[0]).exists())

    def test_continuation_broker_failure_keeps_resumable_checkpoint(self):
        with patch('scans.tasks.SafeClient'),patch('scans.tasks.crawl',side_effect=ScanPause),patch('scans.tasks.process_scan.apply_async',side_effect=ConnectionError):
            process_scan(str(self.batch.pk))
        self.batch.refresh_from_db();self.assertEqual(self.batch.status,'failed')
        self.assertTrue((Path(self.directory.name)/work_paths(self.batch.pk)[0]).exists())

    def test_batch_deletion_queues_checkpoint_cleanup(self):
        with ScanWork(self.batch.pk) as work:work.records.append({'locator':'one','data':{}})
        with self.captureOnCommitCallbacks(execute=False):
            delete_batch(batch_id=self.batch.pk,project=self.project,actor=self.user)
        self.assertTrue(PendingFileDeletion.objects.filter(path=work_paths(self.batch.pk)[0]).exists())
        cleanup_files()
        self.assertFalse((Path(self.directory.name)/work_paths(self.batch.pk)[0]).exists())

    def test_stale_processing_scan_can_be_resumed_but_live_one_cannot(self):
        self.batch.status='processing'
        self.batch.request_metadata['scan_progress']={'heartbeat':timezone.now().isoformat()}
        self.batch.save()
        self.assertFalse(stale_scan(self.batch))
        from django.urls import reverse
        self.client.force_login(self.user)
        url=reverse('projects:sources:retry',args=[self.project.pk,self.batch.pk])
        self.assertEqual(self.client.post(url).status_code,409)
        self.batch.request_metadata['scan_progress']['heartbeat']=(timezone.now()-datetime.timedelta(minutes=16)).isoformat();self.batch.save()
        with patch('scans.tasks.enqueue_scan'):
            self.assertEqual(self.client.post(url).status_code,202)
