import base64
import copy
import datetime
import json
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from projects.models import Project, Membership
from scans.models import ScanTarget
from sources.models import ImportBatch
from sources.services import publish_records, delete_batch
from browser_scans.forms import BrowserForm
from browser_scans.models import StorageObservation, BrowserResource
from browser_scans.services import create_run, store_visit, finalize
from browser_scans.tests import response
from browser_scans.privacy import redacted_url
from assessments.models import Finding
from .models import EnrichmentRun, DetectionRule, Technology, Supplier, SupplierRevision, DomainObservation, CustomerDeclaration, PolicyDocument
from .catalog import install_catalog
from .detection import detect_visit, detect_domain, suffix
from .services import profile, privacy_analysis, update_supplier
from .forms import SupplierForm
from .tasks import enrich


class RedactionTests(SimpleTestCase):
    def test_query_values_and_credentials_are_redacted(self):
        value=redacted_url('https://user:pass@example.com/g/collect?cid=visitor-secret&token=auth-secret#fragment')
        self.assertNotIn('visitor-secret',value);self.assertNotIn('auth-secret',value);self.assertNotIn('user:pass',value)
        self.assertIn('cid=',value)
        self.assertTrue(suffix('region.google-analytics.com','google-analytics.com'))
        self.assertFalse(suffix('google-analytics.com.attacker.example','google-analytics.com'))

    def test_privacy_budget_validation(self):
        self.assertFalse(BrowserForm({'privacy_enabled':True,'max_pages':26}).is_valid())
        self.assertTrue(BrowserForm({'privacy_enabled':True,'max_pages':25}).is_valid())
        self.assertFalse(BrowserForm({'privacy_enabled':True,'consent_accept':'javascript:alert(1)'}).is_valid())


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'},'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'}})
class IntelligenceTests(TestCase):
    def setUp(self):
        user=get_user_model()
        self.owner=user.objects.create_user('intel-owner')
        self.viewer=user.objects.create_user('intel-viewer')
        self.other=user.objects.create_user('intel-other')
        self.root=user.objects.create_superuser('intel-root',password='test-password-not-used')
        self.project=Project.objects.create(name='Intelligence',website='https://example.com/',created_by=self.owner)
        Membership.objects.create(project=self.project,user=self.owner,role='admin')
        Membership.objects.create(project=self.project,user=self.viewer,role='viewer')
        self.target=ScanTarget.objects.create(project=self.project,created_by=self.owner,name='Own',url='https://example.com/',allowed_hosts=['example.com'])
        self.source=ImportBatch.objects.create(project=self.project,created_by=self.owner,scan_target=self.target,source='semscan',method='scan',label='Source',request_metadata={'scan':'crawl','allowed_hosts':['example.com'],'respect_robots':False})
        publish_records(batch_id=self.source.pk,records=[{'locator':'page:0','data':{'url':'https://example.com/','status':200,'content_type':'text/html'}}])
        self.rules=list(DetectionRule.objects.filter(active=True).select_related('technology'))

    def browser(self,privacy=False):
        form=BrowserForm({'max_pages':1,'privacy_enabled':privacy});self.assertTrue(form.is_valid())
        return create_run(self.source,self.owner,form.cleaned_data)

    def payload(self):
        value=response()
        html=b'<html><meta name="generator" content="WordPress 6.7.1"><script src="/wp-content/plugins/elementor/frontend.js"></script><script src="/_next/static/chunk.js"></script><main><h1>Test</h1></main></html>'
        value['server_response']['body']=base64.b64encode(html).decode()
        value['dom']=base64.b64encode(html).decode()
        value['technology_globals']={'jQuery':'3.7.1','HubSpot':'present'}
        value['resources']=[{'url':'https://www.google-analytics.com/g/collect?cid=%5Bredacted%5D','resource_type':'fetch','status':200,'bytes':0,'phase':'before_choice'},
            {'url':'https://google-analytics.com.attacker.example/g/collect','resource_type':'fetch','status':200,'bytes':0,'phase':'before_choice'},
            {'url':'https://plausible.io/js/script.js','resource_type':'script','status':200,'bytes':10,'phase':'before_choice'}]
        return value

    def enrich_model(self,browser):
        return EnrichmentRun.objects.create(batch=browser.batch,actor=self.owner)

    def test_catalog_install_is_idempotent_and_does_not_overwrite_reviewed_suppliers(self):
        count=Technology.objects.count();rules=DetectionRule.objects.count()
        supplier=Supplier.objects.get(key='google');supplier.notes='Reviewed note';supplier.save()
        install_catalog();install_catalog()
        self.assertEqual(Technology.objects.count(),count);self.assertEqual(DetectionRule.objects.count(),rules)
        supplier.refresh_from_db();self.assertEqual(supplier.notes,'Reviewed note')

    def test_four_clean_scenario_queue_and_storage_values_omitted(self):
        browser=self.browser(privacy=True)
        self.assertEqual(set(browser.visits.values_list('scenario',flat=True)),{'no_choice','reject','accept','withdraw'})
        value=self.payload();value['privacy']={'complete':True,'actions':[], 'snapshots':[{'phase':'before_choice','kind':'cookie','name':'_ga','origin':'example.com','metadata':{'secure':True,'value':'must-not-be-stored','length':5}}]}
        visit=store_visit(browser.visits.get(scenario='no_choice').pk,value)
        self.assertEqual(visit.storage_observations.count(),1)
        self.assertNotIn('value',visit.storage_observations.get().metadata)
        self.assertNotIn('must-not-be-stored',json.dumps(visit.privacy))
        self.assertNotIn('snapshots',visit.privacy)

    def test_signatures_include_versions_and_do_not_match_a_hostname_suffix_attack(self):
        browser=self.browser();visit=store_visit(browser.visits.get().pk,self.payload())
        run=self.enrich_model(browser);detect_visit(run,visit,self.rules)
        self.assertTrue(run.detections.filter(technology__key='wordpress',version='6.7.1').exists())
        self.assertTrue(run.detections.filter(technology__key='elementor').exists())
        self.assertTrue(run.detections.filter(technology__key='nextjs').exists())
        self.assertTrue(run.detections.filter(technology__key='jquery',version='3.7.1').exists())
        self.assertFalse(run.detections.filter(evidence__snapshot__signal__contains='attacker.example',technology__key='ga4').exists())
        count=run.detections.count();detect_visit(run,visit,self.rules);self.assertEqual(run.detections.count(),count)

    def test_blocked_tracking_is_not_claimed_to_have_been_transmitted(self):
        browser=self.browser(privacy=True)
        value=self.payload();value['resources'][0]['status']=None;value['resources'][0]['reason']='host_scope'
        value['privacy']={'complete':True,'actions':[],'snapshots':[]}
        visit=store_visit(browser.visits.get(scenario='no_choice').pk,value)
        # Privacy analysis needs a published record; publish all four fixture visits.
        browser.visits.exclude(pk=visit.pk).update(status='failed')
        finalize(browser.pk);visit.refresh_from_db()
        run=self.enrich_model(browser);detect_visit(run,visit,self.rules);privacy_analysis(run,browser)
        self.assertTrue(run.detections.filter(state='attempted_request',technology__key='ga4').exists())
        self.assertFalse(Finding.objects.filter(result__run__definition__key='privacy-signals',rule='tracking_before_choice').exists())

    def test_privacy_signal_has_evidence_and_no_legal_verdict(self):
        browser=self.browser(privacy=True)
        for visit in browser.visits.all():
            value=self.payload();phase={'no_choice':'before_choice','reject':'after_reject','accept':'after_accept','withdraw':'after_withdraw'}[visit.scenario]
            value['resources'][0]['phase']=phase
            value['privacy']={'complete':True,'actions':[{'action':visit.scenario,'status':'completed'}] if visit.scenario!='no_choice' else [],'snapshots':[]}
            store_visit(visit.pk,value)
        finalize(browser.pk)
        run=self.enrich_model(browser)
        for visit in browser.visits.all():detect_visit(run,visit,self.rules)
        privacy_analysis(run,browser)
        self.assertTrue(Finding.objects.filter(result__run__definition__key='privacy-signals',rule='tracking_before_choice').exists())
        self.assertTrue(Finding.objects.filter(result__run__definition__key='privacy-signals',rule='tracking_after_reject').exists())
        self.assertTrue(Finding.objects.filter(result__run__definition__key='privacy-signals',rule='tracking_after_withdraw').exists())
        finding=Finding.objects.filter(result__run__definition__key='privacy-signals').first()
        self.assertTrue(finding.details['review_required']);self.assertTrue(finding.evidence.exists())
        self.assertIn('geen juridisch oordeel',finding.recommendation)

    def test_unverified_reject_action_does_not_create_after_rejection_claim(self):
        browser=self.browser(privacy=True)
        visit=browser.visits.get(scenario='reject')
        value=self.payload();value['resources'][0]['phase']='after_reject';value['privacy']={'complete':False,'actions':[{'action':'reject','status':'not_found'}],'snapshots':[]}
        store_visit(visit.pk,value);browser.visits.exclude(pk=visit.pk).update(status='failed');finalize(browser.pk)
        run=self.enrich_model(browser);visit.refresh_from_db();detect_visit(run,visit,self.rules);privacy_analysis(run,browser)
        self.assertFalse(Finding.objects.filter(result__run__definition__key='privacy-signals',rule='tracking_after_reject').exists())
        self.assertEqual(run.batch.analysis_runs.get(definition__key='privacy-signals').results.get(source_record=visit.source_record).outcome,'unknown')

    def test_country_profile_keeps_data_location_unknown_and_supplier_revisions_immutable(self):
        browser=self.browser();visit=store_visit(browser.visits.get().pk,self.payload());run=self.enrich_model(browser);detect_visit(run,visit,self.rules)
        before=list(run.detections.filter(technology__key='ga4').values_list('supplier_snapshot',flat=True))
        supplier=Supplier.objects.get(key='google')
        update_supplier(supplier.pk,self.root,{'name':supplier.name,'country':'IE','parent_name':'Example parent','parent_country':'US','checked_at':timezone.localdate(),'source_url':'https://example.com/source','notes':'test'})
        self.assertEqual(list(run.detections.filter(technology__key='ga4').values_list('supplier_snapshot',flat=True)),before)
        result=profile(run)
        self.assertGreaterEqual(result['non_eu_publishers'],1)
        self.assertGreaterEqual(result['eu_publishers'],1)
        self.assertIn('unknown',result['data_locations'])
        self.assertIn('not inferred',result['gdpr_assessment'])
        self.assertTrue(SupplierRevision.objects.filter(supplier=supplier,revision=2,actor=self.root).exists())

    def test_supplier_claim_requires_real_country_source_and_date(self):
        self.assertFalse(SupplierForm({'key':'test','name':'Test','country':'ZZ','source_url':'https://example.com','checked_at':timezone.localdate()}).is_valid())
        self.assertFalse(SupplierForm({'key':'test','name':'Test','country':'NL'}).is_valid())
        with self.assertRaises(PermissionDenied):update_supplier(Supplier.objects.first().pk,self.owner,{})

    def test_dns_evidence_is_not_origin_hosting_or_storage_location(self):
        browser=self.browser();run=self.enrich_model(browser)
        domain=DomainObservation.objects.create(run=run,hostname='example.com',records={'NS':{'values':['a.ns.cloudflare.com.']},'TXT':{'values':['""']}},network={'networks':[{'asns':['13335']}],'physical_location':'unknown'})
        detect_domain(run,domain,self.rules)
        self.assertTrue(run.detections.filter(technology__key='cloudflare',evidence__domain=domain).exists())
        self.assertIn('unknown',profile(run)['data_locations'])

    def test_enrichment_task_resumes_domains_without_duplicate_analyses(self):
        browser=self.browser();store_visit(browser.visits.get().pk,self.payload());finalize(browser.pk);run=self.enrich_model(browser)
        data={'records':{'A':{'values':['93.184.216.34'],'ttl':30}},'network':{'public_addresses':['93.184.216.34'],'networks':[],'physical_location':'unknown'},'coverage':{}}
        with patch('site_intelligence.tasks.observe_domain',return_value=data),patch('site_intelligence.tasks.enrich.apply_async'):
            for _ in range(8):
                enrich(run.pk);run.refresh_from_db()
                if run.status in ('complete','incomplete','failed'):break
        self.assertEqual(run.status,'complete',run.error)
        count=run.detections.count();analyses=run.batch.analysis_runs.count();enrich(run.pk)
        self.assertEqual(run.detections.count(),count);self.assertEqual(run.batch.analysis_runs.count(),analyses)

    def test_revoked_access_stops_dns_and_observation_writes(self):
        browser=self.browser();store_visit(browser.visits.get().pk,self.payload());finalize(browser.pk);run=self.enrich_model(browser)
        Membership.objects.filter(project=self.project,user=self.owner).delete()
        with patch('site_intelligence.tasks.observe_domain') as dns:
            enrich(run.pk);dns.assert_not_called()
        run.refresh_from_db();self.assertEqual(run.status,'failed')

    def test_views_and_customer_declarations_obey_project_roles(self):
        browser=self.browser();visit=store_visit(browser.visits.get().pk,self.payload());finalize(browser.pk);run=self.enrich_model(browser);detect_visit(run,visit,self.rules)
        detection=run.detections.first()
        urls=[reverse('ui:intelligence:overview',args=[self.project.pk]),reverse('ui:intelligence:run',args=[self.project.pk,run.pk]),reverse('ui:intelligence:detection',args=[self.project.pk,detection.pk]),reverse('ui:intelligence:registry',args=[self.project.pk]),reverse('ui:intelligence:export',args=[self.project.pk,run.pk])]
        self.client.force_login(self.viewer)
        for url in urls:
            result=self.client.get(url);self.assertEqual(result.status_code,200,url)
            if getattr(result,'streaming',False):self.assertIn(b'technology',b''.join(result.streaming_content))
            self.assertIn('no-store',result['Cache-Control'])
        declaration=reverse('ui:intelligence:declaration',args=[self.project.pk])
        self.assertEqual(self.client.post(declaration,{}).status_code,403)
        self.assertEqual(self.client.get(reverse('ui:intelligence:supplier',args=[self.project.pk,Supplier.objects.first().pk])).status_code,403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(declaration,{'technology':Technology.objects.get(key='hubspot').pk,'kind':'central_crm','statement':'<script>Example</script>'}).status_code,302)
        self.assertEqual(CustomerDeclaration.objects.get(project=self.project).actor,self.owner)
        result=self.client.get(urls[0]);self.assertContains(result,'&lt;script&gt;Example&lt;/script&gt;')
        self.client.force_login(self.other)
        for url in urls:self.assertEqual(self.client.get(url).status_code,404)

    def test_batch_deletion_removes_scoped_intelligence_and_keeps_public_registry(self):
        browser=self.browser();store_visit(browser.visits.get().pk,self.payload());finalize(browser.pk);run=self.enrich_model(browser)
        detect_visit(run,browser.visits.get(),self.rules)
        with patch('sources.tasks.enqueue_cleanup'):delete_batch(batch_id=browser.batch_id,project=self.project,actor=self.owner)
        self.assertFalse(EnrichmentRun.objects.filter(pk=run.pk).exists())
        self.assertTrue(Technology.objects.filter(key='wordpress').exists())

