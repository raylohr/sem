import gzip
import json
from unittest.mock import patch, MagicMock
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from projects.models import Project, Membership
from sources.models import ImportBatch
from sources.services import delete_batch
from .models import ScanTarget, ScanDocument
from .network import normalize_url, resolve_public, SafeClient, FetchResult, ScanError, ScanBudgetExceeded, RobotsDenied
from .runners import crawl, sitemap, parse_sitemap
from .extraction import extract_page
from .tasks import process_scan, ScanCancelled


class NetworkTests(SimpleTestCase):
    def test_private_and_credential_urls_blocked(self):
        for url in ['http://127.0.0.1/', 'http://192.168.178.152/', 'http://169.254.169.254/',
                    'http://[::1]/', 'http://[fec0::1]/', 'http://[2002:7f00:1::]/', 'http://[::ffff:127.0.0.1]/', 'http://224.0.0.1/',
                    'http://2130706433/', 'http://localhost/', 'http://server.home/',
                    'http://user:password@example.com/', 'http://example.com:5432/', 'file:///etc/passwd']:
            with self.subTest(url=url), self.assertRaises(ScanError):
                normalize_url(url)

    def test_exact_hosts_fragments_queries_and_idna(self):
        self.assertEqual(normalize_url('https://EXAMPLE.com/page?a=2&b=1#anchor', ['example.com']), 'https://example.com/page?a=2&b=1')
        with self.assertRaises(ScanError):
            normalize_url('https://sub.example.com/', ['example.com'])
        self.assertEqual(normalize_url('https://bücher.de/'), 'https://xn--bcher-kva.de/')

    @patch('scans.network.socket.getaddrinfo')
    def test_mixed_private_and_public_dns_rejected(self, dns):
        dns.return_value = [(2, 1, 6, '', ('93.184.216.34', 443)), (2, 1, 6, '', ('10.0.0.1', 443))]
        with self.assertRaises(ScanError):
            resolve_public('example.com', 443)

    @patch('scans.network.resolve_public', return_value='93.184.216.34')
    @patch('scans.network.urllib3.HTTPSConnectionPool')
    def test_https_connects_to_pinned_ip_with_original_sni(self, pool, dns):
        response = pool.return_value.urlopen.return_value
        response.headers = {'content-type': 'text/html'}
        response.status = 200
        response.read.side_effect = [b'<html>ok</html>', b'']
        client = SafeClient(['example.com'], check_access=lambda: None)
        client._request('https://example.com/', 0)
        self.assertEqual(pool.call_args.args[0], '93.184.216.34')
        self.assertEqual(pool.call_args.kwargs['server_hostname'], 'example.com')
        self.assertEqual(pool.call_args.kwargs['assert_hostname'], 'example.com')
        kwargs = pool.return_value.urlopen.call_args.kwargs
        self.assertFalse(kwargs['redirect'])
        self.assertEqual(kwargs['headers']['Host'], 'example.com')

    @patch('scans.network.resolve_public', return_value='93.184.216.34')
    @patch('scans.network.urllib3.HTTPSConnectionPool')
    def test_gzip_bomb_is_bounded(self, pool, dns):
        response = pool.return_value.urlopen.return_value
        response.headers = {'content-encoding': 'gzip'}
        response.status = 200
        response.read.side_effect = [gzip.compress(b'x'*1000), b'']
        client = SafeClient(['example.com'], check_access=lambda: None)
        with patch('scans.network.MAX_BODY', 100), self.assertRaises(ScanError):
            client._request('https://example.com/', 0)

    def test_external_redirect_not_followed(self):
        client = SafeClient(['example.com'], check_access=lambda: None)
        with patch.object(client, '_request', return_value=FetchResult('https://example.com/', 302, {'location': 'http://127.0.0.1/'}, b'')) as request:
            result = client.fetch('https://example.com/', enforce_robots=False)
            self.assertTrue(result.blocked_redirect)
            request.assert_called_once()

    def test_redirect_to_private_rebinding_is_rechecked(self):
        client = SafeClient(['example.com'], check_access=lambda: None)
        with patch('scans.network.resolve_public', side_effect=['93.184.216.34', ScanError('private DNS')]), patch('scans.network.urllib3.HTTPSConnectionPool') as pool:
            response = pool.return_value.urlopen.return_value
            response.headers = {'location': '/next'}
            response.status = 302
            response.read.return_value = b''
            with patch.object(client, '_pace'), self.assertRaises(ScanError):
                client.fetch('https://example.com/', enforce_robots=False)
            self.assertEqual(pool.call_count, 1)

    def test_robots_disallow_prevents_page_request(self):
        client = SafeClient(['example.com'], check_access=lambda: None)
        with patch.object(client, '_request', return_value=FetchResult('https://example.com/robots.txt', 200, {}, b'User-agent: *\nDisallow: /private\n')) as request:
            with self.assertRaises(RobotsDenied):
                client.fetch('https://example.com/private')
            self.assertEqual(request.call_count, 1)

    def test_robots_503_fails_closed(self):
        client = SafeClient(['example.com'], check_access=lambda: None)
        with patch.object(client, '_request', return_value=FetchResult('https://example.com/robots.txt', 503, {}, b'')):
            with self.assertRaises(RobotsDenied):
                client.fetch('https://example.com/')

    def test_robots_delay_and_request_rate_respected(self):
        client = SafeClient(['example.com'], check_access=lambda: None)
        robot = FetchResult('https://example.com/robots.txt', 200, {}, b'User-agent: *\nCrawl-delay: 3\nRequest-rate: 1/5\nAllow: /\n')
        with patch.object(client, '_request', side_effect=[robot, FetchResult('https://example.com/', 200, {}, b'')]) as request:
            client.fetch('https://example.com/')
            self.assertEqual(request.call_args.args[1], 5)

    def test_cancel_propagates_out_of_transport(self):
        client = SafeClient(['example.com'], check_access=MagicMock(side_effect=ScanCancelled))
        with self.assertRaises(ScanCancelled):
            client.fetch('https://example.com/')


