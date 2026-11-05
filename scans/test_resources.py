import gzip
import hashlib
import io
import json
import tempfile
import uuid
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase,TestCase,override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from projects.models import Project,Membership
from sources.models import ImportBatch
from sources.services import publish_records
from .extraction import extract_page
from .models import ScanTarget,ResourceMeasurement,ResourceReference
from .network import FetchResult,ScanError,normalize_url
from .resource_transfer import read_body
from .resource_discovery import css_urls,srcset_urls
from .resources import ResourceClient,run,initialize,publish
from .work import ScanWork,ScanPause


def extracted(html,url='https://example.com/'):
    return extract_page(SimpleNamespace(url=url,status=200,headers={'content-type':'text/html'},body=html.encode()))[0]


def response(url,body=b'',status=200,mime='text/css'):
    return FetchResult(url,status,{'content-type':mime,'content-length':str(len(body))},body,
        measurements={'wire_bytes':len(body),'decoded_bytes':len(body),'declared_bytes':len(body),'wire_complete':True,'body_complete':True,'body_truncated':False})


class ResourceParsingTests(SimpleTestCase):
    def test_html_candidates_include_srcset_lazy_css_and_downloads(self):
        fields=extracted('<link rel="stylesheet" href="/main.css"><img src="a.jpg" srcset="b.jpg 1x, c.jpg 2x" data-src="lazy.jpg"><style>@import "more.css"; div {background:url(bg.png)}</style><a href="/doc.pdf">PDF</a>')
        urls={r['url'] for r in fields['resource_candidates']}
        self.assertEqual(urls,{'https://example.com/'+p for p in ('main.css','a.jpg','b.jpg','c.jpg','lazy.jpg','more.css','bg.png','doc.pdf')})
        self.assertTrue(fields['resource_discovery']['complete'])

    def test_css_comments_strings_and_data_are_not_false_http_targets(self):
        refs,complete=css_urls('/* url(fake.png) */ .a {content:"url(fake2.png)";background:url("real.png")} @import url("next.css"); @font-face{src:url(font.woff2)}')
        self.assertTrue(complete)
        self.assertEqual([r['raw_url'] for r in refs],['real.png','next.css','font.woff2'])
        self.assertEqual([r['kind'] for r in refs],['css_asset','stylesheet','font'])
        self.assertEqual(list(srcset_urls('data:image/png;base64,AAAA 1x, real.png 2x')),['data:image/png;base64,AAAA','real.png'])

    def test_css_limits_and_escapes_are_explicit(self):
        refs,complete=css_urls(r'@import "\6d ain.css"; a{background:url("a.png")}',limit=1)
        self.assertEqual(refs[0]['raw_url'],'main.css');self.assertFalse(complete)
        self.assertFalse(css_urls('a{background:url(unclosed')[1])

    def read(self,body,limit=1024,headers=None,budget=4096):
        client=SimpleNamespace(resource_body_limit=limit,max_transfer=budget,transferred=0,decoded_bytes=0,guard=lambda:None)
        source=io.BytesIO(body)
        result,measure=read_body(client,SimpleNamespace(read=lambda n,decode_content=False:source.read(n)),headers or {})
        return result,measure,client

    def test_encoded_decoded_and_declared_sizes_are_distinct(self):
        raw=gzip.compress(b'A'*500)
        body,measure,_=self.read(raw,headers={'content-encoding':'gzip','content-length':str(len(raw))})
        self.assertEqual(body,b'A'*500);self.assertEqual(measure['wire_bytes'],len(raw));self.assertEqual(measure['decoded_bytes'],500)
        self.assertTrue(measure['body_complete'])

    def test_partial_file_and_gzip_bomb_never_claim_full_size(self):
        body,measure,client=self.read(b'A'*2000,limit=1000,headers={'content-length':'2000'})
        self.assertEqual(len(body),1000);self.assertFalse(measure['wire_complete']);self.assertTrue(measure['body_truncated'])
        body,measure,_=self.read(gzip.compress(b'A'*100000),limit=1024,headers={'content-encoding':'gzip'})
        self.assertEqual(len(body),1024);self.assertFalse(measure['body_complete'])
        self.assertEqual(measure['decoded_bytes'],1024)

    def test_exact_global_budget_is_not_exceeded(self):
        _,measure,client=self.read(b'A'*5000,limit=5000,budget=2000)
        self.assertEqual(client.transferred,2000);self.assertFalse(measure['wire_complete'])

    def test_resource_policy_is_separate_from_navigation_and_blocks_redirects(self):
        config={'allowed_hosts':['example.com'],'resource_hosts':['cdn.example.org'],'blocked_resource_hosts':['bad.example.org']}
        client=ResourceClient(config,'resource',check_access=lambda:None,respect_robots=False)
        try:
            self.assertTrue(client.permitted('https://cdn.example.org/a.js'))
            self.assertFalse(client.permitted('https://bad.example.org/a.js'))
            with self.assertRaises(ScanError):normalize_url('https://cdn.example.org/',config['allowed_hosts'])
            with patch.object(client,'_request',return_value=FetchResult('https://example.com/a',302,{'location':'http://127.0.0.1/private'},b'')) as fetch:
                result=client.fetch('https://example.com/a',enforce_robots=False)
            self.assertTrue(result.blocked_redirect);self.assertEqual(fetch.call_count,1)
        finally:client.close()

    def test_external_permission_requires_opt_in_and_does_not_expand_site(self):
        client=ResourceClient({'check_external_links':False},'external',check_access=lambda:None)
        self.assertFalse(client.permitted('https://other.example/a'))
        client.config.update(check_external_links=True,blocked_external_hosts=['other.example'])
        self.assertFalse(client.permitted('https://other.example/a'));self.assertTrue(client.permitted('https://public.example/a'))
        client.close()


