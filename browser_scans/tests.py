import base64
import copy
import hashlib
import io
import json
from types import SimpleNamespace
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from projects.models import Project, Membership
from scans.models import ScanTarget
from scans.network import SafeClient, FetchResult, ScanError, RobotsDenied
from sources.models import ImportBatch
from sources.services import publish_records, delete_batch
from assessments.models import AnalysisRun, Finding
from .forms import BrowserForm
from .models import BrowserRun, BrowserVisit, BrowserArtifact
from .services import create_run, store_visit, finalize
from .tasks import process_browser
from .protocol import send, receive

PNG = b'\x89PNG\r\n\x1a\nfixture'


def response(url='https://example.com/'):
    return {'type': 'result', 'final_url': url,
        'server_response': {'url': url, 'status': 200, 'headers': {'content-type': 'text/html'},
            'body': base64.b64encode(b'<html lang="nl"><title>Server</title><main><h1>Before</h1></main></html>').decode(), 'googlebot_allowed': True},
        'dom': base64.b64encode('<html lang="nl"><title>Gerenderd</title><main><h1>Na JavaScript</h1><a href="/js-only" rel="nofollow">Nieuw</a></main></html>'.encode()).decode(),
        'screenshot': base64.b64encode(PNG).decode(), 'metrics': {'load_ms': 250, 'fcp_ms': 100, 'lcp_ms': 200, 'cls': 0},
        'resources': [{'url': url, 'resource_type': 'document', 'status': 200, 'bytes': 100, 'fetch_ms': 10},
            {'url': 'https://example.com/broken.png', 'resource_type': 'image', 'status': 404, 'bytes': 0, 'fetch_ms': 2}],
        'accessibility': {'version': '4.14.0', 'violations': [{'id': 'image-alt', 'impact': 'serious', 'help': 'Images need alternatives', 'nodes': [{'target': ['img'], 'html': '<img src="x">'}]}], 'incomplete': []},
        'errors': [], 'blocked': [], 'timed_out': False, 'browser_version': 'fixture',
        'viewport': {'width': 1440, 'height': 900}, 'duration_ms': 300, 'axe_sha256': 'a'*64,
        'images': [], 'image_count': 0, 'transport': {'policy': 'fixture'}}


