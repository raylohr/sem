import json
from types import SimpleNamespace
from django.test import TestCase, SimpleTestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from sources.models import ImportBatch
from sources.services import publish_records
from projects.models import Project, Membership
from scans.extraction import extract_page
from scans.models import ScanDocument
from .models import Finding, PageMeasurement, AnalysisRun
from .services import rebuild_batch
from .advanced import valid_hreflang


class NewExtractionTests(SimpleTestCase):
    def extract(self,html,headers=None):
        return extract_page(SimpleNamespace(url='https://example.com/',status=200,
            headers={'content-type':'text/html',**(headers or {})},body=html.encode()))[0]

    def test_observations_distinguish_absent_and_empty_alt_and_syntax_errors(self):
        f=self.extract('<html lang="nl"><h1>One</h1><h3>Three</h3><img src="x"><img src="y" alt=""><script type="application/ld+json">{"@type":"Thing"}</script><script type="application/ld+json">{bad}</script><link rel="alternate" hreflang="en" href="/en/">')
        self.assertEqual([i['alt_present'] for i in f['images']],[False,True])
        self.assertEqual([i['level'] for i in f['heading_sequence']],[1,3])
        self.assertEqual([i['status'] for i in f['jsonld']],['valid','invalid'])
        self.assertEqual(f['hreflang'][0]['url'],'https://example.com/en/')
        self.assertNotIn('set-cookie',f['response_headers'])

    def test_http_canonical_affects_indexability_and_sources_are_retained(self):
        f=self.extract('<link rel="canonical" href="/">',{'link':'<https://example.com/other/>; rel="canonical"','set-cookie':'secret=value'})
        self.assertEqual(len(f['canonical_sources']),2)
        self.assertEqual(f['canonical_sources'][1]['source'],'header')
        self.assertNotIn('set-cookie',f['response_headers'])

    def test_unrelated_robot_is_not_googlebot(self):
        f=self.extract('<title>One</title>',{'x-robots-tag':'otherbot: noindex'})
        self.assertTrue(f['is_indexable'])
        self.assertFalse(self.extract('',{'x-robots-tag':'googlebot: noindex'})['is_indexable'])

    def test_jsonld_limits_stay_unknown(self):
        f=self.extract('<script type="application/ld+json">'+json.dumps({'large':'x'*70000})+'</script>')
        self.assertEqual(f['jsonld'][0]['status'],'unknown')

    def test_hreflang_codes(self):
        for code in ('nl','nl-NL','en-GB','zh-Hans-CN','x-default','eu'):self.assertTrue(valid_hreflang(code),code)
        for code in ('xx','en-UK','en-EU','en_uk','en-XX','-NL','en-US-extra'):self.assertFalse(valid_hreflang(code),code)


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'},'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'}})
class PageRelationTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('rules-owner')
        self.other=get_user_model().objects.create_user('rules-other')
        self.project=Project.objects.create(name='Rule fixtures',website='https://example.com/',created_by=self.user)
        Membership.objects.create(project=self.project,user=self.user,role='admin')
        self.batch=ImportBatch.objects.create(project=self.project,created_by=self.user,source='semscan',method='scan',label='Rules',
            request_metadata={'scan':'crawl','url':'https://example.com/'})

    def page(self,path='/',html='',status=200,**values):
        url='https://example.com'+path
        fields,_=extract_page(SimpleNamespace(url=url,status=status,headers={'content-type':'text/html'},body=(
            '<html lang="nl"><head><title>'+path+'</title><meta name="description" content="Description '+path+'"></head><body><h1>Heading</h1>'+html+'</body></html>').encode()))
        return {'url':url,'final_url':url,'status':status,'final_status':status,'content_type':'text/html','measured_at':'2026-11-03T12:00:00+00:00',
            'redirects':[],'googlebot_allowed':True,**fields,**values}

    def publish(self,pages):
        publish_records(batch_id=self.batch.pk,records=[{'locator':'page:'+str(i),'data':page} for i,page in enumerate(pages)])
        return self.batch.analysis_runs.latest('started_at')

    def finding(self,run,rule):return Finding.objects.filter(result__run=run,rule=rule)

    def test_canonical_errors_and_target_evidence(self):
        run=self.publish([self.page(html='<link rel="canonical" href="/gone/">'),self.page('/gone/',status=404)])
        finding=self.finding(run,'canonical_target').get()
        self.assertTrue(finding.evidence.filter(role='target',snapshot__final_http_status=404).exists())
        self.assertEqual(self.finding(run,'http_error').count(),1)

    def test_canonical_chains_and_loops(self):
        run=self.publish([self.page(html='<link rel="canonical" href="/b/">'),self.page('/b/',html='<link rel="canonical" href="/">')])
        self.assertEqual(self.finding(run,'canonical_loop').count(),2)
        self.assertEqual(self.finding(run,'canonical_chain').count(),2)

    def test_self_canonical_and_same_header_canonical_do_not_form_a_loop(self):
        page=self.page(html='<link rel="canonical" href="/">')
        page['canonicals']*=2;page['canonical_sources']=[{'source':'html','url':page['url']},{'source':'header','url':page['url']}]
        run=self.publish([page])
        self.assertFalse(self.finding(run,'canonical_multiple').exists())
        self.assertFalse(self.finding(run,'canonical_loop').exists())
        self.assertEqual(run.results.get().answer['checks']['canonical_target'],'pass')

    def test_uncrawled_target_remains_unknown_without_network_or_borrowing_other_batches(self):
        run=self.publish([self.page(html='<link rel="canonical" href="https://outside.example/page">')])
        self.assertEqual(run.results.get().answer['checks']['canonical_target'],'unknown')
        self.assertFalse(self.finding(run,'canonical_target').exists())

    def test_hreflang_missing_return_and_noncanonical_target(self):
        run=self.publish([self.page(html='<link rel="alternate" hreflang="nl" href="/"><link rel="alternate" hreflang="en" href="/en/">'),
            self.page('/en/',html='<link rel="canonical" href="/preferred/">')])
        self.assertTrue(self.finding(run,'hreflang_return_missing').exists())
        self.assertTrue(self.finding(run,'hreflang_target').exists())
        self.assertTrue(self.finding(run,'hreflang_return_missing').first().evidence.filter(role='target').exists())

    def test_hreflang_reciprocal_healthy_pair(self):
        links='<link rel="alternate" hreflang="nl" href="/"><link rel="alternate" hreflang="en" href="/en/">'
        run=self.publish([self.page(html=links+'<link rel="canonical" href="/">'),self.page('/en/',html=links+'<link rel="canonical" href="/en/">')])
        for rule in ('hreflang_invalid','hreflang_self_missing','hreflang_return_missing','hreflang_target'):
            self.assertFalse(self.finding(run,rule).exists(),rule)
            self.assertTrue(all(r.answer['checks'][rule]=='pass' for r in run.results.all()),rule)

    def test_hreflang_invalid_duplicate_and_missing_self(self):
        run=self.publish([self.page(html='<link rel="alternate" hreflang="en-UK" href="/bad/"><link rel="alternate" hreflang="en" href="/a/"><link rel="alternate" hreflang="en" href="/b/">')])
        for rule in ('hreflang_invalid','hreflang_conflict','hreflang_self_missing'):self.assertTrue(self.finding(run,rule).exists(),rule)

    def test_per_page_assets_and_googlebot_conflicts(self):
        run=self.publish([self.page(html='<h3>Jump</h3><img src="/x"><img src="/decor" alt=""><script src="http://example.com/a.js"></script><script type="application/ld+json">{bad}</script>',
            robot_directives=[{'source':'meta','agent':'robots','value':'index, follow'},{'source':'header','agent':'unspecified','value':'googlebot: noindex, nofollow'}],googlebot_allowed=False)])
        for rule in ('heading_jump','image_alt_missing','mixed_content','jsonld_invalid','robots_conflict','robots_noindex_blocked'):
            self.assertEqual(self.finding(run,rule).count(),1,rule)
        missing=self.finding(run,'image_alt_missing').get().details['observations'][0]['images']
        self.assertEqual(len(missing),1)

    def test_link_redirect_server_error_and_excluded_target(self):
        run=self.publish([self.page(html='<a href="/redirect/">R</a><a href="/error/">E</a><a href="/hidden/">H</a>'),
            self.page('/redirect/',status=301,final_status=200,final_url='https://example.com/destination/'),
            self.page('/error/',status=503),self.page('/hidden/',html='<meta name="robots" content="noindex">')])
        for rule in ('link_to_redirect','link_to_5xx','link_to_nonindexable'):
            self.assertEqual(self.finding(run,rule).count(),1,rule)
            self.assertTrue(self.finding(run,rule).get().evidence.filter(role='target').exists())

    def test_graph_counts_and_depth_and_local_orphan_candidate(self):
        run=self.publish([self.page(html='<a href="/a/">A</a><a href="/a/">Again</a>'),self.page('/a/',html='<a href="/b/">B</a>'),self.page('/b/'),self.page('/orphan/')])
        results={r.answer['input']['url']:r for r in run.results.all()}
        self.assertEqual(results['https://example.com/a/'].answer['internal_links']['observed_inlinks'],1)
        self.assertEqual(results['https://example.com/b/'].answer['internal_links']['graph_depth'],2)
        self.assertEqual(self.finding(run,'no_observed_inlinks').count(),1)
        self.assertEqual(self.finding(run,'no_observed_inlinks').get().details['url'],'https://example.com/orphan/')

    def test_incomplete_graph_does_not_assert_orphans(self):
        run=self.publish([self.page(links_truncated=True),self.page('/unknown-support/')])
        self.assertFalse(run.coverage['graph']['complete'])
        self.assertFalse(self.finding(run,'no_observed_inlinks').exists())

    def test_unknown_legacy_headers_do_not_become_missing_after_recovery(self):
        page=self.page();page.pop('headers_observed');page.pop('response_headers')
        self.publish([page])
        ScanDocument.objects.create(batch=self.batch,index=0,url=page['url'],status=200,content_type='text/html',body=b'<html><title>Recovered</title></html>',fetched_at=timezone.now())
        run=rebuild_batch(self.batch.pk,recover_documents=True)
        self.assertEqual(run.results.get().answer['checks']['security_headers_missing'],'unknown')
        self.assertFalse(self.finding(run,'security_headers_missing').exists())

    def test_rule_coverage_counts_each_page_once(self):
        run=self.publish([self.page(html='<a href="/error/">E</a>'),self.page('/error/',status=503)])
        for counts in run.coverage['advanced_checks'].values():self.assertLessEqual(sum(counts.values()),2)

    def test_page_result_ui_export_and_project_scope(self):
        run=self.publish([self.page(html='<link rel="canonical" href="/gone/">'),self.page('/gone/',status=404)])
        result=run.results.get(source_record__data__url='https://example.com/')
        url=reverse('ui:assessments:result',args=[self.project.pk,result.pk])
        self.client.force_login(self.user)
        self.assertContains(self.client.get(url),'Canonicaldoel')
        self.assertContains(self.client.get(reverse('ui:assessments:run',args=[self.project.pk,run.pk])),'Uitgevoerde controles')
        export=self.client.get(reverse('ui:assessments:export',args=[self.project.pk,run.pk]))
        rows=b''.join(export.streaming_content).decode()
        self.assertIn('canonical_target',rows);self.assertIn('internal_links',rows)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code,404)

    def test_relation_targets_are_partitioned_by_consent_scenario(self):
        self.batch.request_metadata={**self.batch.request_metadata,'privacy_enabled':True};self.batch.save()
        run=self.publish([self.page(html='<link rel="canonical" href="/target/">',privacy_scenario='reject'),
            self.page('/target/',status=404,privacy_scenario='accept'),self.page('/target/',html='<link rel="canonical" href="/target/">',privacy_scenario='reject')])
        self.assertFalse(self.finding(run,'canonical_target').exists())
        result=run.results.get(source_record__data__url='https://example.com/')
        self.assertEqual(result.answer['checks']['canonical_target'],'pass')

    def test_exact_duplicates_have_peer_evidence(self):
        run=self.publish([self.page(),self.page('/same/',titles=['/'],text_hash=self.page()['text_hash'])])
        finding=self.finding(run,'duplicate_text').first()
        self.assertIsNotNone(finding)
        self.assertTrue(finding.evidence.filter(role='target').exists())

    def test_browser_recovery_uses_rendered_dom_and_preserves_source(self):
        from browser_scans.models import BrowserRun, BrowserVisit, BrowserArtifact
        self.batch.request_metadata={'scan':'browser'};self.batch.save()
        run=self.publish([self.page(html='<img src="/old" alt="Old">')])
        source=self.batch.records.get();original=dict(source.data)
        browser=BrowserRun.objects.create(batch=self.batch,source_batch=None,config={})
        visit=BrowserVisit.objects.create(run=browser,url=source.data['url'],url_hash='f'*64,source_record=source,http_status=200,final_url=source.data['url'])
        artifact=BrowserArtifact.objects.create(visit=visit,kind='rendered_dom',body=b'<html lang="nl"><title>New</title><h1>H</h1><img src="/new"></html>',size=100,checksum='a'*64,content_type='text/html')
        updated=rebuild_batch(self.batch.pk,recover_documents=True)
        source.refresh_from_db();self.assertEqual(source.data,original)
        finding=self.finding(updated,'image_alt_missing').get()
        self.assertTrue(finding.evidence.filter(browser_artifact=artifact).exists())
        self.assertTrue(AnalysisRun.objects.filter(pk=run.pk).exists())

    def test_complete_healthy_page_can_pass(self):
        headers={'strict-transport-security':'max-age=31536000','content-security-policy':"default-src 'self'",'x-content-type-options':'nosniff'}
        run=self.publish([self.page(html='<link rel="canonical" href="/">',response_headers=headers)])
        self.assertEqual(run.results.get().outcome,'pass')

    def test_hreflang_return_must_use_the_declared_source_language(self):
        links='<link rel="alternate" hreflang="nl" href="/"><link rel="alternate" hreflang="en" href="/en/">'
        run=self.publish([self.page(html=links+'<link rel="canonical" href="/">'),
            self.page('/en/',html='<link rel="canonical" href="/en/"><link rel="alternate" hreflang="fr" href="/"><link rel="alternate" hreflang="en" href="/en/">')])
        finding=self.finding(run,'hreflang_return_missing').get(result__source_record__data__url='https://example.com/')
        self.assertEqual(finding.details['observations'][0]['expected_codes'],['nl'])
