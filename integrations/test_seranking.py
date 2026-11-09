import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from projects.models import Project, Membership
from sources.models import ImportBatch
from sources.url_inventory import project_inventory
from .models import SERankingConnection
from .seranking import ProjectClient, SERankingError, fetch_report, matching_projects, BASE_URL
from .seranking_tasks import process_seranking


@override_settings(SERANKING_API_KEY='private-key')
class ProjectClientTests(SimpleTestCase):
    def response(self, payload, status=200):
        response = MagicMock(status_code=status)
        response.iter_content.return_value = [json.dumps(payload).encode()]
        response.__enter__.return_value = response
        return response

    @patch('integrations.seranking.requests.Session')
    def test_only_allowlisted_project_routes_can_send_key(self, session_class):
        session = session_class.return_value.__enter__.return_value
        session.get.return_value = self.response([])
        client = ProjectClient()
        for blocked in ('https://evil.test/', '//evil.test', '../data', 'sites/../data',
                        'https://api.seranking.com/v1/keywords', 'subscription', 'data',
                        'audits/recheck', 'sites', 'rankings?token=secret'):
            with self.subTest(blocked=blocked), self.assertRaises(SERankingError):
                client.get(blocked)
        for params in ({'token': 'other'}, {'url': 'https://evil.test'}, {'site_id': '../data'}):
            with self.subTest(params=params), self.assertRaises(SERankingError):
                client.get('summary', **params)
        session.get.assert_not_called()
        self.assertEqual(client.get('summary', site_id=123), [])
        args, kwargs = session.get.call_args
        self.assertEqual(args[0], BASE_URL + 'sites/summary')
        self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(kwargs['headers']['Authorization'], 'Token private-key')
        self.assertNotIn('token', kwargs['params'])
        self.assertFalse(session.trust_env)

    @patch('integrations.seranking.requests.Session')
    def test_redirects_and_provider_errors_are_safe(self, session_class):
        session = session_class.return_value.__enter__.return_value
        for status in (301, 302, 307, 401, 403, 429, 500):
            with self.subTest(status=status):
                session.get.return_value = self.response({'error': 'private-key'}, status)
                with self.assertRaises(SERankingError) as error:
                    ProjectClient().get('projects')
                self.assertNotIn('private-key', str(error.exception))
                self.assertFalse(session.get.call_args.kwargs['allow_redirects'])

    @override_settings(SERANKING_API_KEY='')
    @patch('integrations.seranking.requests.Session')
    def test_missing_key_does_not_open_connection(self, session):
        with self.assertRaises(SERankingError):
            ProjectClient().get('projects')
        session.assert_not_called()

    @patch('integrations.seranking.requests.Session')
    def test_limits_and_malformed_payloads(self, session_class):
        session = session_class.return_value.__enter__.return_value
        for payload in ('no token', {'error': 'private-key'}, {'errors': ['private-key']}):
            session.get.return_value = self.response(payload)
            with self.assertRaises(SERankingError):
                ProjectClient().get('projects')
        session.get.return_value = self.response([{'id': 1}])
        with patch('integrations.seranking.MAX_RESPONSE_BYTES', 1), self.assertRaises(SERankingError):
            ProjectClient().get('projects')

    def test_pagination_uses_offsets_and_guards(self):
        client = ProjectClient()
        client.get = MagicMock(side_effect=[{'items': [{'id': 1}, {'id': 2}], 'total': 3},
                                           {'items': [{'id': 3}], 'total': 3}])
        guard = MagicMock()
        with patch('integrations.seranking.PAGE_SIZE', 2):
            rows = client.pages('audit_pages', audit_id=1, check_access=guard)
        self.assertEqual(len(rows), 3)
        self.assertEqual(client.get.call_args_list[1].kwargs['offset'], 2)
        self.assertEqual(guard.call_count, 2)

    def test_incomplete_pagination_never_publishes_partial_data(self):
        client = ProjectClient()
        client.get = MagicMock(side_effect=[{'items': [{'id': 1}], 'total': 2}, {'items': [], 'total': 2}])
        with self.assertRaises(SERankingError):
            client.pages('audit_pages', audit_id=1)

    def test_project_discovery_filters_unrelated_hosts(self):
        client = MagicMock()
        client.get.return_value = [
            {'id': 1, 'name': 'example.test'}, {'id': 2, 'name': 'https://www.example.test/'},
            {'id': 3, 'name': 'example.test.evil.test'}, {'id': 4, 'name': 'other.test'}]
        project = SimpleNamespace(domain='example.test', website='https://example.test', include_subdomains=False)
        self.assertEqual([row['id'] for row in matching_projects(client, project)], [1, 2])

    @patch('integrations.seranking.verify_connection')
    def test_rankings_keep_actual_url_and_date_separate_from_target(self, verify):
        client = MagicMock()
        client.get.side_effect = [[{'id': '10', 'name': 'query', 'link': 'https://example.test/target'}],
            [{'site_engine_id': 5, 'keywords': [{'id': '10', 'volume': 100,
                'positions': [{'date': '2026-11-01', 'pos': 4}, {'date': '2026-11-02', 'pos': 7}],
                'landing_pages': [{'date': '2026-11-01', 'url': 'https://example.test/actual'}]}]}]]
        batch = SimpleNamespace(seranking_connection=SimpleNamespace(site_id=1), request_metadata={'type': 'rankings'},
                                period_start=date(2026, 11, 1), period_end=date(2026, 11, 2))
        records, _ = fetch_report(client, batch, lambda: None)
        self.assertEqual(records[0]['data']['url'], 'https://example.test/actual')
        self.assertEqual(records[0]['data']['target_url'], 'https://example.test/target')
        self.assertEqual(records[0]['data']['date'], '2026-11-01')
        self.assertIsNone(records[1]['data']['url'])
        self.assertEqual(records[1]['data']['position'], 7)

    @patch('integrations.seranking.verify_connection')
    def test_audit_must_belong_to_connected_site_and_be_finished(self, verify):
        client = MagicMock()
        client.pages.return_value = [{'id': 9, 'site_id': 999, 'status': 'finished'}]
        batch = SimpleNamespace(seranking_connection=SimpleNamespace(site_id=1, site_name='example.test'),
                                request_metadata={'type': 'audit_pages', 'audit_id': 9})
        with self.assertRaises(SERankingError):
            fetch_report(client, batch, lambda: None)
        self.assertEqual(client.pages.call_count, 1)


