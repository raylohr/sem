import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase,TestCase,override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from projects.models import Project,Membership
from sources.models import ImportBatch,PendingFileDeletion
from browser_scans.models import BrowserRun,BrowserVisit
from . import activity
from .models import ScanTarget
from .network import SafeClient,FetchResult,ScanError
from .work import ScanWork


class ActivityTests(SimpleTestCase):
    def test_inflight_host_and_elapsed_are_visible_before_response_then_outcome_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory):
            with patch('scans.activity.time.time',return_value=1000):
                identifier=activity.start('run','external','https://linkedin.com/path?secret=never-store')
            live=activity.read('run',now=1012)
            self.assertEqual(live['requests'][0]['host'],'linkedin.com')
            self.assertEqual(live['requests'][0]['waiting_seconds'],12)
            self.assertNotIn('secret',str(live))
            with patch('scans.activity.time.time',return_value=1013):activity.finish('run','external',identifier,status=999)
            live=activity.read('run',now=1013)
            self.assertFalse(live['requests']);self.assertEqual(live['series'][0]['outcome'],'unknown')
            self.assertEqual(live['totals'][0]['status'],'999')

    def test_phase_eta_uses_own_start_and_survives_checkpoint_cleanup(self):
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory):
            with ScanWork('run') as work:
                with patch('scans.activity.time.time',return_value=1000):activity.phase('run','external',done=0,total=100)
                with patch('scans.activity.time.time',return_value=1010):activity.phase('run','external',done=20,total=100)
                work.clean=True
            live=activity.read('run',now=1020)
            self.assertEqual(live['phases'][0]['eta_seconds'],80)
            self.assertFalse(work.paths[0].exists())

    def test_retry_is_visible_and_old_history_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory):
            activity.phase('run','pages',total=2)
            now=time.time();activity.retry('run','pages','https://example.com/private?token=x',now+20)
            live=activity.read('run',now=now+5)
            self.assertEqual(live['requests'][0]['state'],'retrying')
            self.assertEqual(live['requests'][0]['retry_in_seconds'],15)
            self.assertEqual(live['totals'][0]['outcome'],'retrying')
            identifier=activity.start('run','pages','https://example.com/')
            activity.finish('run','pages',identifier,status=200)
            self.assertFalse(activity.read('run')['requests'])
            self.assertFalse(activity.read('run',now=now+601)['series'])

    def test_transport_records_failures_without_masking_them(self):
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory):
            client=SafeClient(['example.com'],batch_id='run',check_access=lambda:None,progress_phase='files')
            try:
                with patch.object(client,'_request_impl',side_effect=ScanError('failure')):
                    with self.assertRaises(ScanError):client._request('https://example.com/file.js',0)
                self.assertEqual(activity.read('run')['totals'][0]['outcome'],'unknown')
                self.assertFalse(activity.read('run')['requests'])
            finally:client.close()

    def test_read_does_not_create_files_and_late_finish_does_not_recreate_deleted_data(self):
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory):
            self.assertIsNone(activity.read('run'))
            activity.finish('run','pages','nonexistent',status=200)
            self.assertFalse((Path(directory)/activity.paths('run')[0]).exists())


class WorkflowProgressTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('workflow-owner')
        self.project=Project.objects.create(name='Workflow',website='https://example.com/',created_by=self.user)
        Membership.objects.create(project=self.project,user=self.user,role='admin')
        self.target=ScanTarget.objects.create(project=self.project,created_by=self.user,name='Site',url=self.project.website,allowed_hosts=['example.com'])
        self.batch=ImportBatch.objects.create(project=self.project,created_by=self.user,scan_target=self.target,source='semscan',method='scan',status='processing',request_metadata={'scan':'crawl','check_resources':True,'check_external_links':True})
        self.client.force_login(self.user)
        self.tmp=tempfile.TemporaryDirectory();self.settings=override_settings(MEDIA_ROOT=self.tmp.name);self.settings.enable()
        self.addCleanup(self.tmp.cleanup);self.addCleanup(self.settings.disable)

    def get(self,batch=None):return self.client.get(reverse('projects:scans:progress',args=[self.project.pk,(batch or self.batch).pk])).json()

    def test_external_activity_visible_without_checkpoint_publication_and_page_queue_empty(self):
        activity.phase(self.batch.pk,'pages',status='complete',done=364,total=364)
        activity.phase(self.batch.pk,'files',status='complete',done=653,total=653)
        activity.phase(self.batch.pk,'external',done=8,total=191)
        identifier=activity.start(self.batch.pk,'external','https://linkedin.com/secret?token=x')
        data=self.get();w=data['workflow']
        self.assertEqual(w['current_phase'],'external');self.assertTrue(w['active'])
        self.assertEqual(w['requests'][0]['host'],'linkedin.com')
        self.assertNotIn('secret',str(w))
        activity.finish(self.batch.pk,'external',identifier,status=403)
        self.assertEqual(self.get()['workflow']['series'][0]['outcome'],'blocked')

    def test_root_stays_live_during_child_browser_even_after_crawl_complete(self):
        self.batch.status='complete';self.batch.save()
        child=ImportBatch.objects.create(project=self.project,created_by=self.user,scan_target=self.target,source='semscan',method='scan',status='processing',request_metadata={'scan':'browser'})
        run=BrowserRun.objects.create(batch=child,source_batch=self.batch)
        BrowserVisit.objects.create(run=run,url='https://example.com/',url_hash='root',status='processing',coverage={'progress_started_at':time.time()-12})
        activity.phase(self.batch.pk,'analysis',status='complete',done=364,total=364)
        activity.phase(child.pk,'browser',done=0,total=1)
        identifier=activity.start(child.pk,'browser','https://fonts.gstatic.com/a.woff')
        activity.finish(child.pk,'browser',identifier,status=200)
        w=self.get()['workflow'];self.assertTrue(w['active']);self.assertEqual(w['current_phase'],'browser')
        self.assertTrue(any(s['phase']=='browser' for s in w['series']))
        child.status='complete';child.save();run.visits.update(status='complete')
        activity.phase(child.pk,'browser',status='complete',done=1,total=1)
        self.assertFalse(self.get()['workflow']['active'])

    def test_analysis_is_visible_before_its_database_transaction_publishes(self):
        activity.phase(self.batch.pk,'pages',status='complete',done=10,total=10)
        activity.phase(self.batch.pk,'files',status='complete',done=0,total=0)
        activity.phase(self.batch.pk,'external',status='complete',done=0,total=0)
        activity.phase(self.batch.pk,'analysis',done=5,total=10)
        self.assertEqual(self.get()['workflow']['current_phase'],'analysis')

    def test_activity_storage_is_queued_for_deletion_with_source(self):
        from sources.services import delete_batch
        activity.phase(self.batch.pk,'pages',done=1,total=2)
        delete_batch(batch_id=self.batch.pk,project=self.project,actor=self.user)
        self.assertTrue(PendingFileDeletion.objects.filter(path=activity.paths(self.batch.pk)[0]).exists())

    def test_browser_analysis_keeps_polling_until_database_finalization(self):
        self.batch.status='complete';self.batch.save()
        activity.phase(self.batch.pk,'analysis',status='complete',done=1,total=1)
        child=ImportBatch.objects.create(project=self.project,created_by=self.user,scan_target=self.target,source='semscan',method='scan',status='processing',request_metadata={'scan':'browser'})
        run=BrowserRun.objects.create(batch=child,source_batch=self.batch)
        BrowserVisit.objects.create(run=run,url='https://example.com/',url_hash='root',status='complete')
        activity.phase(child.pk,'browser',status='complete',done=1,total=1)
        activity.phase(child.pk,'browser_analysis',status='complete',done=1,total=1)
        w=self.get()['workflow'];self.assertTrue(w['active']);self.assertEqual(w['current_phase'],'browser_analysis')

    def test_failed_scan_cannot_show_perpetual_inflight_activity(self):
        activity.start(self.batch.pk,'external','https://example.com/')
        self.batch.status='failed';self.batch.save()
        w=self.get()['workflow']
        self.assertFalse(w['active']);self.assertFalse(w['requests']);self.assertEqual(w['status_label'],'Onderzoek gestopt')

    def test_policy_block_is_not_a_fabricated_http_403(self):
        identifier=activity.start(self.batch.pk,'files','https://blocked.example.com/file.js')
        activity.finish(self.batch.pk,'files',identifier,outcome_override='blocked')
        live=activity.read(self.batch.pk)
        self.assertEqual(live['totals'][0]['status'],'blocked')
        self.assertEqual(live['totals'][0]['outcome'],'blocked')

    def test_finished_sitemap_does_not_wait_for_a_nonexistent_page_analysis(self):
        self.batch.status='complete';self.batch.request_metadata={'scan':'sitemap'};self.batch.save()
        w=self.get()['workflow']
        self.assertFalse(w['active'])
        self.assertFalse(any(p['name']=='analysis' for p in w['phases']))

    def test_resumed_phase_eta_does_not_treat_old_work_as_new_throughput(self):
        with patch('scans.activity.time.time',return_value=1000):activity.phase(self.batch.pk,'external',done=100,total=200)
        with patch('scans.activity.time.time',return_value=1010):activity.phase(self.batch.pk,'external',done=110,total=200)
        row=activity.read(self.batch.pk,now=1020)['phases'][0]
        self.assertEqual(row['eta_seconds'],180)

    def test_external_queue_is_not_shown_running_before_file_phase_finishes(self):
        activity.phase(self.batch.pk,'pages',status='complete',done=364,total=364)
        activity.phase(self.batch.pk,'files',done=10,total=653)
        with ScanWork(self.batch.pk) as work:
            work.set('page_phase_done',True)
            work.set('resource_progress',{'outcomes':{'resource':{'success':10,'queued':643},'external':{'queued':191}}})
            phases={p['name']:p for p in self.get()['workflow']['phases']}
            self.assertEqual(phases['external']['status'],'queued')
            self.assertEqual(phases['external']['total'],191)
