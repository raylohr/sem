from datetime import datetime, time, timedelta, timezone as utc_timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase, TestCase, Client
from django.urls import reverse
from django.utils import timezone

from projects.models import Project, Membership
from sources.models import CrawlPageArtifact
from .models import PlaywrightCrawlSettings, PlaywrightCrawl, PlaywrightDomainBudget
from .crawl_service import start_crawl, control_crawl, add_urls, acquire_slot, access, CrawlPaused
from .crawl_schedule import next_occurrence, schedule_playwright_crawls
from .crawl_tasks import process_crawl_url, recover_crawls
from .crawl_content import pack_content
from .crawl_renderer import PageCapture
from .playwright_views import save_configuration


class CalendarTests(SimpleTestCase):
    def config(self,**values):
        return SimpleNamespace(schedule_frequency='daily',schedule_time=time(3),schedule_weekday=0,**values)

    def test_daily_weekly_and_dst_use_amsterdam_wall_clock(self):
        zone=ZoneInfo('Europe/Amsterdam')
        config=self.config()
        before=datetime(2027,3,27,4,tzinfo=zone)
        result=next_occurrence(config,before)
        self.assertEqual((result.day,result.hour,result.utcoffset()),(28,3,timedelta(hours=2)))
        config.schedule_frequency='weekly'
        config.schedule_weekday=0
        result=next_occurrence(config,before)
        self.assertEqual((result.weekday(),result.day,result.hour),(0,29,3))

    def test_nonexistent_time_moves_forward_and_autumn_time_runs_only_once(self):
        zone=ZoneInfo('Europe/Amsterdam')
        config=self.config()
        config.schedule_time=time(2,30)
        result=next_occurrence(config,datetime(2027,3,28,1,tzinfo=zone))
        self.assertEqual((result.hour,result.minute),(3,30))
        result=next_occurrence(config,datetime(2026,10,25,2,45,tzinfo=zone,fold=0))
        self.assertEqual(result.day,26)


class CrawlControlTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user('control-owner')
        self.reader=get_user_model().objects.create_user('control-reader')
        self.other=get_user_model().objects.create_user('control-other')
        self.project=Project.objects.create(name='Controls',website='https://example.test/',created_by=self.owner,include_subdomains=True)
        Membership.objects.create(project=self.project,user=self.owner,role='admin')
        Membership.objects.create(project=self.project,user=self.reader,role='viewer')
        self.config=PlaywrightCrawlSettings.objects.create(project=self.project,use_sitemaps=False,max_retries=0)
        self.page=reverse('ui:playwright-settings',args=[self.project.pk])
        self.action=reverse('projects:connections:playwright-action',args=[self.project.pk])
        self.status=reverse('projects:connections:playwright-status',args=[self.project.pk])
        self.client.force_login(self.owner)

    def start(self,running=True):
        with patch('integrations.crawl_tasks.enqueue_bootstrap'):
            crawl=start_crawl(actor=self.owner,project_id=self.project.pk)
        if running:
            crawl.status='running'
            crawl.started_at=timezone.now()
            crawl.save()
        return crawl

    def control(self,crawl,action):
        return control_crawl(actor=self.owner,project_id=self.project.pk,crawl_id=crawl.pk,action=action)

    def test_default_migration_updates_baselines_and_preserves_enabled_schedules(self):
        from django.apps import apps
        from importlib import import_module
        self.config.max_pages=10000
        self.config.max_depth=10
        self.config.max_duration_minutes=720
        self.config.schedule_frequency='daily'
        self.config.schedule_time=time(3)
        self.config.save()
        other=Project.objects.create(name='Custom',website='https://custom.test/',created_by=self.owner)
        custom=PlaywrightCrawlSettings.objects.create(project=other,max_pages=500,max_depth=7,
            max_duration_minutes=900,schedule_enabled=True,schedule_frequency='daily',schedule_time=time(3))
        migrate=import_module('integrations.migrations.0011_refresh_playwright_defaults').refresh_defaults
        migrate(apps,SimpleNamespace(connection=SimpleNamespace(alias='default')))
        self.config.refresh_from_db()
        custom.refresh_from_db()
        self.assertEqual((self.config.max_pages,self.config.max_depth,self.config.max_duration_minutes),(100000,99,1400))
        self.assertEqual((self.config.schedule_frequency,self.config.schedule_time),('weekly',time(20)))
        self.assertEqual((custom.max_pages,custom.max_depth,custom.max_duration_minutes),(500,7,900))
        self.assertEqual((custom.schedule_frequency,custom.schedule_time),('daily',time(3)))

    def test_remaining_work_and_eta_account_for_pause_and_external_checks(self):
        from .playwright_views import crawl_context
        crawl=self.start()
        now=timezone.now()
        crawl.started_at=now-timedelta(minutes=10)
        crawl.paused_seconds=300
        crawl.save()
        add_urls(crawl.pk,[f'https://example.test/{index}' for index in range(10)])
        completed=list(crawl.urls.values_list('pk',flat=True)[:5])
        crawl.urls.filter(pk__in=completed).update(status='complete')
        add_urls(crawl.pk,['https://external.test/a','https://external.test/b'],kind='external')
        with patch('integrations.playwright_views.timezone.now',return_value=now):
            context=crawl_context(self.project)
        self.assertEqual(context['crawl_pages_remaining'],5)
        self.assertEqual(context['crawl_eta_minutes'],7)
        self.control(crawl,'pause')
        self.assertIsNone(crawl_context(self.project)['crawl_eta_minutes'])
        self.assertContains(self.client.get(self.status),'pagina’s te gaan')
        self.assertContains(self.client.get(self.status),'Geschatte resterende tijd')

    def test_eta_waits_for_progress_instead_of_showing_false_precision(self):
        from .playwright_views import crawl_context
        crawl=self.start()
        add_urls(crawl.pk,['https://example.test/a'])
        context=crawl_context(self.project)
        self.assertEqual(context['crawl_pages_remaining'],1)
        self.assertIsNone(context['crawl_eta_minutes'])

    def test_ui_start_pause_resume_stop_and_partial_publication(self):
        response=self.client.get(self.page)
        self.assertContains(response,'Crawl starten')
        with patch('integrations.crawl_tasks.enqueue_bootstrap'):
            self.assertEqual(self.client.post(self.action,{'action':'start'}).status_code,302)
        crawl=PlaywrightCrawl.objects.get()
        self.client.post(self.action,{'action':'pause','crawl_id':crawl.pk})
        crawl.refresh_from_db()
        self.assertEqual(crawl.status,'paused')
        crawl.batch.refresh_from_db()
        self.assertEqual(crawl.batch.status,'processing')
        self.assertContains(self.client.get(self.status),'Crawl hervatten')
        with patch('integrations.crawl_tasks.enqueue_bootstrap'):
            self.client.post(self.action,{'action':'resume','crawl_id':crawl.pk})
        crawl.refresh_from_db()
        self.assertEqual(crawl.status,'pending')
        self.client.post(self.action,{'action':'stop','crawl_id':crawl.pk})
        crawl.batch.refresh_from_db()
        self.assertEqual(crawl.batch.status,'incomplete')
        self.assertContains(self.client.get(self.page),'Bekijk bronimport en resultaten')

    def test_pause_invalidates_old_tasks_preserves_work_and_excludes_pause_from_deadline(self):
        crawl=self.start()
        add_urls(crawl.pk,['https://example.test/a'])
        row=crawl.urls.get()
        row.status='queued'
        row.task_token=uuid4()
        row.save()
        old_token=row.task_token
        self.assertEqual(acquire_slot(row.pk,old_token),0)
        paused=self.control(crawl,'pause')
        with self.assertRaises(CrawlPaused):
            access(crawl.pk)
        row.refresh_from_db()
        self.assertEqual((row.status,row.attempts,row.task_token),('pending',0,None))
        with self.assertRaises(ValidationError):
            self.start()
        with patch('integrations.crawl_tasks.call_renderer') as renderer:
            process_crawl_url(row.pk,str(old_token))
            renderer.assert_not_called()
        tomorrow=timezone.now()+timedelta(days=2)
        with patch('integrations.crawl_service.timezone.now',return_value=tomorrow),patch('integrations.crawl_tasks.enqueue_url'):
            self.control(crawl,'resume')
            current=access(crawl.pk)
        self.assertGreaterEqual(current.paused_seconds,172799)
        self.assertEqual(current.status,'running')
        self.assertFalse(CrawlPageArtifact.objects.exists())

    def test_pause_while_renderer_is_running_discards_stale_result(self):
        crawl=self.start()
        add_urls(crawl.pk,['https://example.test/a'])
        row=crawl.urls.get()
        row.status='queued'
        row.task_token=uuid4()
        row.save()
        capture=PageCapture(url=row.url,render_complete=True,http_status=200,
            server_html=b'<html>A</html>',rendered_html='<html>B</html>',text='B')
        def pause_inflight(*args):
            self.control(crawl,'pause')
            return {**capture.metadata(),'artifacts':pack_content(capture)}
        with patch('integrations.crawl_tasks.call_renderer',side_effect=pause_inflight):
            process_crawl_url(row.pk,str(row.task_token))
        crawl.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual((crawl.status,row.status),('paused','pending'))
        self.assertFalse(crawl.batch.records.exists())
        self.assertFalse(CrawlPageArtifact.objects.exists())

    def test_pause_before_first_execution_does_not_extend_later_runtime(self):
        crawl=self.start(running=False)
        self.control(crawl,'pause')
        tomorrow=timezone.now()+timedelta(days=2)
        with patch('integrations.crawl_service.timezone.now',return_value=tomorrow),patch('integrations.crawl_tasks.enqueue_bootstrap'):
            self.control(crawl,'resume')
        crawl.refresh_from_db()
        self.assertEqual(crawl.paused_seconds,0)

    def test_revocation_stops_paused_run_and_controls_are_project_scoped(self):
        crawl=self.start()
        self.control(crawl,'pause')
        Membership.objects.filter(project=self.project,user=self.owner).update(role='viewer')
        with self.assertRaises(PermissionDenied):
            self.control(crawl,'resume')
        recover_crawls()
        crawl.refresh_from_db()
        self.assertEqual(crawl.status,'cancelled')
        self.client.force_login(self.reader)
        self.assertEqual(self.client.post(self.action,{'action':'start'}).status_code,403)
        self.assertNotContains(self.client.get(self.status),'name="action"')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.status).status_code,404)
        self.assertEqual(self.client.post(self.action,{'action':'start'}).status_code,404)

    def test_state_changes_require_post_and_csrf_and_get_does_not_create_run(self):
        self.assertEqual(self.client.get(self.action).status_code,405)
        self.client.get(self.status)
        self.assertFalse(PlaywrightCrawl.objects.exists())
        client=Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post(self.action,{'action':'start'}).status_code,403)

    def due(self):
        self.config.schedule_enabled=True
        self.config.schedule_owner=self.owner
        self.config.next_run_at=timezone.now()-timedelta(minutes=2)
        self.config.save()

    @patch('integrations.crawl_tasks.enqueue_bootstrap')
    def test_scheduler_claims_once_and_uses_saved_configuration(self,enqueue):
        self.due()
        with self.captureOnCommitCallbacks(execute=True):
            schedule_playwright_crawls()
            schedule_playwright_crawls()
        self.assertEqual(PlaywrightCrawl.objects.count(),1)
        self.assertEqual(enqueue.call_count,1)
        run=PlaywrightCrawl.objects.get()
        self.assertEqual(run.batch.request_metadata['trigger'],'schedule')
        self.assertEqual(run.configuration['parallel_agents'],1)
        self.config.refresh_from_db()
        self.assertGreater(self.config.next_run_at,timezone.now())
        self.assertIsNotNone(self.config.last_scheduled_at)

    def test_scheduler_skips_paused_run_and_disables_revoked_owner(self):
        crawl=self.start()
        self.control(crawl,'pause')
        self.due()
        schedule_playwright_crawls()
        self.assertEqual(PlaywrightCrawl.objects.count(),1)
        self.config.refresh_from_db()
        self.assertIn('overgeslagen',self.config.schedule_note)
        self.due()
        Membership.objects.filter(project=self.project,user=self.owner).update(role='viewer')
        schedule_playwright_crawls()
        self.config.refresh_from_db()
        self.assertFalse(self.config.schedule_enabled)
        self.assertIsNone(self.config.next_run_at)

    def test_form_enables_future_schedule_without_immediate_crawl_and_disables_it(self):
        data={'include_subdomains':'on','desktop_profile':'random','pages_per_second':'1',
            'parallel_agents':'1','slow_after_seconds':'10','slow_pages_per_second':'.25',
            'schedule_enabled':'on','schedule_frequency':'weekly','schedule_time':'04:15','schedule_weekday':'3'}
        with patch('celery.app.task.Task.apply_async') as enqueue:
            saved,form=save_configuration(actor=self.owner,project_id=self.project.pk,data=data)
        self.assertTrue(saved,form.errors)
        self.assertFalse(PlaywrightCrawl.objects.exists())
        enqueue.assert_not_called()
        self.assertEqual(saved.next_run_at.astimezone(ZoneInfo('Europe/Amsterdam')).weekday(),3)
        self.assertContains(self.client.get(self.page),'Donderdag')
        previous=saved.next_run_at
        saved,form=save_configuration(actor=self.owner,project_id=self.project.pk,data=data)
        self.assertEqual(saved.next_run_at,previous)
        data.pop('schedule_enabled')
        saved,form=save_configuration(actor=self.owner,project_id=self.project.pk,data=data)
        self.assertFalse(saved.schedule_enabled)
        self.assertIsNone(saved.next_run_at)
        self.assertIsNone(saved.schedule_owner)
