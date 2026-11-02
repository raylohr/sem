import tempfile
import threading
import time
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from .network import SafeClient, FetchResult, RobotsDenied, ScanBudgetExceeded
from .work import ScanWork, ScanPause
from .runners import crawl


class ParallelTests(SimpleTestCase):
    def test_audit_fetches_disallowed_url_and_records_google_policy(self):
        cache={'https://example.com':{'status':200,'body':'User-agent: *\nDisallow: /private\n'}}
        client=SafeClient(['example.com'],check_access=lambda:None,respect_robots=False,robot_cache=cache)
        with patch.object(client,'_request',return_value=FetchResult('https://example.com/private',200,{},b'')) as request:
            result=client.fetch('https://example.com/private')
        request.assert_called_once()
        self.assertFalse(result.robots_allowed)
        self.assertFalse(result.googlebot_allowed)

    def test_audit_preserves_budget_and_host_guards(self):
        client=SafeClient(['example.com'],check_access=lambda:None,respect_robots=False)
        with patch.object(client,'_robot_policy',side_effect=ScanBudgetExceeded('budget')):
            with self.assertRaises(ScanBudgetExceeded): client.fetch('https://example.com/')
        with self.assertRaises(ValueError): client.fetch('https://sub.example.com/')

    def test_unavailable_robots_does_not_prevent_audit(self):
        client=SafeClient(['example.com'],check_access=lambda:None,respect_robots=False)
        from .network import ScanError
        with patch.object(client,'_robot_policy',side_effect=ScanError('unavailable')),patch.object(client,'_request',return_value=FetchResult('https://example.com/',200,{},b'')):
            self.assertIsNone(client.fetch('https://example.com/').robots_allowed)

    def test_parallel_frontier_is_bounded_breadth_first_and_thread_safe(self):
        owner=threading.get_ident()
        active=peak=0
        lock=threading.Lock()
        calls=[]
        def permission():
            self.assertEqual(threading.get_ident(),owner)
        client=SafeClient(['example.com'],check_access=permission,concurrency=8,delay=.05)
        def fetch(url):
            nonlocal active,peak
            with lock:
                active+=1;peak=max(peak,active);calls.append(url)
            time.sleep(.03)
            n=int(url.rsplit('/',1)[1])
            body=(''.join(f'<a href="/{i}">next</a>' for i in range(1,33)) if n==0 else '').encode()
            with lock: active-=1
            return FetchResult(url,200,{'content-type':'text/html'},body)
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory),patch.object(client,'fetch',side_effect=fetch):
            with ScanWork('parallel') as work:
                rows,_=crawl(client,url='https://example.com/0',max_urls=33,max_depth=1,work=work)
                self.assertEqual(len(rows),33)
                self.assertEqual(len(set(calls)),33)
                self.assertEqual({row['data']['depth'] for row in rows},{0,1})
                self.assertGreater(peak,1)
                self.assertLessEqual(peak,8)
                self.assertTrue(all(row['data']['is_indexable'] for row in rows))

    def test_overload_is_retried_without_duplicate_records(self):
        client=SafeClient(['example.com'],check_access=lambda:None)
        responses=[FetchResult('https://example.com/',429,{},b''),FetchResult('https://example.com/',200,{'content-type':'text/html'},b'OK')]
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory),patch.object(client,'fetch',side_effect=responses):
            with ScanWork('retry') as work:
                rows,warnings=crawl(client,url='https://example.com/',max_urls=1,max_depth=1,work=work)
                self.assertEqual(len(rows),1)
                self.assertEqual(next(iter(rows))['data']['status'],200)
                self.assertTrue(any('tempo verlaagd' in warning for warning in warnings))

    def test_parallel_pause_resumes_unique_frontier_results(self):
        def response(url):
            time.sleep(.02)
            number=int(url.rsplit('/',1)[-1])
            body=(''.join(f'<a href="/{i}">next</a>' for i in range(1,33)) if number==0 else '').encode()
            return FetchResult(url,200,{'content-type':'text/html'},body)
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory):
            first=SafeClient(['example.com'],check_access=lambda:None,concurrency=4,slice_seconds=.07)
            with patch.object(first,'fetch',side_effect=response),ScanWork('paused') as work:
                with self.assertRaises(ScanPause):
                    crawl(first,url='https://example.com/0',max_urls=33,max_depth=1,work=work)
                self.assertLess(len(work.records),33)
            second=SafeClient(['example.com'],check_access=lambda:None,concurrency=8)
            with patch.object(second,'fetch',side_effect=response),ScanWork('paused') as work:
                rows,_=crawl(second,url='https://example.com/0',max_urls=33,max_depth=1,work=work)
                self.assertEqual(len(rows),33)
                self.assertEqual(len({row['data']['url'] for row in rows}),33)
                self.assertEqual(len({row['locator'] for row in rows}),33)

    @patch('scans.network.resolve_public',return_value='93.184.216.34')
    @patch('scans.network.urllib3.HTTPSConnectionPool')
    def test_connection_reused_but_dns_revalidated(self,pool,dns):
        response=pool.return_value.urlopen.return_value
        response.status=200;response.headers={};response.read.return_value=b''
        client=SafeClient(['example.com'],check_access=lambda:None)
        client._request('https://example.com/one',0)
        client._request('https://example.com/two',0)
        self.assertEqual(pool.call_count,1)
        self.assertEqual(dns.call_count,2)
        client.close()

    @patch('scans.network.resolve_public',return_value='93.184.216.34')
    @patch('scans.network.urllib3.HTTPSConnectionPool')
    def test_overload_sets_shared_cooldown_and_lowers_rate(self,pool,dns):
        from unittest.mock import MagicMock
        response=pool.return_value.urlopen.return_value
        response.status=429;response.headers={'Retry-After':'7'};response.read.return_value=b''
        client=SafeClient(['example.com'],check_access=lambda:None,concurrency=8,delay=.1)
        client.throttle=MagicMock()
        with patch.object(client,'_pace'):
            client._request('https://example.com/',0)
        self.assertGreaterEqual(client.delay,.5)
        self.assertGreater(client.cooldown['example.com'],time.monotonic()+6)
        client.throttle.cooldown.assert_called_once_with('example.com',7)
        client.close()

    def test_robots_delay_survives_slice_changes_without_restarting_wait(self):
        from unittest.mock import MagicMock
        cache={'https://example.com':{'status':200,'body':'User-agent: *\nCrawl-delay: 300\nAllow: /\n'}}
        client=SafeClient(['example.com'],check_access=lambda:None,robot_cache=cache)
        client.throttle=MagicMock()
        parser,delay=client._robot_policy('https://example.com/')
        self.assertEqual(delay,300)
        self.assertNotIn('example.com',client.last_request)
        client.throttle.cooldown.assert_not_called()

    def test_initial_robots_delay_sets_shared_cooldown(self):
        from unittest.mock import MagicMock
        client=SafeClient(['example.com'],check_access=lambda:None)
        client.throttle=MagicMock()
        with patch.object(client,'_request',return_value=FetchResult('https://example.com/robots.txt',200,{},b'User-agent: *\nCrawl-delay: 300\nAllow: /\n')):
            client._robot_policy('https://example.com/')
        client.throttle.cooldown.assert_called_once_with('example.com',300)
