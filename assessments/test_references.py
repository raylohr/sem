"""References are classified independently from fetch scope and page scores."""
import json
import hashlib
from types import SimpleNamespace
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase,SimpleTestCase,override_settings
from django.urls import reverse
from scans.references import classify
from scans.extraction import extract_page
from scans.models import ResourceMeasurement
from projects.models import Project,Membership
from sources.models import ImportBatch
from sources.services import publish_records
from .models import ReferenceObservation,Finding
from .services import rebuild_batch
from reporting.services import create_dossier,assemble


class ClassificationTests(SimpleTestCase):
    config={'domain':'example.com','include_subdomains':True,'allowed_hosts':['example.com'],
        'blocked_hosts':['private.example.com'],'allow_external_resources':True,'check_external_links':True}
    def test_axes_scope_and_label_boundaries(self):
        cases=[('/contact','own','navigation','allowed'),('https://blog.example.com/a','own','navigation','allowed'),
            ('https://private.example.com/a','own','navigation','excluded'),
            ('https://example.com.evil.test/a','external','navigation','allowed'),
            ('#','same_page','fragment','local'),('#team','same_page','fragment','local'),('mailto:info@example.com','action','contact','local'),
            ('tel:+31201234567','action','contact','local'),('/download.pdf','own','download','allowed')]
        for url,destination,function,scope in cases:
            with self.subTest(url=url):
                actual=classify(url,'https://example.com/',self.config)
                self.assertEqual((actual['destination'],actual['function'],actual['scope']),(destination,function,scope))
    def test_observed_mime_wins_over_missing_extension_but_retains_role(self):
        result=classify('/get?id=2','https://example.com/',self.config,mime='application/pdf')
        self.assertEqual(result['function'],'download');self.assertEqual(result['type_basis'],'response')
        result=classify('/get','https://example.com/',self.config,function='loaded_resource',mime='application/pdf')
        self.assertEqual(result['function'],'loaded_resource')
    def test_fragment_identity_keeps_query_and_path(self):
        self.assertEqual(classify('/other#team','https://example.com/',self.config)['function'],'navigation')
        self.assertEqual(classify('?x=1#team','https://example.com/',self.config)['destination'],'own')
    def test_external_loaded_resource_is_not_external_navigation(self):
        result=classify('https://fonts.gstatic.com/font.woff2','https://example.com/',self.config,function='loaded_resource')
        self.assertEqual((result['destination'],result['function']),('external','loaded_resource'))


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'},'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'}})
class ReferenceIntegrationTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('reference-owner')
        self.project=Project.objects.create(name='References',website='https://example.com/',created_by=self.user)
        Membership.objects.create(project=self.project,user=self.user,role='admin')
        self.batch=ImportBatch.objects.create(project=self.project,created_by=self.user,source='semscan',method='scan',request_metadata={
            'scan':'crawl','domain':'example.com','include_subdomains':True,'allowed_hosts':['example.com'],
            'blocked_hosts':['private.example.com'],'allow_external_resources':True,'check_external_links':True,'resource_version':'resources-v1'})
    def measurement(self,url,status=200,mime='text/html',outcome='success'):
        return ResourceMeasurement.objects.create(batch=self.batch,purpose='external' if 'external.test' in url else 'resource',url=url,
            url_hash=hashlib.sha256(url.encode()).hexdigest(),http_status=status,outcome=outcome,headers={'content-type':mime},measurements={'wire_bytes':100,'wire_complete':True,'representation_complete':True})
    def publish(self,html):
        fields,_=extract_page(SimpleNamespace(url='https://example.com/',status=200,headers={'content-type':'text/html'},body=html.encode()))
        data={'url':'https://example.com/','status':200,'final_status':200,'content_type':'text/html','googlebot_allowed':True,'measured_at':'2026-11-05T12:00:00+00:00',**fields}
        with patch('scans.network.SafeClient.fetch',side_effect=AssertionError('Offline analysis must never fetch')):
            publish_records(batch_id=self.batch.pk,records=[{'locator':'page:0','data':data}])
        return self.batch.analysis_runs.latest('started_at')
    def report(self):
        with patch('reporting.services.enqueue'):dossier=create_dossier(self.project,self.user,self.batch.pk)
        return assemble(dossier.pk)
    def test_external_unknown_does_not_reduce_internal_link_coverage(self):
        self.measurement('https://external.test/',status=999,outcome='unknown_response')
        run=self.publish('<title>T</title><h1>H</h1><a href="https://external.test/">External</a>')
        checks=run.results.get().answer['checks']
        for rule in ('links_to_404','link_to_5xx','link_to_redirect','link_to_nonindexable'):
            self.assertEqual(checks[rule],'not_applicable',rule)
        ref=ReferenceObservation.objects.get()
        self.assertEqual((ref.destination,ref.outcome,ref.reason),('external','unknown','unknown_response'))
        dossier=self.report()
        self.assertEqual(dossier.categories.count(),5)
        self.assertEqual(dossier.categories.get(key='technical').calculation['per_rule']['link_to_404']['unknown'],0)
        self.assertEqual(dossier.categories.get(key='files').coverage['counts']['unknown'],1)
    def test_shared_image_has_two_functions_one_measurement(self):
        self.measurement('https://example.com/shared.png',mime='image/png')
        self.publish('<a href="/shared.png">Download image</a><img src="/shared.png" alt="Shared">')
        self.assertEqual(set(ReferenceObservation.objects.values_list('function',flat=True)),{'download','loaded_resource'})
        self.assertEqual(ResourceMeasurement.objects.filter(batch=self.batch).count(),1)
        self.assertTrue(all(r.details['measurement']['http_status']==200 for r in ReferenceObservation.objects.all()))
    def test_extensionless_download_uses_actual_mime_and_pdf_html_is_an_issue(self):
        self.measurement('https://example.com/get',mime='application/pdf')
        self.measurement('https://example.com/manual.pdf',mime='text/html')
        run=self.publish('<a href="/get">Document</a><a href="/manual.pdf">Manual</a>')
        self.assertEqual(ReferenceObservation.objects.get(url__endswith='/get').function,'download')
        ref=ReferenceObservation.objects.get(url__endswith='/manual.pdf')
        self.assertEqual((ref.outcome,ref.reason),('issue','download_mime_mismatch'))
        self.assertEqual(run.results.get().answer['links']['total'],0)
    def test_fragments_contact_syntax_and_exclusions_have_evidence(self):
        self.publish('<h1 id="team">Team</h1><a href="#team">Team</a><a href="#missing">Missing</a><a href="mailto:bad">Bad</a><a href="mailto:info@example.com">Mail</a><a href="tel:+31201234567">Call</a><a href="https://private.example.com/">Private</a>')
        rows={r.url:r for r in ReferenceObservation.objects.all()}
        self.assertEqual(rows['https://example.com/#team'].outcome,'pass')
        self.assertEqual(rows['https://example.com/#missing'].outcome,'issue')
        self.assertEqual(rows['mailto:bad'].outcome,'issue')
        self.assertEqual(rows['mailto:info@example.com'].outcome,'pass')
        self.assertEqual(rows['tel:+31201234567'].outcome,'pass')
        self.assertEqual((rows['https://private.example.com/'].destination,rows['https://private.example.com/'].outcome),('own','not_tested'))
        self.assertEqual(set(Finding.objects.filter(rule__startswith='reference_').values_list('rule',flat=True)),{'reference_fragment_missing','reference_contact_invalid'})
    def test_absent_measurement_and_timeout_are_different(self):
        self.measurement('https://external.test/',status=None,outcome='download_error')
        self.publish('<a href="https://external.test/">Timeout</a><a href="/missing">Not crawled</a>')
        self.assertEqual(ReferenceObservation.objects.get(destination='external').outcome,'unknown')
        self.assertEqual(ReferenceObservation.objects.get(destination='own').outcome,'not_tested')
    def test_attempted_internal_timeout_is_unknown_not_unvisited(self):
        fields,_=extract_page(SimpleNamespace(url='https://example.com/',status=200,headers={'content-type':'text/html'},body=b'<a href="/slow">Slow</a>'))
        publish_records(batch_id=self.batch.pk,records=[
            {'locator':'main','data':{**fields,'url':'https://example.com/','status':200,'final_status':200,'content_type':'text/html'}},
            {'locator':'failed','data':{'url':'https://example.com/slow','status':None,'content_type':'','error':'Timeout','error_code':'read_timeout'}}])
        run=self.batch.analysis_runs.latest('started_at')
        checks=run.results.get(source_record__locator='main').answer['checks']
        for rule in ('links_to_404','link_to_5xx','link_to_redirect','link_to_nonindexable'):self.assertEqual(checks[rule],'unknown',rule)
        self.assertEqual(ReferenceObservation.objects.get().outcome,'unknown')

    def test_unmeasured_files_stay_in_files_coverage(self):
        self.batch.request_metadata['check_resources']=False
        self.batch.save(update_fields=['request_metadata'])
        self.publish('<script src="/never.js"></script>')
        ref=ReferenceObservation.objects.get()
        self.assertEqual((ref.outcome,ref.reason),('not_tested','file_checks_disabled'))
        report=self.report()
        self.assertEqual(report.categories.get(key='files').coverage['counts']['not_tested'],1)
        self.assertIsNone(report.resources.get().measurement_id)

    def test_snapshot_filter_export_and_access_survive_reanalysis(self):
        self.measurement('https://external.test/')
        self.publish('<a href="https://external.test/">External</a>')
        dossier=self.report();frozen=dossier.references.get();snapshot=frozen.snapshot.copy()
        self.client.force_login(self.user)
        url=reverse('ui:reports:references',args=[self.project.pk,dossier.pk])
        response=self.client.get(url+'?destination=external&function=navigation')
        self.assertContains(response,'Externe website');self.assertContains(response,'Bestemming bereikbaar')
        response=self.client.get(url+'?download=1')
        rows=[json.loads(line) for line in b''.join(response.streaming_content).splitlines()]
        self.assertEqual(rows[1]['destination'],'external')
        original=self.batch.records.get().data.copy()
        rebuild_batch(self.batch.pk)
        frozen.refresh_from_db();self.assertIsNone(frozen.source_id);self.assertEqual(frozen.snapshot,snapshot)
        self.assertEqual(self.batch.records.get().data,original)
        other=get_user_model().objects.create_user('reference-other');self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code,404)