class ProtocolTests(SimpleTestCase):
    def test_roundtrip_and_partial_writes(self):
        class Partial(io.BytesIO):
            def write(self, value):
                return super().write(value[:13])
        stream = Partial()
        value = {'type': 'large', 'body': 'a'*10000}
        send(stream, value)
        stream.seek(0)
        self.assertEqual(receive(stream), value)

    def test_rejects_invalid_frame(self):
        for value in (b'', b'[]\n', b'{"x":1}'):
            with self.assertRaises(ValueError):
                receive(io.BytesIO(value))

    def test_forms_reject_private_hosts_and_unbounded_profiles(self):
        for values in ({'resource_hosts': '127.0.0.1'}, {'resource_hosts': '*.example.com'}, {'max_pages': 101}, {'timeout_seconds': 61}, {'profile': 'unbounded'}):
            self.assertFalse(BrowserForm(values).is_valid(), values)
        inherited = BrowserForm({'requests_per_second':999,'concurrency':999},initial={'requests_per_second':7,'concurrency':3})
        self.assertTrue(inherited.is_valid())
        self.assertEqual(inherited.cleaned_data['requests_per_second'],7)
        self.assertEqual(inherited.cleaned_data['concurrency'],3)
        form = BrowserForm({'resource_hosts': 'cdn.example.com,cdn.example.com'})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['resource_hosts'], ['cdn.example.com'])

    def test_one_hop_transport_preserves_redirect_for_browser_and_checks_dns(self):
        client = SafeClient(['example.com'], check_access=lambda: None, respect_robots=False, delay=0.5)
        with patch.object(client, '_robot_policy', return_value=(SimpleNamespace(can_fetch=lambda agent, url: True, sem_available=True), 0)), patch.object(client, '_request', return_value=FetchResult('https://example.com/',302,{'location':'http://127.0.0.1/secret'},b'')) as request:
            result = client.fetch_once('https://example.com/')
            self.assertEqual(result.status, 302)
            request.assert_called_once()
        with self.assertRaises(ScanError):
            client.fetch_once('http://127.0.0.1/secret')


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}, 'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}})
class BrowserTests(TestCase):
    def setUp(self):
        user = get_user_model()
        self.owner = user.objects.create_user('browser-owner')
        self.viewer = user.objects.create_user('browser-viewer')
        self.other = user.objects.create_user('browser-other')
        self.project = Project.objects.create(name='Browser project', website='https://example.com/', created_by=self.owner)
        Membership.objects.create(project=self.project, user=self.owner, role='admin')
        Membership.objects.create(project=self.project, user=self.viewer, role='viewer')
        self.target = ScanTarget.objects.create(project=self.project, created_by=self.owner, name='Own', url='https://example.com/', allowed_hosts=['example.com'])
        self.source = ImportBatch.objects.create(project=self.project, created_by=self.owner, scan_target=self.target, source='semscan', method='scan', label='HTML crawl', request_metadata={'scan':'crawl', 'allowed_hosts':['example.com'], 'respect_robots':False})
        publish_records(batch_id=self.source.pk, records=[{'locator':'page:0','data':{'url':'https://example.com/','status':200,'content_type':'text/html'}}])

    def make_run(self, **values):
        form = BrowserForm(values)
        self.assertTrue(form.is_valid())
        return create_run(self.source, self.owner, form.cleaned_data)

    def test_discovered_resource_subdomains_do_not_expand_navigation_or_external_hosts(self):
        from scans.models import ScanDocument
        ScanDocument.objects.create(batch=self.source,index=0,url='https://example.com/',status=200,fetched_at=timezone.now(),
            content_type='text/html',body=b'<script src="https://analytics.example.com/script.js"></script><script src="https://example.com.attacker.org/x"></script><script src="http://127.0.0.1/x"></script>')
        run=self.make_run(max_pages=2)
        self.assertEqual(run.config['resource_hosts'],['analytics.example.com'])
        self.assertEqual(run.config['allowed_hosts'],['example.com'])
        self.assertEqual(run.batch.request_metadata['discovered_resource_hosts'],['analytics.example.com'])

    def test_creates_separate_batch_and_fixed_budgets(self):
        run = self.make_run(max_pages=2, resource_hosts='cdn.example.com')
        self.assertNotEqual(run.batch_id, self.source.pk)
        self.assertEqual(run.batch.scan_kind, 'browser')
        self.assertEqual(run.config['allowed_hosts'], ['example.com'])
        self.assertEqual(run.config['resource_hosts'], ['cdn.example.com'])
        self.assertEqual(run.visits.count(), 1)
        self.assertEqual(self.source.records.count(), 1)
        self.assertEqual(run.config['max_requests'], 100)

    def test_render_differences_assets_links_and_publication(self):
        run = self.make_run(max_pages=2)
        visit = store_visit(run.visits.get().pk, response())
        self.assertEqual(visit.differences['titles']['rendered'], ['Gerenderd'])
        self.assertEqual(visit.differences['added_link_count'], 1)
        self.assertEqual(run.visits.get(discovery='rendered_link').url, 'https://example.com/js-only')
        self.assertEqual(visit.resources.get(resource_type='image').status, 404)
        self.assertEqual(visit.artifacts.count(), 3)
        self.assertEqual(visit.accessibility_issues.get().rule, 'image-alt')
        run.visits.filter(status='pending').update(status='failed')
        finalize(run.pk)
        run.batch.refresh_from_db()
        self.assertEqual(run.batch.status, 'incomplete')
        self.assertEqual(run.batch.url_observations.get(original_url=visit.url).facts['title'], 'Gerenderd')
        analysis = run.batch.analysis_runs.get(definition__key='browser-basics')
        self.assertEqual(Finding.objects.filter(result__run=analysis).count(), 2)
        broken = Finding.objects.get(result__run=analysis, rule='broken_resource')
        self.assertEqual(broken.details['source_url'], 'https://example.com/')
        self.assertEqual(broken.details['destination'], 'https://example.com/broken.png')
        self.assertTrue(Finding.objects.filter(result__run=analysis, evidence__browser_artifact__kind='screenshot').exists())
        finalize(run.pk)
        self.assertEqual(run.batch.analysis_runs.count(), 2)

    def test_sample_limit_prevents_following_extra_links(self):
        run = self.make_run(max_pages=1)
        store_visit(run.visits.get().pk, response())
        self.assertEqual(run.visits.count(), 1)

    def test_resources_scope_denials_and_timeouts_remain_incomplete(self):
        run = self.make_run(max_pages=1)
        payload = response()
        payload['blocked'] = [{'url': 'https://cdn.example.com/app.js', 'reason': 'host_scope'}]
        payload['timed_out'] = True
        visit = store_visit(run.visits.get().pk, payload)
        self.assertEqual(visit.status, 'incomplete')
        self.assertTrue(visit.coverage['timed_out'])
        self.assertEqual(visit.coverage['blocked'], payload['blocked'])

    def test_artifact_validation_and_rollback(self):
        run = self.make_run(max_pages=1)
        payload = response(); payload['screenshot'] = base64.b64encode(b'not PNG').decode()
        with self.assertRaises(ScanError):
            store_visit(run.visits.get().pk, payload)
        self.assertFalse(BrowserArtifact.objects.filter(visit__run=run).exists())
        self.assertEqual(run.visits.get().status, 'pending')

    def test_non_utf8_server_header_does_not_corrupt_utf8_dom(self):
        run = self.make_run(max_pages=1)
        payload = response(); payload['server_response']['headers']['content-type'] = 'text/html; charset=iso-8859-1'
        payload['dom'] = base64.b64encode('<title>Geërfd</title>'.encode()).decode()
        visit = store_visit(run.visits.get().pk, payload)
        self.assertEqual(visit.rendered_facts['titles'], ['Geërfd'])

    def test_revoked_access_and_cancelled_batch_reject_publication(self):
        run = self.make_run(max_pages=1)
        run.batch.status = 'cancelled'; run.batch.save(update_fields=['status'])
        with self.assertRaises(PermissionDenied):
            store_visit(run.visits.get().pk, response())
        self.assertFalse(run.batch.records.exists())
        self.assertFalse(BrowserArtifact.objects.filter(visit__run=run).exists())

    def test_duplicate_task_delivery_and_restart(self):
        run = self.make_run(max_pages=1)
        with patch('browser_scans.tasks.render', return_value=response()) as renderer:
            process_browser(str(run.pk))
            process_browser(str(run.pk))
        self.assertEqual(renderer.call_count, 1)
        self.assertEqual(run.batch.analysis_runs.count(), 2)

    def test_worker_failure_is_a_real_failed_observation_not_a_fake_http_status(self):
        run = self.make_run(max_pages=1)
        with patch('browser_scans.tasks.render', side_effect=OSError('offline')):
            process_browser(str(run.pk))
        run.batch.refresh_from_db()
        self.assertEqual(run.batch.status, 'incomplete')
        observation = run.batch.url_observations.get()
        self.assertNotIn('http_status', observation.facts)
        self.assertTrue(observation.facts['scan_error'])

    def test_incomplete_axe_rules_are_not_a_pass(self):
        run = self.make_run(max_pages=1)
        payload = response(); payload['accessibility'] = {'version':'4.14.0','violations':[],'incomplete':[{'id':'color-contrast','nodes':1}]}; payload['resources'] = []
        store_visit(run.visits.get().pk, payload); finalize(run.pk)
        self.assertEqual(run.batch.analysis_runs.get(definition__key='browser-basics').results.get().outcome, 'unknown')

    def test_project_scoped_pages_artifacts_and_export(self):
        run = self.make_run(max_pages=1)
        visit = store_visit(run.visits.get().pk, response()); finalize(run.pk)
        artifact = visit.artifacts.get(kind='rendered_dom')
        screenshot = visit.artifacts.get(kind='screenshot')
        urls = [reverse('ui:browser:overview', args=[self.project.pk]), reverse('ui:browser:run',args=[self.project.pk,run.pk]), reverse('ui:browser:visit',args=[self.project.pk,visit.pk]), reverse('ui:browser:artifact',args=[self.project.pk,artifact.pk])]
        self.client.force_login(self.viewer)
        for url in urls:
            response_ = self.client.get(url)
            self.assertEqual(response_.status_code, 200, url)
            self.assertIn('no-store', response_['Cache-Control'])
        download = self.client.get(urls[-1]+'?inline=1')
        self.assertEqual(download['Content-Type'], 'application/octet-stream')
        self.assertTrue(download['Content-Disposition'].startswith('attachment'))
        image = self.client.get(reverse('ui:browser:artifact',args=[self.project.pk,screenshot.pk])+'?inline=1')
        self.assertEqual(image['Content-Type'], 'image/png')
        self.assertEqual(self.client.post(reverse('ui:browser:start',args=[self.project.pk,self.source.pk])).status_code,403)
        self.client.force_login(self.other)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code,404)
        self.assertEqual(self.client.get(reverse('ui:browser:start',args=[self.project.pk,self.source.pk])).status_code,404)

    def test_browser_form_post_enqueues_only_valid_configuration(self):
        self.client.force_login(self.owner)
        url = reverse('ui:browser:start', args=[self.project.pk,self.source.pk])
        with patch('browser_scans.views.process_browser.delay') as queue:
            self.assertEqual(self.client.post(url, {'max_pages':101}).status_code,400)
            queue.assert_not_called()
            self.assertEqual(self.client.post(url, {'max_pages':1}).status_code,302)
            self.assertEqual(queue.call_count,1)

    def test_deletion_removes_browser_data_and_preserves_source(self):
        run = self.make_run(max_pages=1)
        store_visit(run.visits.get().pk, response()); finalize(run.pk)
        with patch('sources.tasks.enqueue_cleanup'):
            delete_batch(batch_id=run.batch_id, project=self.project, actor=self.owner)
        self.assertFalse(BrowserRun.objects.filter(pk=run.pk).exists())
        self.assertFalse(BrowserArtifact.objects.exists())
        self.assertTrue(ImportBatch.objects.filter(pk=self.source.pk).exists())