class ResourceQueueTests(SimpleTestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.override=override_settings(MEDIA_ROOT=self.directory.name);self.override.enable()
        self.batch=SimpleNamespace(pk=uuid.uuid4())
        self.config={'duration':600,'allowed_hosts':['example.com'],'check_resources':True,'check_external_links':True,'requests_per_second':15,'respect_robots':False}
    def tearDown(self):self.override.disable();self.directory.cleanup()
    def seed(self,work,html,path='/'):
        fields=extracted(html,'https://example.com'+path)
        work.records.append({'data':{'url':'https://example.com'+path,'final_status':200,**fields}})

    def test_shared_file_fetches_once_and_external_page_is_not_expanded(self):
        with ScanWork(self.batch.pk) as work:
            for path in ('/a','/b'):self.seed(work,'<script src="/shared.js"></script><a href="https://other.example/">Other</a>',path)
            def fetch(client,url):return response(url,b'<a href="https://third.example/">X</a>',mime='text/html' if client.purpose=='external' else 'text/javascript')
            with patch.object(ResourceClient,'fetch',autospec=True,side_effect=fetch) as call:run(self.batch,work,self.config,lambda:None)
            self.assertEqual(call.call_count,2)
            self.assertEqual(work.connection.execute('SELECT count(*) FROM resource_refs').fetchone()[0],4)
            self.assertEqual(work.connection.execute('SELECT count(*) FROM resource_targets').fetchone()[0],2)

    def test_sdk_errors_are_discovered_and_linked_to_calculator(self):
        with ScanWork(self.batch.pk) as work:
            self.seed(work,''.join('<script src="/_sdk/'+name+'.js"></script>' for name in ('a','b','c','d')),'/calculator/')
            with patch.object(ResourceClient,'fetch',side_effect=lambda url:response(url,b'<html>Not found</html>',404,'text/html')):
                run(self.batch,work,self.config,lambda:None)
            rows=work.connection.execute('SELECT result FROM resource_targets').fetchall()
            self.assertEqual(len(rows),4);self.assertTrue(all(json.loads(r[0])['http_status']==404 for r in rows))
            self.assertTrue(all(json.loads(r[0])['source_url']=='https://example.com/calculator/' for r in work.connection.execute('SELECT payload FROM resource_refs')))

    def test_checkpoint_resume_does_not_repeat_completed_or_ambiguous_requests(self):
        with ScanWork(self.batch.pk) as work:
            self.seed(work,'<script src="/a.js"></script><script src="/b.js"></script>')
            with patch.object(ResourceClient,'fetch',side_effect=[response('https://example.com/a.js',mime='text/javascript'),ScanPause()]):
                with self.assertRaises(ScanPause):run(self.batch,work,self.config,lambda:None)
        with ScanWork(self.batch.pk) as work:
            with patch.object(ResourceClient,'fetch') as fetch:run(self.batch,work,self.config,lambda:None)
            fetch.assert_not_called()
            outcomes=[json.loads(r[0])['outcome'] for r in work.connection.execute('SELECT result FROM resource_targets')]
            self.assertEqual(outcomes,['success','interrupted_unknown'])

    def test_target_budget_retains_unmeasured_targets(self):
        with ScanWork(self.batch.pk) as work:
            self.seed(work,'<img src="/a.png"><img src="/b.png">')
            with patch.object(ResourceClient,'fetch',return_value=response('https://example.com/a.png',mime='image/png')) as fetch:
                run(self.batch,work,{**self.config,'resource_max_targets':1},lambda:None)
            self.assertEqual(fetch.call_count,1)
            self.assertIn('not_measured_budget',[json.loads(r[0])['outcome'] for r in work.connection.execute('SELECT result FROM resource_targets')])

    def test_css_origin_propagation_after_stylesheet_already_fetched(self):
        with ScanWork(self.batch.pk) as work:
            self.seed(work,'<link rel="stylesheet" href="/b.css">','/first')
            self.seed(work,'<link rel="stylesheet" href="/a.css">','/second')
            bodies={'https://example.com/b.css':b'body{background:url(bg.png)}','https://example.com/a.css':b'@import "b.css";','https://example.com/bg.png':b'png'}
            with patch.object(ResourceClient,'fetch',side_effect=lambda url:response(url,bodies[url],mime='image/png' if url.endswith('.png') else 'text/css')) as fetch:run(self.batch,work,self.config,lambda:None)
            self.assertEqual(fetch.call_count,3)
            rows=work.connection.execute("SELECT r.payload FROM resource_refs r JOIN resource_targets t ON t.key=r.target_key WHERE t.url='https://example.com/bg.png'").fetchall()
            self.assertEqual({json.loads(r[0])['source_url'] for r in rows},{'https://example.com/first','https://example.com/second'})


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'},'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'}})
class ResourcePublishingTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('resource-owner')
        self.project=Project.objects.create(name='Resources',website='https://example.com/',created_by=self.user)
        Membership.objects.create(project=self.project,user=self.user,role='admin')
        self.batch=ImportBatch.objects.create(project=self.project,created_by=self.user,source='semscan',method='scan',request_metadata={'scan':'crawl','resource_version':'resources-v1'})
        records=[]
        for path in ('/a','/b'):
            url='https://example.com'+path;fields=extracted('<h1>Title</h1><script src="/bad.js"></script>',url)
            records.append({'locator':path,'data':{'url':url,'status':200,'final_status':200,'content_type':'text/html','measured_at':timezone.now().isoformat(),**fields}})
        publish_records(batch_id=self.batch.pk,records=records,derive_assessment=False)
    def target(self,path='/bad.js',status=404,purpose='resource',**kw):
        url='https://example.com'+path
        target=ResourceMeasurement.objects.create(batch=self.batch,purpose=purpose,url=url,url_hash=hashlib.sha256(url.encode()).hexdigest(),http_status=status,
            outcome='blocked' if status==403 else 'http_error' if status>=400 else 'success',headers={'content-type':'text/html'},measured_at=timezone.now(),**kw)
        for path in ('/a','/b'):ResourceReference.objects.create(target=target,reference_hash=path,source_url='https://example.com'+path,kind='script',element_path='/html[1]/script[1]')
        return target

    def test_one_shared_error_all_references_and_page_denominator_unchanged(self):
        from assessments.services import rebuild_batch
        from assessments.models import Finding
        target=self.target()
        run=rebuild_batch(self.batch.pk)
        finding=Finding.objects.get(result__run=run,rule='resource_http_error')
        self.assertEqual(finding.details['count'],2);self.assertEqual(finding.evidence.count(),2)
        self.assertEqual(run.results.count(),2)
        self.assertEqual(run.coverage['resource_checks']['resource_http_error']['issue'],1)
        self.client.force_login(self.user)
        detail=self.client.get(reverse('projects:scans:resource',args=[self.project.pk,self.batch.pk,target.pk]))
        self.assertContains(detail,'https://example.com/a');self.assertContains(detail,'https://example.com/b')
        response=self.client.get(reverse('projects:scans:resources',args=[self.project.pk,self.batch.pk])+'?download=1')
        exported=b''.join(response.streaming_content).decode();self.assertIn('reference',exported);self.assertIn('404',exported)
        self.assertEqual(self.client.get(reverse('ui:assessments:finding',args=[self.project.pk,finding.pk])).status_code,200)

    def test_403_is_unknown_and_never_a_broken_visitor_link(self):
        from assessments.r4 import checks
        self.assertEqual(checks(self.target('/blocked',403,'external'),102400)['external_http_error'],'unknown')

    def test_mime_mismatch_and_partial_image_size(self):
        from assessments.r4 import checks
        target=self.target('/script',200)
        self.assertEqual(checks(target,100)['resource_mime_mismatch'],'issue')
        target.references.update(kind='image');target.headers={'content-type':'image/png'};target.measurements={'wire_bytes':200,'wire_complete':False}
        self.assertEqual(checks(target,100)['resource_image_large'],'issue')
        self.assertEqual(checks(target,300)['resource_image_large'],'unknown')

    def test_resource_pages_are_project_scoped(self):
        target=self.target();other=get_user_model().objects.create_user('resource-other');self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('projects:scans:resource',args=[self.project.pk,self.batch.pk,target.pk])).status_code,404)

    def test_new_resource_checks_do_not_change_score_catalog(self):
        from reporting.catalog import EXPECTED
        from assessments.r4 import RULES
        self.assertFalse(set(RULES).intersection(set().union(*EXPECTED.values())))

    def test_public_dns_is_revalidated_after_each_resource_redirect(self):
        from unittest.mock import MagicMock
        from .resources import result_for
        fake=MagicMock(status=302,headers={'location':'https://other.example/a','content-length':'0'})
        fake.read.return_value=b''
        config={'allowed_hosts':['example.com','other.example']}
        client=ResourceClient(config,'resource',check_access=lambda:None,respect_robots=False)
        # Avoid robots traffic here so this assertion concerns the two explicit redirect hops.
        client._robot_policy=lambda url:(_ for _ in ()).throw(ScanError('robots unavailable'))
        try:
            with patch('scans.network.resolve_public',side_effect=['93.184.216.34',ScanError('private DNS')]) as resolve,patch('scans.network.urllib3.HTTPSConnectionPool') as pool:
                pool.return_value.urlopen.return_value=fake
                result,_=result_for(client,'https://example.com/start')
            self.assertEqual(resolve.call_count,2)
            self.assertEqual(pool.return_value.urlopen.call_count,1)
            self.assertEqual(result['outcome'],'download_error')
            self.assertEqual(result['evidence']['error_code'],'unsafe_dns')
        finally:client.close()

    def test_published_resource_inventory_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory):
            with ScanWork(self.batch.pk) as work:
                work.records.append({'data':{'url':'https://example.com/a',**extracted('<script src="/bad.js"></script>')}})
                config={'duration':600,'allowed_hosts':['example.com'],'check_resources':True,'respect_robots':False}
                with patch.object(ResourceClient,'fetch',return_value=response('https://example.com/bad.js',b'404',404,'text/html')):run(self.batch,work,config,lambda:None)
                publish(self.batch,work);publish(self.batch,work)
        self.assertEqual(self.batch.resource_measurements.count(),1)
        self.assertEqual(ResourceReference.objects.filter(target__batch=self.batch).count(),1)

    def test_real_scan_task_publishes_assets_with_separate_page_count(self):
        from .tasks import process_scan
        from assessments.models import Finding
        target=ScanTarget.objects.create(project=self.project,name='Task resources',url='https://example.com/',created_by=self.user)
        config={'scan':'crawl','url':target.url,'allowed_hosts':['example.com'],'max_urls':10,'max_depth':2,'duration':600,
                'resource_version':'resources-v1','check_resources':True,'check_external_links':True,'respect_robots':False}
        batch=ImportBatch.objects.create(project=self.project,created_by=self.user,scan_target=target,source='semscan',method='scan',request_metadata=config)
        html=b'<html><head><title>Task</title></head><body><script src="/bad.js"></script><img src="/big.png"><a href="https://other.example/">External</a></body></html>'
        def asset(client,url):
            return response(url,b'403',403,'text/html') if client.purpose=='external' else response(url,b'404',404,'text/html') if url.endswith('.js') else response(url,b'x'*120000,200,'image/png')
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory),patch('scans.network.SafeClient.fetch',return_value=FetchResult(target.url,200,{'content-type':'text/html'},html)),patch.object(ResourceClient,'fetch',autospec=True,side_effect=asset):
            process_scan(str(batch.pk))
        batch.refresh_from_db();self.assertEqual(batch.status,'complete',batch.error)
        self.assertEqual(batch.record_count,1)
        self.assertEqual(batch.resource_measurements.count(),3)
        run=batch.analysis_runs.latest('started_at')
        self.assertEqual(run.results.count(),1)
        self.assertEqual(Finding.objects.filter(result__run=run,rule='resource_http_error').count(),1)
        self.assertEqual(Finding.objects.filter(result__run=run,rule='resource_image_large').count(),1)
        self.assertFalse(Finding.objects.filter(result__run=run,rule='external_http_error').exists())
        self.assertEqual(run.coverage['resource_checks']['external_http_error']['unknown'],1)

    def test_page_phase_is_not_repeated_after_resource_pause(self):
        from .tasks import process_scan
        target=ScanTarget.objects.create(project=self.project,name='Resume',url='https://example.com/',created_by=self.user)
        config={'scan':'crawl','url':target.url,'allowed_hosts':['example.com'],'max_urls':10,'max_depth':2,'duration':600,
                'resource_version':'resources-v1','check_resources':True,'respect_robots':False}
        batch=ImportBatch.objects.create(project=self.project,created_by=self.user,scan_target=target,source='semscan',method='scan',request_metadata=config)
        html=b'<script src="/a.js"></script><script src="/b.js"></script>'
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory),patch('scans.network.SafeClient.fetch',return_value=FetchResult(target.url,200,{'content-type':'text/html'},html)) as page_fetch,patch.object(ResourceClient,'fetch',side_effect=[response('https://example.com/a.js',mime='text/javascript'),ScanPause()]) as asset_fetch,patch('scans.tasks.process_scan.apply_async'):
            process_scan(str(batch.pk));process_scan(str(batch.pk))
        self.assertEqual(page_fetch.call_count,1);self.assertEqual(asset_fetch.call_count,2)
        batch.refresh_from_db();self.assertEqual(batch.status,'complete',batch.error)
        self.assertTrue(batch.resource_measurements.filter(outcome='interrupted_unknown').exists())

    def test_sitemap_file_is_measured_separately_from_page_budget(self):
        from .test_seeding import FixtureClient,urlset
        from .runners import crawl
        from .coverage import publish_coverage
        client=FixtureClient({'https://example.com/':(200,b'<h1>Root</h1>'),
            'https://example.com/robots.txt':(200,b'Sitemap: https://example.com/map.xml'),
            'https://example.com/map.xml':(200,urlset('https://example.com/','https://example.com/document.pdf'))})
        client.separate_resources=True
        config={'duration':600,'allowed_hosts':['example.com'],'check_resources':True,'respect_robots':False}
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory):
            with ScanWork(self.batch.pk) as work:
                records,_=crawl(client,url='https://example.com/',max_urls=10,max_depth=2,work=work)
                self.assertEqual(len(records),1)
                self.assertNotIn('https://example.com/document.pdf',client.calls)
                with patch.object(ResourceClient,'fetch',return_value=response('https://example.com/document.pdf',b'PDF',200,'application/pdf')):run(self.batch,work,config,lambda:None)
                publish(self.batch,work);publish_coverage(self.batch,work)
        coverage=self.batch.crawl_url_coverage.get(url='https://example.com/document.pdf')
        self.assertEqual(coverage.outcome,'resource_only')
        self.assertFalse(coverage.attempted)

    def test_report_freezes_all_shared_sources_and_measurement_without_extra_pages(self):
        from assessments.services import rebuild_batch
        from reporting.services import create_dossier,assemble
        target=self.target()
        target.measurements={'wire_bytes':123,'wire_complete':True};target.save(update_fields=['measurements'])
        rebuild_batch(self.batch.pk)
        with patch('reporting.services.enqueue'):dossier=create_dossier(self.project,self.user,self.batch.pk)
        assemble(dossier.pk)
        group=dossier.groups.get(rule='resource_http_error')
        self.assertEqual(group.affected_urls,2);self.assertEqual(group.occurrences,2)
        self.assertEqual(dossier.pages.count(),2)
        self.assertEqual(set(group.evidence.values_list('url',flat=True)),{'https://example.com/a','https://example.com/b'})
        self.assertTrue(all(item.snapshot['resource']['measurements']['wire_bytes']==123 for item in group.evidence.all()))

    def test_resource_action_requires_a_new_measured_target_pass(self):
        from datetime import timedelta
        from assessments.services import rebuild_batch
        from reporting.services import create_dossier,assemble
        from reporting.actions import create_action,verify_action
        from reporting.models import DossierResource
        target=self.target()
        rebuild_batch(self.batch.pk)
        with patch('reporting.services.enqueue'):old=create_dossier(self.project,self.user,self.batch.pk)
        assemble(old.pk)
        group=old.groups.get(rule='resource_http_error')
        action=create_action(self.project,self.user,[group],{'title':'Fix shared script','acceptance':'The resource must respond successfully.'})
        # Model a later dossier with no content pages: a new measured target pass is the required evidence.
        from reporting.models import Dossier,DossierInput
        later=Dossier.objects.create(project=self.project,actor=self.user,crawl=self.batch,status='complete',title='Target confirmation')
        old_input=old.inputs.first()
        DossierInput.objects.create(dossier=later,run=old_input.run,batch=self.batch,kind='crawl',snapshot=old_input.snapshot)
        frozen=old.resources.get(measurement=target)
        confirmation=DossierResource.objects.create(dossier=later,url=frozen.url,url_hash=frozen.url_hash,purpose='resource',
            measured_at=frozen.measured_at+timedelta(seconds=1),checks={'resource_http_error':'unknown'},snapshot=frozen.snapshot)
        self.assertNotEqual(verify_action(action.pk,later.pk,self.user,True).status,'resolved')
        confirmation.checks={'resource_http_error':'pass'};confirmation.save(update_fields=['checks'])
        self.assertEqual(verify_action(action.pk,later.pk,self.user,True).status,'resolved')