class ParsingTests(SimpleTestCase):
    def test_html_metadata_links_and_indexability(self):
        html = b'''<html lang="nl"><head><title>One &amp; Two</title><meta name="description" content="Description"><link rel="canonical" href="/canonical"><meta name="robots" content="noindex, follow"></head><body><h1>Heading</h1><h2>Second</h2><a href="/next#fragment">Next</a><script>hidden words</script></body></html>'''
        result = FetchResult('https://example.com/', 200, {'content-type': 'text/html'}, html)
        data, links = extract_page(result)
        self.assertEqual(data['titles'], ['One & Two'])
        self.assertEqual(data['h1'], ['Heading'])
        self.assertEqual(data['canonicals'], ['https://example.com/canonical'])
        self.assertFalse(data['is_indexable'])
        self.assertNotIn('hidden words', data['text'])
        self.assertIn('https://example.com/next#fragment', links)

    def test_entity_and_dtd_sitemaps_rejected(self):
        body = b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><urlset><url><loc>&xxe;</loc></url></urlset>'
        with self.assertRaises(ScanError):
            parse_sitemap(body)

    def test_sitemap_index_deduplication_and_scope(self):
        client = MagicMock()
        client.allowed_hosts = {'example.com'}
        client.fetch.side_effect = [
            FetchResult('https://example.com/sitemap.xml', 200, {}, b'<sitemapindex><sitemap><loc>https://example.com/pages.xml</loc></sitemap><sitemap><loc>https://example.com/sitemap.xml</loc></sitemap></sitemapindex>'),
            FetchResult('https://example.com/pages.xml', 200, {}, b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.com/page</loc><lastmod>2026-10-01</lastmod></url><url><loc>https://example.com/page</loc></url><url><loc>http://127.0.0.1/</loc></url></urlset>')]
        records, warnings = sitemap(client, url='https://example.com/sitemap.xml', max_urls=100, max_depth=3)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['data']['lastmod'], '2026-10-01')
        self.assertFalse(records[0]['data']['page_fetched'])
        self.assertEqual(client.fetch.call_count, 2)
        self.assertTrue(any('toegestane hosts' in w for w in warnings))

    def test_crawl_respects_depth_and_reports_redirect_status(self):
        client = MagicMock()
        client.allowed_hosts = {'example.com'}
        client.fetch.return_value = FetchResult('https://example.com/final', 200, {'content-type': 'text/html'}, b'<title>Home</title><a href="/next">Next</a>', redirects=[{'url':'https://example.com/', 'status':301, 'destination':'https://example.com/final'}])
        records, warnings = crawl(client, url='https://example.com/', max_urls=10, max_depth=0)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['data']['status'], 301)
        self.assertEqual(records[0]['data']['final_status'], 200)
        client.fetch.assert_called_once()

    def test_crawl_stops_after_rate_limit(self):
        client = MagicMock()
        client.allowed_hosts = {'example.com'}
        client.fetch.return_value = FetchResult('https://example.com/', 429, {'content-type':'text/html'}, b'<a href="/next">Next</a>')
        records, warnings = crawl(client, url='https://example.com/', max_urls=10, max_depth=3)
        self.assertEqual(len(records), 1)
        client.fetch.assert_called_once()
        self.assertTrue(any('429/503' in w for w in warnings))

    def test_time_limit_is_visible(self):
        client = MagicMock()
        client.allowed_hosts = {'example.com'}
        client.fetch.side_effect = ScanBudgetExceeded('Time budget')
        records, warnings = crawl(client, url='https://example.com/', max_urls=10, max_depth=3)
        self.assertEqual(records, [])
        self.assertIn('Time budget', warnings)


