import copy
import hashlib
from unittest.mock import patch
from django.test import SimpleTestCase,TestCase,override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from projects.models import Project,Membership
from sources.models import ImportBatch
from sources.services import publish_records
from scans.models import ScanTarget
from .models import BrowserRun,DiscoveredLink
from .forms import BrowserForm
from .sampling import select_pages
from .services import create_run,store_visit,finalize
from .workflow import schedule_after_crawl
from .tests import response


class R5SampleTests(SimpleTestCase):
    def test_sample_preserves_paths_and_includes_uncertain_interactive_candidate(self):
        rows=[{'final_url':'https://example.com/'+path,'facts':{'word_count':500,'h1':['Heading']}} for path in ('','news/a','news/b','team/a','team/b')]
        rows.append({'final_url':'https://example.com/tools/calculator','facts':{'word_count':0,'h1':[],'resource_urls':['/_next/x.js']}})
        selected,reasons=select_pages(rows,['example.com'],4)
        self.assertEqual(len(selected),4);self.assertIn('https://example.com/tools/calculator',selected)
        self.assertTrue(any('weinig tekst in server-HTML' in row['reasons'] for row in reasons))
        self.assertEqual((selected,reasons),select_pages(rows,['example.com'],4))

    def test_multiple_priority_candidates_are_retained_together(self):
        rows=[{'final_url':'https://example.com/'+str(i),'facts':{'word_count':500}} for i in range(10)]
        rows.extend({'final_url':'https://example.com/tools/'+name,'facts':{'word_count':0,'h1':[],'resource_urls':['/_next/x.js']}} for name in ('a','b'))
        selected,_=select_pages(rows,['example.com'],6)
        self.assertIn('https://example.com/tools/a',selected);self.assertIn('https://example.com/tools/b',selected)
        self.assertEqual(len(selected),6)

    def test_form_reserves_within_total_and_bounds_exploration(self):
        self.assertFalse(BrowserForm({'max_pages':3,'dynamic_pages':3}).is_valid())
        self.assertFalse(BrowserForm({'scroll_steps':11}).is_valid())
        self.assertTrue(BrowserForm({'max_pages':3,'dynamic_pages':1,'scroll_steps':0}).is_valid())

    def test_title_width_is_advice_without_expanding_numerical_score(self):
        from reporting.services import browser_checks
        from reporting.catalog import EXPECTED
        self.assertEqual(browser_checks({'metrics':{'title_width':{'status':'measured','width_px':601}}},set())['browser_title_width'],'issue')
        self.assertEqual(browser_checks({'metrics':{'title_width':{'status':'unknown','width_px':None}}},set())['browser_title_width'],'unknown')
        self.assertNotIn('browser_title_width',set().union(*EXPECTED.values()))


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'},'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'}})
class R5WorkflowTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user('r5-owner')
        self.project=Project.objects.create(name='R5 fixtures',website='https://example.com/',created_by=self.owner)
        Membership.objects.create(project=self.project,user=self.owner,role='admin')
        self.target=ScanTarget.objects.create(project=self.project,created_by=self.owner,name='R5',url='https://example.com/',allowed_hosts=['example.com'])
        self.source=ImportBatch.objects.create(project=self.project,created_by=self.owner,scan_target=self.target,source='semscan',method='scan',
            request_metadata={'scan':'crawl','url':self.target.url,'allowed_hosts':['example.com'],'respect_robots':False,'browser_phase':True,'browser_max_pages':3,'requests_per_second':7,'concurrency':3,'browser_requests_per_second':5})
        publish_records(batch_id=self.source.pk,records=[{'locator':str(i),'data':{'url':'https://example.com/'+path,'status':200,'final_status':200,'content_type':'text/html'}} for i,path in enumerate(('','news/a','contact'))])

    def make_run(self,**kw):return create_run(self.source,self.owner,{'max_pages':3,'dynamic_pages':1,**kw})

    def test_auto_phase_is_idempotent_and_baseline_only(self):
        with patch('browser_scans.tasks.process_browser.delay') as enqueue:
            schedule_after_crawl(self.source.pk);schedule_after_crawl(self.source.pk)
        self.assertEqual(enqueue.call_count,1)
        run=BrowserRun.objects.get(source_batch=self.source)
        self.assertTrue(run.config['automatic_from_crawl']);self.assertEqual(run.config['requests_per_second'],7)
        self.assertEqual(run.config['concurrency'],3)
        self.assertEqual(run.config['network_scope'],str(self.source.pk))
        self.assertFalse(run.config['privacy_enabled']);self.assertEqual(run.config['scenarios'],['baseline'])
        self.assertEqual(run.visits.count(),2)
        self.assertTrue(run.coverage['selection'])
        self.source.refresh_from_db();self.assertEqual(self.source.request_metadata['browser_followup_id'],str(run.pk))

    def test_automatic_phase_does_not_run_for_targeted_followup_or_no_html(self):
        self.source.request_metadata.update(retry_only=True);self.source.save()
        with patch('browser_scans.tasks.process_browser.delay') as enqueue:schedule_after_crawl(self.source.pk)
        enqueue.assert_not_called();self.assertFalse(BrowserRun.objects.filter(source_batch=self.source).exists())

    def test_reserved_budget_and_candidate_evidence(self):
        run=self.make_run();self.assertEqual(run.visits.count(),2)
        visit=run.visits.first();data=response(visit.url)
        data['exploration']={'links':[{'url':'https://example.com/js-only','phase':'after_scroll'}]}
        updated=store_visit(visit.pk,data)
        link=DiscoveredLink.objects.get(visit=updated)
        self.assertEqual(link.phase,'after_scroll');self.assertEqual(link.state,'queued_browser')
        self.assertEqual(run.visits.count(),3)
        self.assertEqual(updated.server_facts['titles'],['Server'])
        self.assertEqual(updated.rendered_facts['titles'],['Gerenderd'])

    def test_geometry_and_width_preserved_with_unknown_values(self):
        run=self.make_run(max_pages=1,dynamic_pages=0);visit=run.visits.get()
        data=response();data['images']=[{'url':'https://example.com/a','current_src':'https://example.com/a2','natural_width':None,'natural_height':None,'rendered_width':150,'rendered_height':None,'device_pixel_ratio':1,'viewport':{'width':1440,'height':900}}]
        data['title_width']={'status':'measured','width_px':650,'model':'sem-title-width-v1','font_sha256':'a'*64}
        visit=store_visit(visit.pk,data)
        self.assertIsNone(visit.coverage['images'][0]['natural_width'])
        self.assertEqual(visit.metrics['title_width']['width_px'],650)
        finalize(run.pk)
        self.assertTrue(run.batch.analysis_runs.get(definition__key='browser-basics').results.get().findings.filter(rule='browser_title_width').exists())

    def test_budget_and_outside_scope_remain_visible(self):
        run=self.make_run(max_pages=1,dynamic_pages=0);visit=run.visits.get();data=response()
        import base64
        data['dom']=base64.b64encode(b'<a href="/new">New</a><a href="https://other.example/">Other</a>').decode()
        store_visit(visit.pk,data)
        self.assertEqual(set(visit.discovered_links.values_list('state',flat=True)),{'budget','outside_scope'})
        self.assertEqual(run.visits.count(),1)

    def test_privacy_discovery_never_spawns_baseline_context(self):
        run=self.make_run(max_pages=1,dynamic_pages=0,privacy_enabled=True);visit=run.visits.first()
        store_visit(visit.pk,response())
        self.assertFalse(run.visits.filter(scenario='baseline').exists())
        self.assertEqual(visit.discovered_links.get().state,'privacy_only')

    def test_html_followup_requires_post_and_preserves_provenance(self):
        run=self.make_run(max_pages=1,dynamic_pages=0);visit=run.visits.get();store_visit(visit.pk,response());finalize(run.pk)
        self.client.force_login(self.owner)
        endpoint=reverse('ui:browser:html-followup',args=[self.project.pk,run.pk])
        self.assertEqual(self.client.get(endpoint).status_code,405)
        with patch('scans.tasks.enqueue_scan') as enqueue:
            with self.captureOnCommitCallbacks(execute=True):result=self.client.post(endpoint)
        self.assertEqual(result.status_code,302);self.assertEqual(enqueue.call_count,1)
        batch=ImportBatch.objects.get(request_metadata__browser_followup_run=str(run.pk))
        self.assertEqual(batch.request_metadata['max_depth'],0);self.assertTrue(batch.request_metadata['retry_only'])
        self.assertFalse(batch.request_metadata['browser_phase'])
        self.assertEqual(batch.retry_targets.get().snapshot['source_browser_visit'],visit.pk)
        self.assertTrue(visit.discovered_links.filter(followup=batch).exists())
        rendered=self.client.get(reverse('ui:browser:run',args=[self.project.pk,run.pk]))
        self.assertEqual(rendered.status_code,200)
        title=rendered.content.decode().split('<title>')[1].split('</title>')[0]
        self.assertNotIn('<form',title);self.assertNotIn('<h3',title)

    def test_original_already_measured_url_not_requeued_for_http(self):
        import base64
        run=self.make_run(max_pages=1,dynamic_pages=0);visit=run.visits.get();data=response();data['dom']=base64.b64encode(b'<a href="/contact">Contact</a>').decode()
        store_visit(visit.pk,data);finalize(run.pk)
        self.client.force_login(self.owner)
        self.client.post(reverse('ui:browser:html-followup',args=[self.project.pk,run.pk]))
        self.assertFalse(ImportBatch.objects.filter(request_metadata__browser_followup_run=str(run.pk)).exists())

    def test_scan_task_schedules_browser_after_page_publication(self):
        import tempfile
        from scans.tasks import process_scan
        from scans.network import FetchResult
        config={'scan':'crawl','url':'https://example.com/','allowed_hosts':['example.com'],'max_urls':1,'max_depth':0,'duration':120,'respect_robots':False,'browser_phase':True,'browser_max_pages':1}
        batch=ImportBatch.objects.create(project=self.project,created_by=self.owner,scan_target=self.target,source='semscan',method='scan',request_metadata=config)
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory),patch('scans.network.SafeClient.fetch',return_value=FetchResult('https://example.com/',200,{'content-type':'text/html'},b'<h1>Page</h1>')),patch('browser_scans.tasks.process_browser.delay') as enqueue:
            with self.captureOnCommitCallbacks(execute=True):process_scan(str(batch.pk))
        batch.refresh_from_db();self.assertEqual(batch.status,'complete',batch.error)
        self.assertEqual(enqueue.call_count,1)
        self.assertTrue(batch.request_metadata.get('browser_followup_id'))
        self.assertEqual(BrowserRun.objects.get(source_batch=batch).visits.count(),1)

    def test_targeted_http_task_keeps_browser_origin_without_expanding_or_recovery_claim(self):
        import tempfile
        from scans.tasks import process_scan
        from scans.network import FetchResult
        run=self.make_run(max_pages=1,dynamic_pages=0);visit=run.visits.get();store_visit(visit.pk,response());finalize(run.pk)
        self.client.force_login(self.owner)
        with patch('scans.tasks.enqueue_scan'):self.client.post(reverse('ui:browser:html-followup',args=[self.project.pk,run.pk]))
        batch=ImportBatch.objects.get(request_metadata__browser_followup_run=str(run.pk))
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory),patch('scans.network.SafeClient.fetch',return_value=FetchResult('https://example.com/js-only',200,{'content-type':'text/html'},b'<h1>New HTTP</h1><a href="/never-follow">Never</a>')) as fetch:
            process_scan(str(batch.pk))
        batch.refresh_from_db();self.assertEqual(batch.status,'complete',batch.error)
        self.assertEqual(fetch.call_count,1)
        data=batch.records.get().data
        self.assertEqual(data['discovery_sources'],['rendered_dom'])
        self.assertEqual(data['browser_discovery']['source_browser_visit'],visit.pk)
        self.assertNotIn('recovered_from_previous_scan',data)
        self.assertEqual(self.source.records.count(),3)

    def test_private_export_contains_geometry_model_and_entire_link_queue(self):
        run=self.make_run(max_pages=1,dynamic_pages=0);visit=run.visits.get();store_visit(visit.pk,response());finalize(run.pk)
        self.client.force_login(self.owner)
        export=self.client.get(reverse('ui:browser:export',args=[self.project.pk,run.pk]))
        text=b''.join(export.streaming_content).decode()
        self.assertIn('browser_visit',text);self.assertIn('discovered_link',text);self.assertIn('artifact',text)
        other=get_user_model().objects.create_user('r5-other');self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('ui:browser:export',args=[self.project.pk,run.pk])).status_code,404)