@override_settings(SERANKING_API_KEY='private-key')
class ProjectIntegrationTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('se-owner')
        self.editor = get_user_model().objects.create_user('se-editor')
        self.viewer = get_user_model().objects.create_user('se-viewer')
        self.project = Project.objects.create(name='SE', website='https://example.test/', created_by=self.owner)
        for user, role in ((self.owner, 'admin'), (self.editor, 'editor'), (self.viewer, 'viewer')):
            Membership.objects.create(project=self.project, user=user, role=role)
        self.connection = SERankingConnection.objects.create(project=self.project, owner=self.owner,
                                                            site_id=123, site_name='example.test')
        self.client.force_login(self.owner)

    def url(self, name, connection=True):
        args = [self.project.pk] + ([self.connection.pk] if connection else [])
        return reverse('projects:connections:seranking-' + name, args=args)

    def batch(self, report='rankings'):
        return ImportBatch.objects.create(project=self.project, created_by=self.editor, source='seranking', method='api',
            label='SE Ranking', seranking_connection=self.connection, period_start='2026-11-01', period_end='2026-11-02',
            request_metadata={'type': report, 'record_scope': 'summary' if report == 'summary' else 'url'})

    def test_viewers_cannot_import_and_editors_cannot_discover_or_connect(self):
        for user, actions in ((self.viewer, ('import', 'audits', 'disconnect', 'discover', 'connect')),
                              (self.editor, ('disconnect', 'discover', 'connect'))):
            self.client.force_login(user)
            for action in actions:
                with self.subTest(user=user.username, action=action), patch('integrations.seranking.ProjectClient.get') as get:
                    response = self.client.post(self.url(action, action not in ('connect', 'discover')))
                    self.assertEqual(response.status_code, 403)
                    get.assert_not_called()

    @patch('integrations.seranking_views.matching_projects')
    def test_cannot_bind_arbitrary_remote_site(self, sites):
        sites.return_value = [{'id': 123, 'name': 'example.test', 'title': 'site'}]
        self.assertEqual(self.client.post(self.url('connect', False), {'site_id': '999'}).status_code, 400)
        self.assertEqual(SERankingConnection.objects.count(), 1)

    @patch('integrations.seranking_views.enqueue_seranking')
    def test_import_validates_dates_and_report_and_queues_after_commit(self, enqueue):
        self.client.force_login(self.editor)
        for data in ({'report': 'data'}, {'report': 'rankings'},
                     {'report': 'rankings', 'start': '2026-01-01', 'end': '2026-03-01'},
                     {'report': 'audit_pages'}):
            self.assertEqual(self.client.post(self.url('import'), data).status_code, 400)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url('import'), {'report': 'summary'})
        self.assertEqual(response.status_code, 202)
        batch = ImportBatch.objects.get()
        self.assertEqual(batch.request_metadata['api_scope'], 'project-only')
        self.assertEqual(batch.request_metadata['record_scope'], 'summary')
        enqueue.assert_called_once_with(batch.pk)
        self.assertNotIn('private-key', response.content.decode())

    @patch('integrations.seranking_tasks.fetch_report')
    def test_worker_host_filter_and_duplicate_delivery(self, fetch):
        batch = self.batch()
        fetch.return_value = ([{'locator': str(i), 'data': row} for i, row in enumerate([
            {'url': 'https://example.test/actual', 'date': '2026-11-01', 'position': 3},
            {'url': 'https://evil.test/', 'position': 1}, {'url': None, 'keyword': 'unranked'}])], [])
        process_seranking(str(batch.pk))
        process_seranking(str(batch.pk))
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'complete')
        self.assertEqual(batch.record_count, 2)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(batch.request_metadata['host_filter']['excluded_rows'], 1)
        _, inventory = project_inventory(self.project)
        self.assertEqual([row['url'] for row in inventory], ['https://example.test/actual'])

    @patch('integrations.seranking_tasks.fetch_report')
    def test_project_report_is_preserved_but_not_added_to_inventory(self, fetch):
        batch = self.batch('summary')
        fetch.return_value = ([{'locator': '1', 'data': {'url': 'https://example.test/', 'visibility': 42}}], [])
        process_seranking(str(batch.pk))
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'complete')
        self.assertEqual(batch.records.count(), 1)
        self.assertEqual(project_inventory(self.project)[1], [])

    @patch('integrations.seranking_tasks.fetch_report')
    def test_disconnect_and_owner_revocation_block_worker_before_network(self, fetch):
        for disconnect in (True, False):
            self.connection.active = not disconnect
            self.connection.save()
            if not disconnect:
                Membership.objects.filter(project=self.project, user=self.owner).update(role='viewer')
            batch = self.batch()
            process_seranking(str(batch.pk))
            batch.refresh_from_db()
            self.assertNotEqual(batch.status, 'complete')
            self.assertEqual(batch.records.count(), 0)
        fetch.assert_not_called()

    @patch('integrations.seranking_tasks.fetch_report')
    def test_failure_is_safe_and_retry_uses_seranking_queue(self, fetch):
        batch = self.batch()
        fetch.side_effect = RuntimeError('private-key')
        process_seranking(str(batch.pk))
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'failed')
        self.assertNotIn('private-key', batch.error)
        with patch('integrations.seranking_tasks.enqueue_seranking') as enqueue, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('projects:sources:retry', args=[self.project.pk, batch.pk]))
        self.assertEqual(response.status_code, 202)
        enqueue.assert_called_once_with(batch.pk)

    def test_connection_ui_and_batch_retry_render(self):
        response = self.client.get(reverse('ui:connections', args=[self.project.pk]))
        self.assertContains(response, 'SE Ranking-project zoeken')
        self.assertContains(response, 'Project API')
        self.assertNotContains(response, 'private-key')
        batch = self.batch()
        response = self.client.get(reverse('ui:batch', args=[self.project.pk, batch.pk]))
        self.assertContains(response, 'Opnieuw starten')

    @patch('integrations.seranking_tasks.fetch_report')
    def test_revocation_during_fetch_prevents_publication(self, fetch):
        batch = self.batch()
        def revoke(*args):
            SERankingConnection.objects.filter(pk=self.connection.pk).update(active=False)
            return [{'locator': '1', 'data': {'url': 'https://example.test/'}}], []
        fetch.side_effect = revoke
        process_seranking(str(batch.pk))
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'failed')
        self.assertFalse(batch.records.exists())

    def test_foreign_project_cannot_use_connection(self):
        other = Project.objects.create(name='Other', website='https://other.test/', created_by=self.editor)
        Membership.objects.create(project=other, user=self.editor, role='admin')
        self.client.force_login(self.editor)
        url = reverse('projects:connections:seranking-import', args=[other.pk, self.connection.pk])
        self.assertEqual(self.client.post(url, {'report': 'summary'}).status_code, 404)
        self.assertFalse(ImportBatch.objects.exists())

    @patch('integrations.seranking_tasks.fetch_report')
    def test_report_browsing_and_url_detail_include_source_data(self, fetch):
        batch = self.batch('audit_pages')
        fetch.return_value = ([{'locator': str(i), 'data': {'url': f'https://example.test/page-{i}',
                              'title': f'Page {i}', 'status': '200'}} for i in range(55)], [])
        process_seranking(str(batch.pk))
        response = self.client.get(reverse('ui:batch', args=[self.project.pk, batch.pk]), {'page': '2'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['url_page']), 5)
        response = self.client.get(reverse('ui:batch', args=[self.project.pk, batch.pk]), {'q': 'page-54'})
        self.assertEqual(response.context['url_page'].paginator.count, 1)
        response = self.client.get(reverse('ui:url-detail', args=[self.project.pk]), {'url': 'https://example.test/page-54'})
        self.assertContains(response, 'SE Ranking')
        self.assertContains(response, 'Page 54')
        _, rows = project_inventory(self.project)
        self.assertEqual(next(row for row in rows if row['url'].endswith('page-54'))['title'], 'Page 54')

    def test_project_deletion_removes_protected_connections(self):
        from projects.services import delete_project
        self.owner.is_superuser = True
        self.owner.save()
        self.owner.refresh_from_db()
        self.batch()
        delete_project(actor=self.owner, project_id=self.project.pk, confirmation_name=self.project.name)
        self.assertFalse(SERankingConnection.objects.exists())
        self.assertFalse(ImportBatch.objects.exists())

    @override_settings(LANGUAGE_CODE='nl', USE_THOUSAND_SEPARATOR=True)
    @patch('integrations.seranking_views.matching_projects')
    def test_localized_discovery_form_roundtrips_project_id(self, sites):
        from html.parser import HTMLParser
        from django.utils.translation import override
        class Inputs(HTMLParser):
            def __init__(self):
                super().__init__()
                self.values = {}
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'input' and attrs.get('name'):
                    self.values[attrs['name']] = attrs.get('value', '')
        sites.return_value = [{'id': 13176785, 'name': 'example.test', 'title': 'Example'}]
        with override('nl'):
            response = self.client.post(self.url('discover', False))
            parser = Inputs()
            parser.feed(response.content.decode())
            self.assertEqual(parser.values['site_id'], '13176785')
            self.assertTrue(response.context['can_manage'])
            self.assertEqual(response.context['role'], 'admin')
            response = self.client.post(self.url('connect', False), parser.values)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(SERankingConnection.objects.filter(project=self.project, site_id=13176785, active=True).exists())

    @override_settings(LANGUAGE_CODE='nl', USE_THOUSAND_SEPARATOR=True)
    @patch('integrations.seranking_views.verify_connection')
    @patch('integrations.seranking_views.ProjectClient')
    def test_localized_audit_form_keeps_id_unformatted(self, client, verify):
        from django.utils.translation import override
        client.return_value.pages.return_value = [{'id': 700138456, 'site_id': 123, 'status': 'finished', 'title': 'Audit'}]
        with override('nl'):
            response = self.client.post(self.url('audits'))
        self.assertContains(response, 'name="audit_id" value="700138456"')
        self.assertTrue(response.context['can_edit'])
        self.assertEqual(response.context['role'], 'admin')
