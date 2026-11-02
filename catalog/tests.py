import datetime
from types import SimpleNamespace
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase,TestCase
from django.urls import reverse
from django.utils import timezone
from projects.models import Project,Membership
from sources.models import ImportBatch,SourceRecord
from sources.services import publish_records,delete_batch
from .adapters import normalize_url,adapt_record
from .models import ProjectURL,URLObservation
from .services import index_batch


class AdapterTests(SimpleTestCase):
    def batch(self,source,method='upload',**extra):
        return SimpleNamespace(source=source,method=method,measured_at=None,period_end=None,**extra)

    def test_normalization_only_changes_known_equivalents(self):
        self.assertEqual(normalize_url('HTTPS://EXAMPLE.com:443#fragment')[0],'https://example.com/')
        variants=['http://example.com/','https://example.com/','https://www.example.com/','https://example.com/a','https://example.com/a/','https://example.com/?a=1&b=2','https://example.com/?b=2&a=1','http://example.com:0/','https://example.com/a%2Fb','https://example.com/a/b']
        self.assertEqual(len({normalize_url(url)[0] for url in variants}),len(variants))
        self.assertEqual(normalize_url('https://bücher.de/')[1],'xn--bcher-kva.de')

    def test_invalid_urls_are_not_invented(self):
        for value in ['Nog te verifiëren','/relative','www.example.com/page','javascript:alert(1)','https://user:password@example.com/','https://example.com/a\nb']:
            self.assertIsNone(normalize_url(value))

    def test_gsc_indexing_reason_is_not_an_http_measurement(self):
        data={'sheet':'Tabel','values':{'URL':'https://example.com/','Laatst gecrawld':'1970-01-01 00:00:00'}}
        result=adapt_record(self.batch('gsc'),data,{'gsc_problem':'Niet gevonden (404)'})
        self.assertEqual(result['facts']['gsc_indexing_reason'],'Niet gevonden (404)')
        self.assertNotIn('http_status',result['facts'])
        self.assertNotIn('gsc_last_crawled',result['facts'])
        self.assertIsNone(result['measured_date'])

    def test_gsc_and_ahrefs_values_have_distinct_metric_names(self):
        gsc=adapt_record(self.batch('gsc','api'),{'url':'https://example.com/','date':'2026-10-01','raw':{'clicks':12,'impressions':100,'ctr':0.12,'position':3.5}})
        ahrefs=adapt_record(self.batch('ahrefs'),{'values':{'URL':'https://example.com/','Organic traffic':'900','HTTP status code':'200','Title':'Home'}})
        self.assertEqual(gsc['facts']['gsc_clicks'],12)
        self.assertEqual(ahrefs['facts']['ahrefs_organic_traffic_estimate'],900)
        self.assertNotIn('gsc_clicks',ahrefs['facts'])
        self.assertEqual(gsc['measured_date'],datetime.date(2026,10,1))

    def test_subtree_totals_are_not_page_metrics(self):
        result=adapt_record(self.batch('audit'),{'sheet':'AHREFS site structure 26-10','values':{'URL / pad uit bronexport':'https://example.com/blog','Current organic traffic':9000}})
        self.assertEqual(result['scope'],'subtree')
        self.assertEqual(result['facts']['ahrefs_subtree_organic_traffic_estimate'],9000)
        self.assertNotIn('ahrefs_organic_traffic_estimate',result['facts'])

    def test_lastmod_and_report_period_are_not_measurement_dates(self):
        batch=self.batch('sitemap');batch.period_end=datetime.date(2026,10,31)
        result=adapt_record(batch,{'url':'https://example.com/','lastmod':'2026-01-01','sitemap_url':'https://example.com/sitemap.xml'})
        self.assertIsNone(result['measured_date'])
        self.assertEqual(result['facts']['sitemap_lastmod'],'2026-01-01')
        self.assertNotIn('http_status',result['facts'])

    def test_semscan_keeps_crawl_and_sitemap_observations_distinct(self):
        crawl=adapt_record(self.batch('semscan','scan',scan_kind='crawl'),{'url':'https://example.com/','status':200,'robots_allowed':False,'googlebot_allowed':False,'is_indexable':False})
        sitemap=adapt_record(self.batch('semscan','scan',scan_kind='sitemap'),{'url':'https://example.com/','lastmod':'2026-01-01','page_fetched':False})
        self.assertEqual(crawl['facts']['http_status'],200)
        self.assertFalse(crawl['facts']['googlebot_allowed'])
        self.assertNotIn('http_status',sitemap['facts'])
        self.assertFalse(sitemap['facts']['page_fetched'])

    def test_screaming_frog_timestamp_has_no_invented_timezone(self):
        result=adapt_record(self.batch('screaming_frog'),{'values':{'Address':'https://example.com/','Crawl Timestamp':'2026-10-14 12:07:09','Status Code':200,'Indexability':'Indexable'}})
        self.assertEqual(result['measured_date'],datetime.date(2026,10,14))
        self.assertIsNone(result['measured_at'])
        self.assertEqual(result['facts']['is_indexable'],True)

    def test_non_http_error_codes_remain_raw_only(self):
        for status in [0,999]:
            result=adapt_record(self.batch('screaming_frog'),{'values':{'Address':'https://example.com/','Status Code':status}})
            self.assertNotIn('http_status',result['facts'])