class ScanAccessTests(TestCase):
    def setUp(self):
        import tempfile
        from django.test import override_settings
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        override=override_settings(MEDIA_ROOT=self.directory.name)
        override.enable()
        self.addCleanup(override.disable)
        U = get_user_model()
        self.admin = U.objects.create_user('scan-admin')
        self.editor = U.objects.create_user('scan-editor')
        self.viewer = U.objects.create_user('scan-viewer')
        self.other = U.objects.create_user('scan-other')
        self.project = Project.objects.create(name='Scans', website='https://example.com', created_by=self.admin)
        for user, role in [(self.admin,'admin'), (self.editor,'editor'), (self.viewer,'viewer')]:
            Membership.objects.create(project=self.project, user=user, role=role)
        self.target = ScanTarget.objects.create(project=self.project, name='Website', url='https://example.com/', allowed_hosts=['example.com'], created_by=self.admin)
        self.client.force_login(self.admin)

    def url(self, name):
        args = [self.project.pk]
        if name in ('start', 'target-deactivate'):
            args.append(self.target.pk)
        return reverse('projects:scans:'+name, args=args)

    def make_batch(self):
        return ImportBatch.objects.create(project=self.project, created_by=self.editor, scan_target=self.target,
            source='crawler', method='scan', label='Crawl', request_metadata={
                'scan':'crawl', 'url':self.target.url, 'allowed_hosts':['example.com'], 'duration':10, 'max_urls':2,'max_depth':1})

    def test_target_requires_admin(self):
        self.client.force_login(self.editor)
        self.assertEqual(self.client.post(self.url('target-create'), '{}', content_type='application/json').status_code, 403)

    def test_target_adds_primary_host_and_rejects_private_hosts(self):
        response = self.client.post(self.url('target-create'), json.dumps({'name':'Own', 'url':'https://example.org/', 'kind':'own'}), content_type='application/json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['allowed_hosts'], ['example.org'])
        response = self.client.post(self.url('target-create'), json.dumps({'name':'Private', 'url':'http://192.168.178.152/', 'kind':'own'}), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_viewer_cannot_start_or_cancel(self):
        batch = self.make_batch()
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(self.url('start'), '{}', content_type='application/json').status_code, 403)
        self.assertEqual(self.client.post(reverse('projects:scans:cancel', args=[self.project.pk,batch.pk])).status_code, 403)

    def test_start_preserves_actor_and_scope(self):
        self.client.force_login(self.editor)
        with patch('scans.views.enqueue_scan') as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(self.url('start'), json.dumps({'kind':'crawl','max_urls':10,'max_depth':0}), content_type='application/json')
            self.assertEqual(response.status_code, 202)
            enqueue.assert_called_once()
        batch = ImportBatch.objects.get()
        self.assertEqual(batch.created_by, self.editor)
        self.assertEqual(batch.request_metadata['allowed_hosts'], ['example.com'])
        self.assertEqual(batch.request_metadata['max_depth'], 0)
        self.assertEqual(batch.source,'semscan')
        self.assertTrue(batch.request_metadata['respect_robots'])
        self.assertEqual(batch.request_metadata['concurrency'],1)
        self.assertEqual(batch.request_metadata['requests_per_second'],1)

    def test_omitted_scan_settings_use_broad_coverage_and_conservative_pace(self):
        response=self.client.post(self.url('start'),json.dumps({'kind':'crawl'}),content_type='application/json')
        self.assertEqual(response.status_code,202)
        batch=ImportBatch.objects.get(pk=response.json()['id'])
        for key,value in {'max_urls':100000,'max_depth':99,'concurrency':1,'requests_per_second':1,'respect_robots':True,'robots_mode':'respect'}.items():
            self.assertEqual(batch.request_metadata[key],value)
        response=self.client.post(self.url('start'),json.dumps({'kind':'crawl','robots_mode':'ignore','concurrency':8,'requests_per_second':12}),content_type='application/json')
        self.assertEqual(response.status_code,202)
        batch=ImportBatch.objects.get(pk=response.json()['id'])
        self.assertFalse(batch.request_metadata['respect_robots'])
        self.assertEqual(batch.request_metadata['concurrency'],8)
        self.assertEqual(batch.request_metadata['requests_per_second'],12)

    def test_scan_options_are_validated_and_preserved(self):
        response=self.client.post(self.url('start'),json.dumps({'kind':'sitemap','robots_mode':'respect','concurrency':4,'requests_per_second':5}),content_type='application/json')
        self.assertEqual(response.status_code,202)
        batch=ImportBatch.objects.get(pk=response.json()['id'])
        self.assertEqual(batch.scan_kind,'sitemap')
        self.assertTrue(batch.request_metadata['respect_robots'])
        self.assertEqual(batch.request_metadata['concurrency'],4)
        for field,value in [('robots_mode','bad'),('concurrency',17),('requests_per_second',16)]:
            response=self.client.post(self.url('start'),json.dumps({'kind':'crawl',field:value}),content_type='application/json')
            self.assertEqual(response.status_code,400)

    def test_seed_outside_scope_rejected(self):
        response = self.client.post(self.url('start'), json.dumps({'kind':'crawl','url':'https://other.example.com/'}), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_crawl_cap_rejected(self):
        response = self.client.post(self.url('start'), json.dumps({'kind':'crawl','max_urls':100001}), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_accepts_100000_urls_and_allocates_realistic_budgets(self):
        for kind in ('crawl','sitemap'):
            response=self.client.post(self.url('start'),json.dumps({'kind':kind,'max_urls':100000,'max_depth':100}),content_type='application/json')
            self.assertEqual(response.status_code,202)
            batch=ImportBatch.objects.get(pk=response.json()['id'])
            self.assertEqual(batch.request_metadata['max_urls'],100000)
            self.assertGreaterEqual(batch.request_metadata['duration'],100000)
            self.assertGreater(batch.request_metadata['max_transfer'],25*1024**2)

    def test_foreign_project_targets_hidden(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url('targets')).status_code, 404)
        self.assertEqual(self.client.post(self.url('start'), '{}', content_type='application/json').status_code, 404)

    def test_cancelled_batch_is_not_processed(self):
        batch = self.make_batch()
        self.assertEqual(self.client.post(reverse('projects:scans:cancel',args=[self.project.pk,batch.pk])).status_code,200)
        with patch('scans.tasks.SafeClient') as client:
            process_scan(str(batch.pk))
            client.assert_not_called()
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'cancelled')

    def test_inactive_target_prevents_network(self):
        batch = self.make_batch()
        self.client.post(self.url('target-deactivate'))
        with patch('scans.tasks.SafeClient') as client:
            process_scan(str(batch.pk))
            client.assert_not_called()
        batch.refresh_from_db()
        self.assertEqual(batch.status,'failed')

    def test_worker_publishes_documents_and_deletion_cascades(self):
        from django.utils import timezone
        batch = self.make_batch()
        doc = {'url':self.target.url, 'status':200, 'body':b'<html/>', 'content_type':'text/html','fetched_at':timezone.now()}
        with patch('scans.tasks.SafeClient') as client, patch('scans.tasks.crawl',return_value=([{'locator':'crawl:0','data':{'url':self.target.url}}],[])):
            client.return_value.documents=[doc]
            process_scan(str(batch.pk))
        batch.refresh_from_db()
        self.assertEqual(batch.status,'complete')
        self.assertEqual(batch.scan_documents.get().body,b'<html/>')
        delete_batch(batch_id=batch.pk,project=self.project,actor=self.admin)
        self.assertEqual(ScanDocument.objects.count(),0)

    def test_failed_downloads_publish_as_incomplete_and_remain_readable(self):
        from django.utils import timezone
        from catalog.models import URLObservation
        batch=self.make_batch()
        rows=[{'locator':'crawl:0','data':{'url':self.target.url,'status':200}},
              {'locator':'crawl:1','data':{'url':self.target.url+'broken','error':'Leestime-out','error_code':'read_timeout'}}]
        doc={'url':self.target.url,'status':200,'body':b'ok','content_type':'text/html','fetched_at':timezone.now()}
        with patch('scans.tasks.SafeClient') as client,patch('scans.tasks.crawl',return_value=(rows,[])):
            client.return_value.documents=[doc]
            process_scan(str(batch.pk))
        batch.refresh_from_db()
        self.assertEqual(batch.status,'incomplete')
        self.assertEqual(batch.record_count,2)
        self.assertEqual(batch.request_metadata['scan_outcome']['request_failures'],1)
        self.assertTrue(any('Onvolledige scan' in warning for warning in batch.warnings))
        self.assertEqual(URLObservation.objects.filter(batch=batch).count(),2)
        response=self.client.get(reverse('projects:sources:records',args=[self.project.pk,batch.pk]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['total'],2)
        response=self.client.get(reverse('projects:scans:document-download',args=[self.project.pk,batch.pk,0]))
        self.assertEqual(response.status_code,200)
        response=self.client.get(reverse('ui:batch',args=[self.project.pk,batch.pk]))
        self.assertContains(response,'Deze scan is onvolledig')
        response=self.client.get(reverse('projects:urls:list',args=[self.project.pk]))
        self.assertEqual(response.json()['total'],2)
        with patch('scans.tasks.SafeClient') as client:
            process_scan(str(batch.pk))
            client.assert_not_called()
        self.assertEqual(batch.records.count(),2)

    def test_historical_outcome_review_preserves_records_and_provenance(self):
        from django.core.management import call_command
        from sources.models import SourceRecord
        from io import StringIO
        batch=self.make_batch()
        batch.status='complete';batch.record_count=1;batch.save()
        SourceRecord.objects.create(batch=batch,locator='old',data={'url':self.target.url,'error':'HTTP-opvraag mislukt (netwerk, certificaat of antwoord).'})
        call_command('review_scan_outcomes',stdout=StringIO())
        batch.refresh_from_db();self.assertEqual(batch.status,'complete')
        call_command('review_scan_outcomes',apply=True,stdout=StringIO())
        batch.refresh_from_db();self.assertEqual(batch.status,'incomplete')
        self.assertEqual(batch.created_by,self.editor)
        self.assertEqual(batch.records.count(),1)
        warnings=list(batch.warnings)
        call_command('review_scan_outcomes',apply=True,stdout=StringIO())
        batch.refresh_from_db();self.assertEqual(batch.warnings,warnings)

    def test_access_revoked_during_scan_prevents_publication(self):
        batch=self.make_batch()
        def runner(*args,**kwargs):
            Membership.objects.filter(user=self.editor).delete()
            return [{'locator':'crawl:0','data':{}}],[]
        with patch('scans.tasks.SafeClient'),patch('scans.tasks.crawl',side_effect=runner):
            process_scan(str(batch.pk))
        batch.refresh_from_db()
        self.assertEqual(batch.status,'failed')
        self.assertEqual(batch.records.count(),0)

    def test_cancellation_during_scan_prevents_publication(self):
        batch=self.make_batch()
        def runner(*args,**kwargs):
            ImportBatch.objects.filter(pk=batch.pk).update(status='cancelled')
            return [{'locator':'crawl:0','data':{}}],[]
        with patch('scans.tasks.SafeClient'),patch('scans.tasks.crawl',side_effect=runner):
            process_scan(str(batch.pk))
        batch.refresh_from_db()
        self.assertEqual(batch.status,'cancelled')
        self.assertEqual(batch.records.count(),0)

    def test_raw_document_download_is_project_scoped(self):
        from django.utils import timezone
        batch=self.make_batch()
        ImportBatch.objects.filter(pk=batch.pk).update(status='complete')
        ScanDocument.objects.create(batch=batch,index=0,url=self.target.url,status=200,body=b'raw-document',content_type='text/html',fetched_at=timezone.now())
        url=reverse('projects:scans:document-download',args=[self.project.pk,batch.pk,0])
        self.assertEqual(self.client.get(url).content,b'raw-document')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code,404)

    def test_host_permission_removed_prevents_network(self):
        batch=self.make_batch()
        ScanTarget.objects.filter(pk=self.target.pk).update(allowed_hosts=['example.org'])
        with patch('scans.tasks.SafeClient') as client:
            process_scan(str(batch.pk))
            client.assert_not_called()
        batch.refresh_from_db()
        self.assertEqual(batch.status,'failed')

    def test_scan_retry_is_routed_to_scan_worker(self):
        batch=self.make_batch()
        url=reverse('projects:sources:retry',args=[self.project.pk,batch.pk])
        with patch('scans.tasks.enqueue_scan') as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.client.post(url).status_code,202)
            enqueue.assert_called_once_with(batch.pk)
