import gzip
import tempfile
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase, override_settings
from .network import FetchResult, ScanBudgetExceeded
from .runners import crawl
from .work import ScanWork, ScanPause


class FixtureClient:
    allowed_hosts={'example.com'}
    discover_sitemaps=True
    concurrency=1
    robot_cache={}
    host_discovery=None
    respect_robots=False
    def __init__(self,pages):self.pages=pages;self.calls=[];self.pause=None
    def guard(self):pass
    def fetch(self,url,**kwargs):
        self.calls.append(url)
        if self.pause and self.pause(url):raise ScanPause
        value=self.pages.get(url,(404,b''))
        if isinstance(value,Exception):raise value
        status,body=value
        return FetchResult(url,status,{'content-type':'text/html' if not url.endswith('.xml') else 'application/xml'},body)


def urlset(*urls):
    return ('<urlset>'+''.join('<url><loc>'+url+'</loc></url>' for url in urls)+'</urlset>').encode()


class SitemapSeedTests(SimpleTestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        settings=override_settings(MEDIA_ROOT=directory.name);settings.enable();self.addCleanup(settings.disable)

    def run_crawl(self,client,key='seed',**kwargs):
        with ScanWork(key) as work:
            records,warnings=crawl(client,url='https://example.com/',max_urls=kwargs.get('max_urls',100),max_depth=kwargs.get('max_depth',5),work=work)
            return list(records),warnings,work.get('seed_maps_done'),work.get('seed_maps_truncated',False)

    def test_sitemap_and_links_share_queue_preserve_sources_and_real_depth(self):
        client=FixtureClient({'https://example.com/':(200,b'<a href="/linked">linked</a>'),
            'https://example.com/robots.txt':(200,b'User-agent: *\nSitemap: https://example.com/maps.xml\n'),
            'https://example.com/maps.xml':(200,urlset('https://example.com/','https://example.com/both','https://example.com/orphan','https://example.com/both#duplicate','http://127.0.0.1/private')),
            'https://example.com/linked':(200,b'<a href="/both">both</a>'),
            'https://example.com/both':(200,b'page'),
            'https://example.com/orphan':(200,b'<a href="/child">child</a>'),
            'https://example.com/child':(200,b'child')})
        records,warnings,done,_=self.run_crawl(client)
        pages={r['data']['url']:r['data'] for r in records}
        self.assertEqual(len(pages),5)
        self.assertEqual(pages['https://example.com/both']['discovery_sources'],['sitemap','internal_link'])
        self.assertEqual(pages['https://example.com/both']['depth'],2)
        self.assertEqual(pages['https://example.com/orphan']['discovery_sources'],['sitemap'])
        self.assertIsNone(pages['https://example.com/orphan']['depth'])
        self.assertIsNone(pages['https://example.com/child']['depth'])
        self.assertEqual(pages['https://example.com/child']['discovery_sources'],['internal_link'])
        self.assertTrue(done)
        self.assertNotIn('http://127.0.0.1/private',client.calls)
        self.assertEqual(client.calls.count('https://example.com/both'),1)
        self.assertTrue(any('toegestane' in warning for warning in warnings))

    def test_nested_index_cycle_and_compressed_sitemap(self):
        index=b'<sitemapindex><sitemap><loc>https://example.com/child.xml</loc></sitemap><sitemap><loc>https://example.com/maps.xml</loc></sitemap></sitemapindex>'
        client=FixtureClient({'https://example.com/':(200,b'home'),
            'https://example.com/robots.txt':(200,b'Sitemap: https://example.com/maps.xml'),
            'https://example.com/maps.xml':(200,index),
            'https://example.com/child.xml':(200,gzip.compress(urlset('https://example.com/hidden'))),
            'https://example.com/hidden':(200,b'hidden')})
        records,_,done,_=self.run_crawl(client)
        self.assertTrue(done);self.assertEqual(len(records),2)
        self.assertEqual(client.calls.count('https://example.com/maps.xml'),1)

    def test_missing_or_invalid_sitemap_does_not_stop_link_crawl(self):
        client=FixtureClient({'https://example.com/':(200,b'<a href="/next">next</a>'),
            'https://example.com/sitemap.xml':(200,b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///secret">]><urlset>&e;</urlset>'),
            'https://example.com/next':(200,b'next')})
        records,warnings,done,_=self.run_crawl(client)
        self.assertEqual(len(records),2);self.assertTrue(done)
        self.assertTrue(any('niet bruikbaar' in warning for warning in warnings))

    def test_crawl_resumes_sitemap_phase_without_duplicate_page_requests(self):
        client=FixtureClient({'https://example.com/':(200,b'home'),
            'https://example.com/robots.txt':(200,b'Sitemap: https://example.com/maps.xml'),
            'https://example.com/maps.xml':(200,urlset('https://example.com/hidden')),
            'https://example.com/hidden':(200,b'hidden')})
        client.pause=lambda url:url.endswith('maps.xml')
        with self.assertRaises(ScanPause):self.run_crawl(client,'resume')
        client.pause=None
        records,_,done,_=self.run_crawl(client,'resume')
        self.assertTrue(done);self.assertEqual(len(records),2)
        self.assertEqual(client.calls.count('https://example.com/'),1)
        self.assertEqual(client.calls.count('https://example.com/robots.txt'),1)

    def test_sitemap_pages_use_page_limit_and_depth_limit_without_fake_depth(self):
        client=FixtureClient({'https://example.com/':(200,b'home'),
            'https://example.com/robots.txt':(200,b'Sitemap: https://example.com/maps.xml'),
            'https://example.com/maps.xml':(200,urlset(*['https://example.com/'+str(i) for i in range(30)]))})
        records,warnings,_,truncated=self.run_crawl(client,max_urls=2,max_depth=0)
        self.assertEqual(len(records),2);self.assertTrue(truncated)
        self.assertIsNone(records[1]['data']['depth'])
        self.assertTrue(any('limiet' in warning for warning in warnings))

    def test_budget_expiry_keeps_first_page_and_unknown_discovery(self):
        client=FixtureClient({'https://example.com/':(200,b'home'),
            'https://example.com/robots.txt':ScanBudgetExceeded('budget')})
        records,_,done,_=self.run_crawl(client)
        self.assertEqual(len(records),1);self.assertFalse(done)
        self.assertEqual(records[0]['data']['depth'],0)

    def test_finalization_tolerates_expired_network_budget(self):
        client=FixtureClient({'https://example.com/':(200,b'home')})
        with ScanWork('expired') as work:
            from .seeding import initialize,origin,finalize
            initialize(work);origin(work,'https://example.com/','start')
            work.records.append({'data':{'url':'https://example.com/','depth':0}})
            with patch.object(client,'guard',side_effect=ScanBudgetExceeded('expired')):
                finalize(client,work,'https://example.com/',10)
            self.assertTrue(work.get('provenance_done'))

    def test_root_link_is_remembered_when_sitemap_adds_target_after_depth_boundary(self):
        client=FixtureClient({'https://example.com/':(200,b'<a href="/target">target</a>'),
            'https://example.com/robots.txt':(200,b'Sitemap: https://example.com/maps.xml'),
            'https://example.com/maps.xml':(200,urlset('https://example.com/target')),
            'https://example.com/target':(200,b'target')})
        records,_,_,_=self.run_crawl(client,max_depth=0)
        self.assertEqual(records[1]['data']['depth'],1)
        self.assertEqual(records[1]['data']['discovery_sources'],['sitemap','internal_link'])


class SitemapPublicationTests(TestCase):
    def test_task_publishes_sitemap_only_pages_coverage_and_measurements(self):
        from django.contrib.auth import get_user_model
        from projects.models import Project,Membership
        from sources.models import ImportBatch
        from .models import ScanTarget
        from .tasks import process_scan
        user=get_user_model().objects.create_user('sitemap-publication')
        project=Project.objects.create(name='Sitemaps',website='https://example.com/',created_by=user)
        Membership.objects.create(project=project,user=user,role='admin')
        target=ScanTarget.objects.create(project=project,created_by=user,name='Site',url=project.website,allowed_hosts=['example.com'])
        batch=ImportBatch.objects.create(project=project,created_by=user,scan_target=target,source='semscan',method='scan',label='Crawl',request_metadata={'scan':'crawl','url':project.website,'allowed_hosts':['example.com'],'discover_sitemaps':True,'duration':120,'max_urls':100,'max_depth':5})
        client=FixtureClient({'https://example.com/':(200,b'<title>Home</title>'),
            'https://example.com/robots.txt':(200,b'Sitemap: https://example.com/maps.xml'),
            'https://example.com/maps.xml':(200,urlset('https://example.com/hidden')),
            'https://example.com/hidden':(200,b'<title>Hidden</title>')})
        client.transferred=client.decoded_bytes=0
        client.close=lambda:None
        def factory(*args,**kwargs):
            client.documents=kwargs['document_sink']
            return client
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory),patch('scans.tasks.SafeClient',side_effect=factory):
            process_scan(str(batch.pk))
        batch.refresh_from_db()
        self.assertEqual(batch.status,'complete',batch.error)
        self.assertEqual(batch.record_count,2)
        self.assertEqual(batch.request_metadata['sitemap_discovery']['sitemap_only'],1)
        self.assertEqual(batch.request_metadata['sitemap_discovery']['visited_sitemap_urls'],1)
        page=batch.page_measurements.get(source_record__data__url='https://example.com/hidden')
        self.assertIsNone(page.depth)
        self.assertEqual(page.facts['discovery_sources'],['sitemap'])
        observation=batch.url_observations.get(source_record=page.source_record)
        self.assertEqual(observation.facts['discovery_sources'],['sitemap'])
        self.client.force_login(user)
        from django.urls import reverse
        self.assertContains(self.client.get(reverse('ui:batch',args=[project.pk,batch.pk])),'Sitemapontdekking binnen de webcrawl')