class BrokerPolicyTests(SimpleTestCase):
    def broker(self, messages, resource_hosts=None, error=None, blocked_resource_hosts=None,config_overrides=None):
        from .client import render
        class Duplex:
            def __init__(self):
                self.incoming = io.BytesIO(b''.join(json.dumps(message).encode()+b'\n' for message in messages))
                self.outgoing = io.BytesIO()
            def readline(self, size):
                return self.incoming.readline(size)
            def write(self, value):
                return self.outgoing.write(value)
            def flush(self):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        stream = Duplex()
        class Transport:
            calls = []
            def __init__(self, *args, **kwargs):
                self.documents = []; self.transferred = self.decoded_bytes = 0
            def fetch_once(self, url):
                self.calls.append(url)
                if error:
                    raise error
                return FetchResult(url, 200, {'content-type':'text/html', 'set-cookie':'private', 'authorization':'secret'}, b'HTML', elapsed=0.001)
            def close(self):
                pass
        config = {'allowed_hosts':['example.com'], 'resource_hosts':resource_hosts or [], 'timeout_ms':5000,
            'blocked_resource_hosts':blocked_resource_hosts or [],
            'requests_per_second':1,'max_bytes':100000,'respect_robots':False,'settle_ms':100,'scroll_steps':0,'profile':'desktop','max_requests':10}
        config.update(config_overrides or {})
        with patch('browser_scans.client.socket.socket') as socket:
            socket.return_value.makefile.return_value = stream
            result = render('https://example.com/',config,check_access=lambda:None,batch_id='test',transport_factory=Transport)
        outputs = [json.loads(line) for line in stream.outgoing.getvalue().splitlines()]
        return result, Transport.calls, outputs

    def test_only_exact_resource_hosts_are_allowed_and_cannot_become_navigation_hosts(self):
        requests = [
            {'type':'request','url':'http://127.0.0.1/secret','method':'GET','main_navigation':False},
            {'type':'request','url':'https://example.com/post','method':'POST','main_navigation':False},
            {'type':'request','url':'https://cdn.example.com/script.js','method':'GET','main_navigation':False},
            {'type':'request','url':'https://cdn.example.com/','method':'GET','main_navigation':True},
            {'type':'result','final_url':'https://example.com/'}]
        result, calls, outputs = self.broker(requests, ['cdn.example.com'])
        self.assertEqual(calls, ['https://cdn.example.com/script.js'])
        self.assertEqual([row['allowed'] for row in outputs[1:]], [False,False,True,False])
        self.assertNotIn('set-cookie', outputs[3]['headers'])
        self.assertNotIn('authorization', outputs[3]['headers'])

    def test_resource_blacklist_overrides_allowed_hosts_without_blocking_main_page(self):
        requests=[{'type':'request','url':'https://example.com/style.css','method':'GET','main_navigation':False},
                  {'type':'request','url':'https://cdn.example.com/script.js','method':'GET','main_navigation':False},
                  {'type':'request','url':'https://example.com/','method':'GET','main_navigation':True},
                  {'type':'result','final_url':'https://example.com/'}]
        _,calls,outputs=self.broker(requests,['cdn.example.com'],blocked_resource_hosts=['example.com','cdn.example.com'])
        self.assertEqual(calls,['https://example.com/'])
        self.assertEqual([row['allowed'] for row in outputs[1:]],[False,False,True])

    def test_byte_budget_stops_subsequent_network_fetches(self):
        from scans.network import ScanBudgetExceeded
        requests = [{'type':'request','url':'https://example.com/'+str(i),'method':'GET','main_navigation':False} for i in range(2)]
        requests.append({'type':'result','final_url':'https://example.com/'})
        _, calls, outputs = self.broker(requests, error=ScanBudgetExceeded('budget'))
        self.assertEqual(calls, ['https://example.com/0'])
        self.assertTrue(all(not row['allowed'] for row in outputs[1:]))

    def test_final_browser_url_cannot_escape_navigation_scope(self):
        with self.assertRaises(ScanError):
            self.broker([{'type':'result','final_url':'http://127.0.0.1/private'}])


class SamplingTests(SimpleTestCase):
    def test_spreads_paths_and_excludes_parameter_duplicates_and_infrastructure(self):
        from .sampling import select_urls
        urls=['https://example.com/']+['https://example.com/?topic='+str(i) for i in range(20)]+[
            'https://example.com/cdn-cgi/l/email-protection','https://example.com/city/amsterdam/',
            'https://example.com/city/rotterdam/','https://example.com/help/faq/','https://other.example/x']
        selected=select_urls(urls,['example.com'],5)
        self.assertEqual(set(selected),{'https://example.com/','https://example.com/city/amsterdam/','https://example.com/city/rotterdam/','https://example.com/help/faq/'})
        self.assertEqual(len(select_urls(urls,['example.com'],2)),2)

    def test_large_city_section_keeps_a_distinct_deeper_template(self):
        from .sampling import select_urls
        urls=['https://example.com/city/']+['https://example.com/city/a'+str(i)+'/' for i in range(100)]+['https://example.com/city/place/village/']
        selected=select_urls(urls,['example.com'],4)
        self.assertIn('https://example.com/city/place/village/',selected)
        self.assertEqual(len(selected),4)