class CatalogTests(TestCase):
    def setUp(self):
        U=get_user_model()
        self.owner=U.objects.create_user('catalog-owner')
        self.viewer=U.objects.create_user('catalog-viewer')
        self.other=U.objects.create_user('catalog-other')
        self.project=Project.objects.create(name='Combined',website='https://example.com/',created_by=self.owner)
        self.hidden=Project.objects.create(name='Hidden',website='https://other.example/',created_by=self.other)
        Membership.objects.create(project=self.project,user=self.owner,role='admin')
        Membership.objects.create(project=self.project,user=self.viewer,role='viewer')
        self.client.force_login(self.owner)
        self.a=self.publish('ahrefs',[{'values':{'URL':'HTTPS://EXAMPLE.com:443/','Title':'Home','HTTP status code':'200','Organic traffic':'900','Is indexable page':'true'}}])
        self.b=self.publish('semscan',[{'url':'https://example.com/','measured_at':'2026-10-02T10:00:00+00:00','status':404,'is_indexable':False,'titles':['Missing'],'content_type':'text/html'}],method='scan')

    def publish(self,source,rows,method='upload',project=None):
        project=project or self.project
        batch=ImportBatch.objects.create(project=project,created_by=self.owner if project==self.project else self.other,source=source,method=method,label=source,request_metadata={'scan':'crawl'} if source=='semscan' else {})
        if project==self.hidden:
            Membership.objects.get_or_create(project=project,user=self.other,defaults={'role':'admin'})
        publish_records(batch_id=batch.pk,records=[{'locator':f'row:{i}','data':data} for i,data in enumerate(rows)])
        batch.refresh_from_db()
        return batch

    def url(self,project=None):
        return reverse('projects:urls:list',args=[(project or self.project).pk])

    def test_combined_identity_preserves_two_sources_and_conflicting_signals(self):
        self.assertEqual(ProjectURL.objects.filter(project=self.project).count(),1)
        result=self.client.get(self.url()).json()
        self.assertEqual(result['total'],1)
        self.assertEqual(result['urls'][0]['observation_count'],2)
        self.assertEqual(set(result['urls'][0]['sources']),{'ahrefs','semscan'})
        self.assertEqual(result['urls'][0]['http_signals'],[200,404])
        self.assertEqual(set(result['urls'][0]['indexability_signals']),{True,False})
        self.assertEqual(result['urls'][0]['unknown_dates'],1)

    def test_filters_apply_to_the_same_observation(self):
        self.assertEqual(self.client.get(self.url(),{'source':'ahrefs','http':404}).json()['total'],0)
        self.assertEqual(self.client.get(self.url(),{'source':'semscan','http':404}).json()['total'],1)

    def test_known_dates_do_not_fall_back_to_import_time(self):
        result=self.client.get(self.url(),{'date_from':'2026-10-02','date_to':'2026-10-02'}).json()
        self.assertEqual(result['urls'][0]['observation_count'],1)
        result=self.client.get(self.url(),{'date_from':'2026-10-02','date_to':'2026-10-02','include_unknown':'on'}).json()
        self.assertEqual(result['urls'][0]['observation_count'],2)

    def test_other_project_and_foreign_batch_are_rejected(self):
        batch=self.publish('gsc',[{'url':'https://other.example/'}],project=self.hidden)
        self.assertEqual(self.client.get(self.url(self.hidden)).status_code,404)
        self.assertEqual(self.client.get(self.url(),{'batch':str(batch.pk)}).status_code,400)
        item=ProjectURL.objects.get(project=self.hidden)
        self.assertEqual(self.client.get(reverse('ui:catalog:detail',args=[self.project.pk,item.pk])).status_code,404)

    def test_batch_deletion_removes_only_its_facts(self):
        item=ProjectURL.objects.get(project=self.project)
        delete_batch(batch_id=self.b.pk,project=self.project,actor=self.owner)
        self.assertTrue(ProjectURL.objects.filter(pk=item.pk).exists())
        self.assertEqual(URLObservation.objects.filter(url=item).count(),1)
        result=self.client.get(self.url()).json()
        self.assertEqual(result['urls'][0]['http_signals'],[200])
        delete_batch(batch_id=self.a.pk,project=self.project,actor=self.owner)
        self.assertFalse(ProjectURL.objects.filter(pk=item.pk).exists())

    def test_reindex_is_idempotent_and_does_not_change_raw_source(self):
        before=list(self.a.records.values_list('data',flat=True))
        item=ProjectURL.objects.get(project=self.project)
        index_batch(self.a.pk);index_batch(self.a.pk)
        self.assertEqual(URLObservation.objects.filter(batch=self.a).count(),1)
        self.assertEqual(ProjectURL.objects.get(project=self.project).pk,item.pk)
        self.assertEqual(list(self.a.records.values_list('data',flat=True)),before)

    def test_subtree_is_opt_in(self):
        self.publish('audit',[{'sheet':'AHREFS site structure 26-10','values':{'URL / pad uit bronexport':'https://example.com/blog','Current organic traffic':1000}}])
        self.assertEqual(self.client.get(self.url()).json()['total'],1)
        result=self.client.get(self.url(),{'scope':'subtree'}).json()
        self.assertEqual(result['total'],1)
        self.assertEqual(result['urls'][0]['url'],'https://example.com/blog')

    def test_gsc_coverage_metadata_is_attached_without_indexing_charts(self):
        batch=self.publish('gsc',[{'sheet':'Metadata','values':{'Property':'Probleem','Waarde':'Niet gevonden (404)'}},{'sheet':'Tabel','values':{'URL':'https://example.com/missing','Laatst gecrawld':'1970-01-01 00:00:00'}},{'sheet':'Diagram','values':{'Datum':'2026-10-01','Pagina’s':12}}])
        observation=batch.url_observations.get()
        self.assertEqual(observation.facts['gsc_indexing_reason'],'Niet gevonden (404)')
        self.assertIsNone(observation.measured_date)
        self.assertNotIn('http_status',observation.facts)

    def test_viewer_can_read_details_and_exact_original_record(self):
        self.client.force_login(self.viewer)
        item=ProjectURL.objects.get(project=self.project)
        response=self.client.get(reverse('ui:catalog:detail',args=[self.project.pk,item.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Ahrefs geschat organisch verkeer')
        self.assertContains(response,'900')
        record=self.a.records.get()
        response=self.client.get(reverse('ui:record',args=[self.project.pk,self.a.pk,record.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'HTTPS://EXAMPLE.com:443/')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('ui:record',args=[self.project.pk,self.a.pk,record.pk])).status_code,404)

    def test_html_overview_filters_render_and_escape_search(self):
        response=self.client.get(reverse('ui:catalog:overview',args=[self.project.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Verschillend per meting')
        response=self.client.get(reverse('ui:catalog:overview',args=[self.project.pk]),{'q':'<script>alert(1)</script>'})
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,'<script>alert(1)</script>')

    def test_publication_failure_rolls_back_entire_batch(self):
        batch=ImportBatch.objects.create(project=self.project,created_by=self.owner,source='ahrefs',method='upload',label='Failure')
        with patch('catalog.services.index_batch',side_effect=RuntimeError('test failure')):
            with self.assertRaises(RuntimeError):
                publish_records(batch_id=batch.pk,records=[{'locator':'row:1','data':{'url':'https://example.com/new'}}])
        batch.refresh_from_db()
        self.assertEqual(batch.status,'pending')
        self.assertEqual(batch.records.count(),0)

    def test_non_complete_and_non_url_records_do_not_appear(self):
        batch=ImportBatch.objects.create(project=self.project,created_by=self.owner,source='gsc',method='upload',label='Pending')
        SourceRecord.objects.create(batch=batch,locator='raw',data={'url':'https://example.com/pending'})
        with self.assertRaises(ValueError):
            index_batch(batch.pk)
        self.assertEqual(self.client.get(self.url()).json()['total'],1)

    def test_unrecognized_url_rows_remain_available_with_summary(self):
        batch=self.publish('gsc',[{'sheet':'Tabel','values':{'URL':'https://example.com/path\\regex'}},{'sheet':'Diagram','values':{'Datum':'2026-10-01','Pagina’s':2}}])
        self.assertEqual(batch.records.count(),2)
        self.assertEqual(batch.url_index_info['rejected_url_records'],1)
        self.assertEqual(batch.url_index_info['non_url_records'],1)
        self.assertEqual(batch.url_observations.count(),0)
        response=self.client.get(reverse('ui:batch',args=[self.project.pk,batch.pk]))
        self.assertContains(response,'geen eenduidig herkenbare URL')

    def test_api_detail_keeps_provenance_and_metrics_per_observation(self):
        item=ProjectURL.objects.get(project=self.project)
        result=self.client.get(reverse('projects:urls:detail',args=[self.project.pk,item.pk])).json()
        self.assertEqual(result['total'],2)
        self.assertEqual({row['source'] for row in result['observations']},{'ahrefs','semscan'})
        for row in result['observations']:
            self.assertEqual(row['user_id'],self.owner.pk)
            self.assertTrue(row['imported_at'])
            self.assertTrue(row['source_record_id'])

    def test_url_pagination_has_stable_order(self):
        self.publish('sitemap',[{'url':f'https://example.com/page-{i:02d}'} for i in range(30)])
        first=self.client.get(self.url()).json()
        second=self.client.get(self.url(),{'page':2}).json()
        self.assertEqual(len(first['urls']),25)
        self.assertEqual(len(second['urls']),6)
        self.assertFalse({row['id'] for row in first['urls']} & {row['id'] for row in second['urls']})
        self.assertEqual([row['url'] for row in first['urls']],sorted(row['url'] for row in first['urls']))
