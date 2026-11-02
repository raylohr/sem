import json
import tempfile
from pathlib import Path
from django.test import SimpleTestCase,TestCase,override_settings
from django.utils import timezone
from .work import ScanWork,work_paths
from .telemetry import read_checkpoint,snapshot,estimate_remaining
from . import tests as scan_tests
from django.urls import reverse
from .models import ScanDocument


class TelemetryTests(SimpleTestCase):
    def test_reader_works_during_writer_lock_and_preserves_status_buckets(self):
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory):
            with ScanWork('live') as work:
                work.add('crawl','https://example.com/',0)
                work.add('crawl','https://example.com/',0)
                item=work.next('crawl')
                for status in [200,201,404,404]:
                    work.documents.append({'url':'https://example.com/','status':status,'fetched_at':timezone.now(),'body':b'raw','content_type':'text/html'})
                work.finish(item[0],{'locator':'one','data':{'error':'timeout','measured_at':timezone.now().isoformat()}})
                live=read_checkpoint('live')
                self.assertEqual(live['records'],1)
                self.assertEqual(live['pending'],0)
                self.assertEqual(live['errors'],1)
                self.assertEqual(live['totals'],{'download_error':1,'200':1,'201':1,'404':2})
                self.assertTrue(all(point['time']%3==0 for point in live['series']))
                self.assertEqual(sum(point['count'] for point in live['series']),5)
            with ScanWork('live') as work:
                self.assertEqual(snapshot(work.connection)['totals'],live['totals'])

    def test_legacy_checkpoint_is_upgraded_without_double_counting(self):
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory):
            with ScanWork('legacy') as work:
                work.records.append({'locator':'one','data':{'error':'network','measured_at':timezone.now().isoformat()}})
                work.documents.append({'url':'https://example.com/','status':404,'fetched_at':timezone.now(),'body':b'raw','content_type':'text/html'})
                for table in ['live_counts','live_series','live_totals']:work.connection.execute('DROP TABLE '+table)
                work.connection.commit()
            with ScanWork('legacy') as work:
                live=snapshot(work.connection)
                self.assertEqual(live['records'],1)
                self.assertEqual(live['errors'],1)
                self.assertEqual(live['documents'],1)
                self.assertEqual(live['totals'],{'download_error':1,'404':1})

    def test_eta_uses_queue_processed_and_elapsed_time(self):
        timing=estimate_remaining(records=260,pending=1976,started_at=1000,now=1130,active=True,max_urls=100000)
        self.assertEqual(timing,{'elapsed_seconds':130,'urls_per_second':2,'eta_seconds':988})
        timing=estimate_remaining(records=260,pending=1976,started_at=1000,now=1260,active=True,max_urls=100000)
        self.assertEqual(timing['eta_seconds'],1976)

    def test_eta_handles_startup_limits_and_finished_scans(self):
        for count,start,now in [(0,1000,1100),(1,None,1100),(1,1200,1100)]:
            self.assertIsNone(estimate_remaining(records=count,pending=100,started_at=start,now=now,active=True)['eta_seconds'])
        self.assertIsNone(estimate_remaining(records=10,pending=100,started_at=1000,now=1100,active=False)['eta_seconds'])
        self.assertEqual(estimate_remaining(records=10,pending=100,started_at=1000,now=1100,active=True,max_urls=20)['eta_seconds'],100)
        self.assertEqual(estimate_remaining(records=10,pending=0,started_at=1000,now=1100,active=True)['eta_seconds'],0)

    def test_reader_never_creates_a_missing_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory):
            self.assertIsNone(read_checkpoint('missing'))
            self.assertFalse((Path(directory)/work_paths('missing')[0]).exists())


class ProgressAccessTests(TestCase):
    setUp = scan_tests.ScanAccessTests.setUp
    make_batch = scan_tests.ScanAccessTests.make_batch
    def test_live_progress_is_scoped_and_not_cached(self):
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory):
            batch=self.make_batch()
            url=reverse('projects:scans:progress',args=[self.project.pk,batch.pk])
            with ScanWork(batch.pk) as work:
                work.documents.append({'url':self.target.url,'status':201,'fetched_at':timezone.now(),'body':b'created','content_type':'text/html'})
                response=self.client.get(url)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['totals'],{'201':1})
                self.assertEqual(response.json()['poll_seconds'],3)
                self.assertIsNotNone(response.json()['elapsed_seconds'])
                self.assertIsNone(response.json()['eta_seconds'])
                self.assertIn('no-store',response.headers['Cache-Control'])
                self.client.force_login(self.viewer)
                self.assertEqual(self.client.get(url).status_code,200)
                self.client.force_login(self.other)
                self.assertEqual(self.client.get(url).status_code,404)
                self.client.logout()
                self.assertEqual(self.client.get(url).status_code,302)

    def test_completed_progress_uses_persisted_rollup_after_cleanup(self):
        batch=self.make_batch();batch.status='incomplete'
        batch.request_metadata['scan_progress']={'live':{'records':42,'documents':41,'errors':1,'pending':0,'updated_at':timezone.now().timestamp(),'end':int(timezone.now().timestamp()),'series':[],'totals':{'200':41,'download_error':1}}};batch.save()
        response=self.client.get(reverse('projects:scans:progress',args=[self.project.pk,batch.pk]))
        self.assertFalse(response.json()['active'])
        self.assertEqual(response.json()['records'],42)
        self.assertEqual(response.json()['totals']['200'],41)

    def test_endpoint_eta_uses_worker_start_and_skips_sitemap_units(self):
        import time
        batch=self.make_batch();batch.request_metadata['max_urls']=10000;batch.save()
        with ScanWork(batch.pk) as work:
            work.set('started',time.time()-120)
            for i in range(3): work.add('crawl',f'https://example.com/{i}',0)
            work.finish(work.next('crawl')[0],{'locator':'one','data':{'url':'https://example.com/0','status':200}})
            url=reverse('projects:scans:progress',args=[self.project.pk,batch.pk])
            data=self.client.get(url).json()
            self.assertEqual(data['records'],1)
            self.assertEqual(data['pending'],2)
            self.assertGreaterEqual(data['eta_seconds'],240)
            self.assertLess(data['eta_seconds'],250)
            batch.request_metadata['scan']='sitemap';batch.save()
            self.assertIsNone(self.client.get(url).json()['eta_seconds'])