class StorageCoverageTests(SimpleTestCase):
    def test_storage_bounds_and_unreadable_frames_are_explicit(self):
        from unittest.mock import Mock
        from browser_scans.privacy import snapshot
        context=Mock()
        context.cookies.return_value=[{'name':'bounded','domain':'example.com'}]*301
        frame=Mock()
        frame.evaluate.side_effect=RuntimeError('inaccessible')
        page=Mock(frames=[frame])
        coverage={}
        rows=snapshot(context,page,'before_choice',coverage)
        self.assertEqual(len(rows),300)
        self.assertTrue(coverage['storage_truncated'])
        self.assertTrue(coverage['storage_unreadable'])
        self.assertTrue(all('value' not in row for row in rows))

    def test_ephemeral_cookie_header_is_bounded_and_never_authorization(self):
        from unittest.mock import Mock
        from scans.network import SafeClient, ScanError
        from urllib3._collections import HTTPHeaderDict
        reply=Mock(status=200,headers=HTTPHeaderDict({'content-type':'text/plain'}))
        reply.headers.add('Set-Cookie','one=secret; HttpOnly')
        reply.headers.add('Set-Cookie','two=secret; Secure')
        reply.read.side_effect=[b'ok',b'']
        pool=Mock()
        pool.urlopen.return_value=reply
        client=SafeClient(['example.com'],check_access=lambda:None,respect_robots=False)
        with patch('scans.network.resolve_public',return_value='93.184.216.34'),patch('scans.network.urllib3.HTTPSConnectionPool',return_value=pool):
            result=client.fetch_once('https://example.com/',enforce_robots=False,cookie_header='one=secret')
            headers=pool.urlopen.call_args.kwargs['headers']
            self.assertEqual(headers['Cookie'],'one=secret')
            self.assertNotIn('Authorization',headers)
            self.assertEqual(len(result.set_cookies),2)
            pool.urlopen.reset_mock()
            for bad in ['one=secret\r\nAuthorization: injected','x'*16385]:
                with self.assertRaises(ScanError):
                    client.fetch_once('https://example.com/',enforce_robots=False,cookie_header=bad)
            pool.urlopen.assert_not_called()
        client.close()
