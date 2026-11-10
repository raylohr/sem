import asyncio
from datetime import timedelta
from decimal import Decimal
import gzip
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from projects.models import Project, Membership
from .models import PlaywrightCrawlSettings, PlaywrightCrawl, PlaywrightCrawlURL, PlaywrightDomainBudget
from .crawl_service import start_crawl, add_urls, dispatch_run, acquire_slot, report_health, access
from .crawl_tasks import process_crawl_url, bootstrap_crawl, recover_crawls
from .crawl_proxy import public_ip, resolve_target
from .crawl_http import SafeHTTP, CrawlNetworkError, sitemap_entries, RobotsRules
from .crawl_renderer import desktop_user_agent
from .playwright_config import DESKTOP_PROFILES


class NetworkBoundaryTests(SimpleTestCase):
    def test_public_destination_validation_rejects_private_and_translation_ranges(self):
        for value in ('127.0.0.1','10.0.0.1','172.17.0.1','192.168.178.152','169.254.169.254',
                      '0.0.0.0','::1','fc00::1','fe80::1','::ffff:127.0.0.1','64:ff9b::c0a8:101','ff02::1'):
            self.assertFalse(public_ip(value),value)
        self.assertTrue(public_ip('1.1.1.1'))
        self.assertTrue(public_ip('2606:4700:4700::1111'))

    def test_mixed_public_private_dns_is_rejected_before_connection(self):
        async def check():
            loop=asyncio.get_running_loop()
            records=[(2,1,6,'',('1.1.1.1',443)),(2,1,6,'',('127.0.0.1',443))]
            from unittest.mock import AsyncMock
            with patch.object(loop,'getaddrinfo',new=AsyncMock(return_value=records)):
                with self.assertRaises(PermissionError):
                    await resolve_target('public.example',443)
        asyncio.run(check())

    def test_sitemap_indexes_and_compressed_urlsets_and_entity_rejection(self):
        kind,urls=sitemap_entries(b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://example.test/pages.xml</loc></sitemap></sitemapindex>')
        self.assertEqual((kind,urls),('sitemapindex',['https://example.test/pages.xml']))
        xml=b'<urlset><url><loc>https://example.test/a</loc></url></urlset>'
        self.assertEqual(sitemap_entries(gzip.compress(xml)),('urlset',['https://example.test/a']))
        with self.assertRaises(CrawlNetworkError):
            sitemap_entries(b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><urlset><url><loc>&x;</loc></url></urlset>')
        with patch('integrations.crawl_http.MAX_XML',100),self.assertRaises(CrawlNetworkError):
            sitemap_entries(gzip.compress(b'x'*1000))

    def test_robots_specific_groups_wildcards_and_allow_tie(self):
        rules=RobotsRules('User-agent: *\nDisallow: /admin\nAllow: /admin/public\nDisallow: /*?secret=*\nSitemap: https://example.test/sitemap.xml\nUser-agent: Firefox\nDisallow: /private\n')
        self.assertFalse(rules.allowed('Mozilla Chrome','https://example.test/admin'))
        self.assertTrue(rules.allowed('Mozilla Chrome','https://example.test/admin/public'))
        self.assertFalse(rules.allowed('Mozilla Chrome','https://example.test/a?secret=value'))
        self.assertFalse(rules.allowed('Mozilla Firefox','https://example.test/private'))
        self.assertTrue(rules.allowed('Mozilla Firefox','https://example.test/admin'))
        self.assertEqual(rules.sitemaps,['https://example.test/sitemap.xml'])

    @patch('integrations.crawl_http.requests.Session')
    def test_external_reachability_uses_no_response_body_even_with_get_fallback(self,session_class):
        session=session_class.return_value.__enter__.return_value
        def response(status):
            item=MagicMock(status_code=status,headers={})
            item.__enter__.return_value=item
            return item
        head,get=response(405),response(200)
        session.request.side_effect=[head,get]
        result=SafeHTTP('http://proxy:8765','Desktop').reachable('https://example.org/')
        self.assertEqual(result.status,200)
        self.assertEqual([call.args[0] for call in session.request.call_args_list],['HEAD','GET'])
        head.iter_content.assert_not_called()
        get.iter_content.assert_not_called()
        self.assertFalse(session.trust_env)
        self.assertEqual(session.proxies['https'],'http://proxy:8765')

    @patch('integrations.crawl_http.requests.Session')
    def test_metadata_redirect_is_revalidated_before_following(self,session_class):
        session=session_class.return_value.__enter__.return_value
        response=MagicMock(status_code=302,headers={'location':'http://127.0.0.1/'})
        response.__enter__.return_value=response
        session.request.return_value=response
        with self.assertRaises(CrawlNetworkError):
            SafeHTTP('http://proxy:8765','Desktop').fetch('https://example.test/',allowed=lambda url:url.startswith('https://example.test/'))
        self.assertEqual(session.request.call_count,1)

    def test_profiles_use_actual_engine_version_and_never_headless_identity(self):
        for profile in DESKTOP_PROFILES.values():
            value=desktop_user_agent(profile,'150.0.1234.5')
            self.assertIn('150.0.1234.5',value)
            self.assertNotIn('Headless',value)
            self.assertTrue(value.startswith('Mozilla/5.0'))


@override_settings(CRAWLER_EGRESS_PROXY='http://proxy:8765')
class CrawlQueueTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user('crawl-owner')
        self.viewer=get_user_model().objects.create_user('crawl-viewer')
        self.project=Project.objects.create(name='Crawl',website='https://www.example.test/',created_by=self.owner,include_subdomains=True)
        Membership.objects.create(project=self.project,user=self.owner,role='admin')
        Membership.objects.create(project=self.project,user=self.viewer,role='viewer')
        self.configuration=PlaywrightCrawlSettings.objects.create(project=self.project,use_sitemaps=False,parallel_agents=2,
            start_urls=['https://example.test/a'],excluded_hosts=['staging.example.test'])

    def crawl(self,**overrides):
        with patch('integrations.crawl_tasks.enqueue_bootstrap'):
            return start_crawl(actor=self.owner,project_id=self.project.pk,overrides=overrides)

    def running(self,**overrides):
        crawl=self.crawl(**overrides)
        crawl.status='running'
        crawl.started_at=timezone.now()
        crawl.save()
        return crawl

    def queued(self,crawl,url='https://example.test/a',kind='page'):
        add_urls(crawl.pk,[url],kind=kind)
        row=crawl.urls.get(url=url)
        row.status='queued'
        row.task_token=uuid4()
        row.lease_until=timezone.now()+timedelta(minutes=4)
        row.save()
        return row

    def test_snapshot_is_immutable_and_overlapping_runs_and_unauthorized_start_are_rejected(self):
        crawl=self.crawl(max_pages=3)
        self.assertEqual(crawl.configuration['max_pages'],3)
        self.configuration.parallel_agents=20
        self.configuration.save()
        self.assertEqual(crawl.configuration['parallel_agents'],2)
        with self.assertRaises(ValidationError):
            self.crawl()
        with self.assertRaises(PermissionDenied):
            start_crawl(actor=self.viewer,project_id=self.project.pk)
        self.assertIn(crawl.profile,DESKTOP_PROFILES)

    def test_discovery_deduplicates_bounds_depth_and_never_queues_external_documents(self):
        crawl=self.running(max_pages=2,max_depth=1)
        add_urls(crawl.pk,['https://example.test/a#one','https://example.test/a#two',
            'https://blog.example.test/b','https://other.test/c','https://staging.example.test/private'])
        self.assertEqual(crawl.urls.count(),2)
        add_urls(crawl.pk,['https://example.test/c'])
        crawl.refresh_from_db()
        self.assertFalse(crawl.limit_reached)
        self.assertEqual(crawl.urls.count(),3)
        add_urls(crawl.pk,['https://external.test/'],kind='external')
        self.assertEqual(crawl.urls.filter(kind='external').count(),1)

    def test_start_rate_is_shared_and_parallel_slots_are_not_exceeded(self):
        crawl=self.running()
        first=self.queued(crawl)
        second=self.queued(crawl,'https://example.test/b')
        self.assertEqual(acquire_slot(first.pk,first.task_token),0)
        delay=acquire_slot(second.pk,second.task_token)
        self.assertGreater(delay,0)
        second.refresh_from_db()
        self.assertEqual(second.status,'queued')
        PlaywrightDomainBudget.objects.filter(pk=crawl.domain).update(next_start_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(acquire_slot(second.pk,second.task_token),0)
        third=self.queued(crawl,'https://example.test/c')
        self.assertGreater(acquire_slot(third.pk,third.task_token),0)
        self.assertEqual(crawl.urls.filter(status='processing').count(),2)

    def test_slow_responses_reduce_rate_and_agents_then_recover_gradually(self):
        crawl=self.running()
        report_health(crawl.pk,slow=True)
        crawl.refresh_from_db()
        budget=PlaywrightDomainBudget.objects.get(pk=crawl.domain)
        self.assertEqual(crawl.current_agents,1)
        self.assertEqual(budget.rate_factor,Decimal('.25'))
        report_health(crawl.pk)
        budget.refresh_from_db()
        self.assertEqual(budget.rate_factor,Decimal('.25'))
        PlaywrightDomainBudget.objects.filter(pk=crawl.domain).update(slow_until=timezone.now()-timedelta(seconds=1),healthy_samples=2)
        report_health(crawl.pk)
        budget.refresh_from_db()
        crawl.refresh_from_db()
        self.assertEqual(budget.rate_factor,Decimal('.5'))
        self.assertEqual(crawl.current_agents,2)

    @patch('integrations.crawl_tasks.enqueue_url')
    def test_dispatch_queues_at_most_configured_agents_and_completion_is_truthful(self,enqueue):
        crawl=self.running()
        add_urls(crawl.pk,[f'https://example.test/{i}' for i in range(5)])
        with self.captureOnCommitCallbacks(execute=True):
            dispatch_run(crawl.pk)
        self.assertEqual(enqueue.call_count,2)
        crawl.urls.update(status='complete')
        dispatch_run(crawl.pk)
        crawl.refresh_from_db()
        self.assertEqual(crawl.status,'complete')

    @patch('integrations.crawl_tasks.call_renderer')
    @patch('integrations.crawl_tasks.enqueue_url')
    def test_agent_handles_rendered_links_and_keeps_html_out_of_metadata(self,enqueue,render):
        crawl=self.running()
        row=self.queued(crawl)
        from .crawl_renderer import PageCapture
        from .crawl_content import pack_content
        capture=PageCapture(url=row.url,http_status=200,render_complete=True,ttfb_seconds=.1,
            server_html=b'<html>Server</html>',rendered_html='<html>DOM</html>',text='DOM',
            links=['https://blog.example.test/new','https://outside.test/'])
        render.return_value={**capture.metadata(),'artifacts':pack_content(capture)}
        def discover_before_completion(*args,**kwargs):
            self.assertEqual(type(row).objects.get(pk=row.pk).status,'processing')
            return add_urls(*args,**kwargs)
        with patch('integrations.crawl_tasks.add_urls',side_effect=discover_before_completion):
            process_crawl_url(row.pk,str(row.task_token))
        row.refresh_from_db()
        self.assertEqual(row.status,'complete',row.error)
        self.assertTrue(crawl.urls.filter(url='https://blog.example.test/new',kind='page').exists())
        self.assertTrue(crawl.urls.filter(url='https://outside.test/',kind='external').exists())
        self.assertNotIn('rendered_html',row.metadata)
        self.assertNotIn('server_html',row.metadata)

    @patch('integrations.crawl_tasks.call_renderer')
    def test_actor_revocation_stops_new_network_work(self,render):
        crawl=self.running()
        row=self.queued(crawl)
        Membership.objects.filter(project=self.project,user=self.owner).update(role='viewer')
        process_crawl_url(row.pk,str(row.task_token))
        crawl.refresh_from_db()
        self.assertEqual(crawl.status,'cancelled')
        render.assert_not_called()

    @patch('integrations.crawl_tasks.enqueue_url')
    @patch('integrations.crawl_tasks.call_renderer')
    def test_timeout_is_retried_with_backoff_and_final_failure_is_incomplete(self,render,enqueue):
        crawl=self.running()
        row=self.queued(crawl)
        render.return_value={'error':'timeout','render_complete':False}
        process_crawl_url(row.pk,str(row.task_token))
        row.refresh_from_db()
        self.assertEqual(row.status,'pending')
        self.assertIsNotNone(row.next_attempt_at)
        row.status='queued'
        row.attempts=2
        row.task_token=uuid4()
        row.save()
        PlaywrightDomainBudget.objects.filter(pk=crawl.domain).update(next_start_at=timezone.now()-timedelta(seconds=1))
        process_crawl_url(row.pk,str(row.task_token))
        row.refresh_from_db()
        crawl.refresh_from_db()
        self.assertEqual(row.status,'failed')
        self.assertEqual(crawl.status,'incomplete')

    @patch('integrations.crawl_tasks.enqueue_url')
    def test_preparation_uses_saved_seeds_without_starting_external_crawls(self,enqueue):
        crawl=self.crawl()
        with self.captureOnCommitCallbacks(execute=True):
            bootstrap_crawl(str(crawl.pk))
        crawl.refresh_from_db()
        self.assertEqual(crawl.status,'running',crawl.error)
        self.assertEqual(crawl.urls.count(),2)
        self.assertEqual(crawl.urls.filter(kind='external').count(),0)

    @patch('integrations.crawl_tasks.enqueue_url')
    def test_time_threshold_does_not_stop_work_and_scope_changes_still_revoke_access(self,enqueue):
        crawl=self.running(max_duration_minutes=1)
        crawl.started_at=timezone.now()-timedelta(minutes=2)
        crawl.save()
        add_urls(crawl.pk,['https://example.test/a'])
        recover_crawls()
        crawl.refresh_from_db()
        self.assertEqual(crawl.status,'running')
        self.assertEqual(access(crawl.pk).pk,crawl.pk)
        Project.objects.filter(pk=self.project.pk).update(include_subdomains=False)
        with self.assertRaises(PermissionDenied):
            access(crawl.pk)

    def test_all_crawl_tasks_are_routed_to_dedicated_queue(self):
        self.assertEqual(settings.CELERY_TASK_ROUTES['integrations.crawl_tasks.process_crawl_url']['queue'],'crawler')
        self.assertEqual(settings.CELERY_TASK_ROUTES['integrations.crawl_tasks.bootstrap_crawl']['queue'],'crawler')

    @patch('integrations.crawl_tasks.enqueue_url')
    def test_parallel_dispatch_is_capped_at_twenty(self,enqueue):
        self.configuration.parallel_agents=20
        self.configuration.save()
        crawl=self.running()
        add_urls(crawl.pk,[f'https://example.test/{i}' for i in range(25)])
        with self.captureOnCommitCallbacks(execute=True):
            dispatch_run(crawl.pk)
        self.assertEqual(enqueue.call_count,20)
        self.assertEqual(crawl.urls.filter(status='queued').count(),20)

    @patch('integrations.crawl_tasks.enqueue_url')
    def test_worker_crashes_respect_retry_limit(self,enqueue):
        crawl=self.running()
        row=self.queued(crawl)
        row.status='processing'
        row.attempts=3
        row.lease_until=timezone.now()-timedelta(seconds=1)
        row.save()
        recover_crawls()
        row.refresh_from_db()
        crawl.refresh_from_db()
        self.assertEqual(row.status,'failed')
        self.assertEqual(crawl.status,'incomplete')

    @patch('integrations.crawl_tasks.metadata_pause')
    @patch('integrations.crawl_tasks.robots_for')
    @patch('integrations.crawl_tasks.SafeHTTP')
    @patch('integrations.crawl_tasks.enqueue_url')
    def test_sitemap_index_discovers_only_project_urls_and_robots_are_not_enforced_by_default(self,enqueue,http,robots,pause):
        from .crawl_http import HTTPResult
        self.configuration.use_sitemaps=True
        self.configuration.save()
        robots.return_value={'text':'User-agent: *\nDisallow: /\nSitemap: https://www.example.test/index.xml','unreachable':False}
        def fetch(url,**kwargs):
            if url.endswith('index.xml'):
                return HTTPResult(url,200,{},b'<sitemapindex><sitemap><loc>https://www.example.test/pages.xml</loc></sitemap></sitemapindex>',[])
            if url.endswith('pages.xml'):
                return HTTPResult(url,200,{},b'<urlset><url><loc>https://blog.example.test/from-sitemap</loc></url><url><loc>https://outside.test/no</loc></url></urlset>',[])
            return HTTPResult(url,404,{},b'',[])
        http.return_value.fetch.side_effect=fetch
        crawl=self.crawl()
        bootstrap_crawl(str(crawl.pk))
        crawl.refresh_from_db()
        self.assertEqual(crawl.status,'running')
        self.assertTrue(crawl.urls.filter(url='https://blog.example.test/from-sitemap',provenance='sitemap').exists())
        self.assertFalse(crawl.urls.filter(url__contains='outside.test').exists())

    @patch('integrations.crawl_tasks.call_renderer')
    @patch('integrations.crawl_tasks.enqueue_url')
    def test_stale_agent_delivery_cannot_replace_result_or_start_browser(self,enqueue,renderer):
        crawl=self.running()
        row=self.queued(crawl)
        old=uuid4()
        process_crawl_url(row.pk,str(old))
        row.refresh_from_db()
        self.assertEqual(row.status,'queued')
        renderer.assert_not_called()
