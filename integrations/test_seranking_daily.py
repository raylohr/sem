from datetime import date, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from projects.models import Project, Membership
from sources.models import ImportBatch, InventoryState
from .models import SERankingConnection, SERankingRefresh
from .seranking_refresh import run_daily_refresh, set_daily_schedule
from .seranking_tasks import schedule_daily_seranking, process_seranking_refresh


@override_settings(SERANKING_API_KEY='test-only')
class DailyRefreshTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('daily-owner')
        self.viewer = get_user_model().objects.create_user('daily-viewer')
        self.project = Project.objects.create(name='Daily', website='https://example.test/', created_by=self.owner)
        Membership.objects.create(project=self.project, user=self.owner, role='admin')
        Membership.objects.create(project=self.project, user=self.viewer, role='viewer')
        self.connection = SERankingConnection.objects.create(project=self.project, owner=self.owner,
            site_id=123, site_name='example.test', daily_enabled=True)
        self.client.force_login(self.owner)
        self.url = reverse('projects:connections:seranking-schedule', args=[self.project.pk, self.connection.pk])

    def refresh(self, day=date(2026, 11, 10)):
        return SERankingRefresh.objects.create(connection=self.connection, check_date=day)

    def mocks(self):
        from contextlib import ExitStack
        stack = ExitStack()
        client = stack.enter_context(patch('integrations.seranking_refresh.ProjectClient'))
        stack.enter_context(patch('integrations.seranking_refresh.verify_connection'))
        stack.enter_context(patch('integrations.seranking_refresh.site_audits', return_value=[]))
        fetch = stack.enter_context(patch('integrations.seranking_tasks.fetch_report'))
        client.return_value.get.return_value = {'first':'2026-11-07', 'current':'2026-11-10'}
        fetch.return_value = ([{'locator':'1', 'data':{'url':'https://example.test/', 'value':42}}], [])
        self.addCleanup(stack.close)
        return client, fetch

    @patch('integrations.seranking_tasks.enqueue_seranking_refresh')
    def test_switch_enables_check_immediately_and_disables_without_deleting_imports(self, enqueue):
        self.connection.daily_enabled = False
        self.connection.save()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, {'enabled':'1'})
        self.assertEqual(response.status_code, 200)
        self.connection.refresh_from_db()
        self.assertTrue(self.connection.daily_enabled)
        refresh = SERankingRefresh.objects.get()
        enqueue.assert_called_once_with(refresh.pk)
        batch = ImportBatch.objects.create(project=self.project, created_by=self.owner, source='seranking',
            method='api', label='Keep', seranking_connection=self.connection, status='complete')
        self.assertEqual(self.client.post(self.url, {}).status_code, 200)
        self.connection.refresh_from_db()
        refresh.refresh_from_db()
        self.assertFalse(self.connection.daily_enabled)
        self.assertEqual(refresh.status, 'cancelled')
        self.assertTrue(ImportBatch.objects.filter(pk=batch.pk, status='complete').exists())

    def test_viewer_cannot_change_switch(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(self.url, {'enabled':'0'}).status_code, 403)
        self.connection.refresh_from_db()
        self.assertTrue(self.connection.daily_enabled)
        self.assertFalse(SERankingRefresh.objects.exists())

    @patch('integrations.seranking_tasks.enqueue_seranking_refresh')
    def test_scheduler_is_daily_and_never_creates_jobs_for_disabled_connections(self, enqueue):
        with patch('integrations.seranking_tasks.timezone.localtime', return_value=timezone.make_aware(datetime(2026,11,10,7))):
            schedule_daily_seranking()
        self.assertFalse(SERankingRefresh.objects.exists())
        with patch('integrations.seranking_tasks.timezone.localtime', return_value=timezone.make_aware(datetime(2026,11,10,8))), self.captureOnCommitCallbacks(execute=True):
            schedule_daily_seranking()
            schedule_daily_seranking()
        self.assertEqual(SERankingRefresh.objects.count(), 1)
        SERankingRefresh.objects.update(status='complete')
        enqueue.reset_mock()
        with patch('integrations.seranking_tasks.timezone.localtime', return_value=timezone.make_aware(datetime(2026,11,10,12))), self.captureOnCommitCallbacks(execute=True):
            schedule_daily_seranking()
        enqueue.assert_not_called()
        self.connection.daily_enabled = False
        self.connection.save()
        with patch('integrations.seranking_tasks.timezone.localtime', return_value=timezone.make_aware(datetime(2026,11,11,8))):
            schedule_daily_seranking()
        self.assertEqual(SERankingRefresh.objects.count(), 1)

    def test_unchanged_reports_create_no_additional_imports_or_inventory_rebuild(self):
        client, fetch = self.mocks()
        first = self.refresh()
        run_daily_refresh(str(first.pk))
        first.refresh_from_db()
        self.assertEqual(first.status, 'complete', first.results)
        self.assertEqual(ImportBatch.objects.filter(status='complete').count(), 8)
        self.assertTrue(all(result['status'] == 'updated' for key,result in first.results.items() if key != 'audit_pages'))
        revision = InventoryState.objects.get(project=self.project).revision
        second = self.refresh(date(2026,11,11))
        run_daily_refresh(str(second.pk))
        second.refresh_from_db()
        self.assertEqual(second.status, 'complete', second.results)
        self.assertTrue(all(result['status'] == 'unchanged' for result in second.results.values()))
        self.assertEqual(ImportBatch.objects.count(), 8)
        self.assertEqual(InventoryState.objects.get(project=self.project).revision, revision)
        # Duplicate task delivery also does nothing.
        count = fetch.call_count
        process_seranking_refresh(str(second.pk))
        self.assertEqual(fetch.call_count, count)

    def test_changed_report_is_imported_but_old_history_is_retained(self):
        client, fetch = self.mocks()
        run_daily_refresh(str(self.refresh().pk))
        original_ids = set(ImportBatch.objects.values_list('pk', flat=True))
        def changed(client, batch, guard):
            value = 43 if batch.request_metadata['type'] == 'keywords' else 42
            return [{'locator':'1', 'data':{'url':'https://example.test/', 'value':value}}], []
        fetch.side_effect = changed
        second = self.refresh(date(2026,11,11))
        run_daily_refresh(str(second.pk))
        second.refresh_from_db()
        self.assertEqual(second.results['keywords']['status'], 'updated')
        self.assertEqual(second.results['summary']['status'], 'unchanged')
        self.assertEqual(ImportBatch.objects.count(), 9)
        self.assertTrue(original_ids <= set(ImportBatch.objects.values_list('pk', flat=True)))

    def test_unavailable_report_is_visible_without_daily_failed_import_clutter(self):
        from .seranking import SERankingError
        client, fetch = self.mocks()
        def unavailable(client, batch, guard):
            if batch.request_metadata['type'] == 'gsc_queries':
                raise SERankingError('GSC-rapport niet beschikbaar.')
            return [{'locator':'1','data':{'url':'https://example.test/', 'value':42}}], []
        fetch.side_effect = unavailable
        refresh = self.refresh()
        run_daily_refresh(str(refresh.pk))
        refresh.refresh_from_db()
        self.assertEqual(refresh.status, 'partial')
        self.assertEqual(refresh.results['gsc_queries']['status'], 'unavailable')
        self.assertFalse(ImportBatch.objects.filter(status='failed').exists())
        self.assertEqual(ImportBatch.objects.filter(status='complete').count(), 7)

    def test_opt_out_during_fetch_prevents_publication(self):
        client, fetch = self.mocks()
        def disable(client, batch, guard):
            set_daily_schedule(actor=self.owner, project_id=self.project.pk, connection_id=self.connection.pk, enabled=False)
            return [{'locator':'1','data':{'url':'https://example.test/'}}], []
        fetch.side_effect = disable
        refresh = self.refresh()
        run_daily_refresh(str(refresh.pk))
        refresh.refresh_from_db()
        self.assertEqual(refresh.status, 'cancelled')
        self.assertFalse(ImportBatch.objects.filter(status='complete').exists())
        self.assertFalse(ImportBatch.objects.filter(records__isnull=False).exists())

    def test_revoked_owner_prevents_any_api_call(self):
        client, fetch = self.mocks()
        Membership.objects.filter(project=self.project,user=self.owner).update(role='viewer')
        refresh = self.refresh()
        run_daily_refresh(str(refresh.pk))
        refresh.refresh_from_db()
        self.assertEqual(refresh.status, 'cancelled')
        client.assert_not_called()
        fetch.assert_not_called()

    def test_disconnect_cancels_scheduled_updates(self):
        refresh = self.refresh()
        url = reverse('projects:connections:seranking-disconnect',args=[self.project.pk,self.connection.pk])
        self.assertEqual(self.client.post(url).status_code, 302)
        self.connection.refresh_from_db()
        refresh.refresh_from_db()
        self.assertFalse(self.connection.daily_enabled)
        self.assertEqual(refresh.status, 'cancelled')

    def test_switch_and_check_results_are_rendered(self):
        refresh = self.refresh()
        refresh.status='partial'
        refresh.finished_at=timezone.now()
        refresh.results={'gsc_queries': {'status':'unavailable','message':'GSC-rapport niet beschikbaar.'}}
        refresh.save()
        response=self.client.get(reverse('ui:connections', args=[self.project.pk]))
        self.assertContains(response, 'role="switch"')
        self.assertContains(response, 'Dagelijks automatisch bijwerken')
        self.assertContains(response, 'Gedeeltelijk bijgewerkt')
        self.assertContains(response, 'GSC-rapport niet beschikbaar.')

    def test_new_ranking_date_is_imported_without_redownloading_prior_period(self):
        client, fetch = self.mocks()
        client.return_value.get.return_value = {'first':'2026-11-07', 'current':'2026-11-09'}
        run_daily_refresh(str(self.refresh(date(2026,11,9)).pk))
        client.return_value.get.return_value = {'first':'2026-11-07', 'current':'2026-11-10'}
        latest = self.refresh()
        run_daily_refresh(str(latest.pk))
        latest.refresh_from_db()
        self.assertEqual(latest.results['rankings']['status'], 'updated')
        periods = list(ImportBatch.objects.filter(request_metadata__type='rankings').order_by('period_start').values_list('period_start','period_end'))
        self.assertEqual(periods, [(date(2026,11,7),date(2026,11,9)), (date(2026,11,10),date(2026,11,10))])

    def test_new_audit_date_is_imported_once(self):
        client, fetch = self.mocks()
        with patch('integrations.seranking_refresh.site_audits', return_value=[{'id':9,'last_update':'2026-11-07'}]):
            first = self.refresh(date(2026,11,9))
            run_daily_refresh(str(first.pk))
        self.assertEqual(ImportBatch.objects.filter(request_metadata__type='audit_pages').count(), 1)
        with patch('integrations.seranking_refresh.site_audits', return_value=[{'id':9,'last_update':'2026-11-08'}]):
            # Changed crawl metadata and changed page content constitute a new observation.
            fetch.return_value = ([{'locator':'1','data':{'url':'https://example.test/','value':43}}], [])
            second = self.refresh()
            run_daily_refresh(str(second.pk))
            second.refresh_from_db()
            self.assertEqual(second.results['audit_pages']['status'], 'updated')
            self.assertEqual(ImportBatch.objects.filter(request_metadata__type='audit_pages').count(), 2)
            third = self.refresh(date(2026,11,11))
            run_daily_refresh(str(third.pk))
            third.refresh_from_db()
            self.assertEqual(third.results['audit_pages']['status'], 'unchanged')
            self.assertEqual(ImportBatch.objects.filter(request_metadata__type='audit_pages').count(), 2)

    def test_report_row_order_does_not_create_duplicate_imports(self):
        client, fetch = self.mocks()
        rows = [{'locator':'1','data':{'url':'https://example.test/a','value':1}},
                {'locator':'2','data':{'url':'https://example.test/b','value':2}}]
        fetch.return_value = (rows, [])
        run_daily_refresh(str(self.refresh().pk))
        fetch.return_value = (list(reversed(rows)), [])
        latest = self.refresh(date(2026,11,11))
        run_daily_refresh(str(latest.pk))
        latest.refresh_from_db()
        self.assertEqual(ImportBatch.objects.count(), 8)
        self.assertTrue(all(result['status'] == 'unchanged' for result in latest.results.values()))
