from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from projects.models import Project, Membership
from .models import ImportBatch, SourceRecord


class URLDetailTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('url-detail-user')
        self.project=Project.objects.create(name='Details',website='https://example.com',domain='example.com',created_by=self.user)
        Membership.objects.create(project=self.project,user=self.user,role='viewer')
        self.client.force_login(self.user)
        self.url='https://example.com/Path/?a=1&b=2'
        self.route=reverse('ui:url-detail',args=[self.project.pk])

    def add(self,source,data,*,status='complete',project=None,metadata=None):
        batch=ImportBatch.objects.create(project=project or self.project,created_by=self.user,source=source,
            method='file',label='Details '+source,status=status,request_metadata=metadata or {})
        return SourceRecord.objects.create(batch=batch,locator='row:1',data=data)

    def test_title_links_to_exact_url_detail_and_preserves_external_url_link(self):
        self.add('sf',{'Address':self.url,'Title 1':'SF titel','Crawl Timestamp':'2026-10-14','Status Code':'200'})
        self.add('ahrefs',{'URL':self.url,'Title':'Ahrefs titel','last_seen':'2026-11-01'})
        self.add('gsc',{'URL':self.url,'Last crawled':'2026-11-02','gsc_indexing_reason':'Niet gevonden (404)'},metadata={'indexing_reason':'Niet gevonden (404)'})
        response=self.client.get(reverse('ui:project',args=[self.project.pk]))
        self.assertContains(response,self.route)
        detail=self.client.get(self.route,{'url':self.url})
        self.assertEqual(detail.status_code,200)
        self.assertContains(detail,'SF titel')
        self.assertContains(detail,'Ahrefs titel')
        self.assertContains(detail,'Niet gevonden (404)')
        self.assertEqual([source['key'] for source in detail.context['url_sources']],['ahrefs','gsc','sf'])
        self.assertEqual(detail.context['url_item']['url'],self.url)
        self.assertContains(detail,'col-12 col-lg-6')

    def test_dynamic_sources_fields_nested_values_and_deletion(self):
        self.add('sf',{'Address':self.url,'Title 1':'Titel','Crawl Timestamp':'2026-10-14'})
        future=self.add('future_source',{'url':self.url,'date':'2026-11-03','New field':'Added dynamically',
            'nested':{'clicks':0,'active':False,'values':['one','two']},'unsafe':'<script>alert(1)</script>'})
        response=self.client.get(self.route,{'url':self.url})
        self.assertContains(response,'Added dynamically')
        self.assertContains(response,'nested → clicks')
        self.assertContains(response,'&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response,'<script>alert(1)</script>')
        future.data.pop('New field');future.save(update_fields=['data'])
        self.assertNotContains(self.client.get(self.route,{'url':self.url}),'Added dynamically')
        future.batch.delete()
        response=self.client.get(self.route,{'url':self.url})
        self.assertNotContains(response,'future_source')
        self.assertEqual(len(response.context['url_sources']),1)

    def test_all_observations_retained_sorted_and_removed_import_disappears(self):
        old=self.add('sf',{'Address':self.url,'Title 1':'Old value','Crawl Timestamp':'2026-10-14'})
        latest=self.add('sf',{'Address':self.url,'Title 1':'Newest value','Crawl Timestamp':'2026-11-03'})
        response=self.client.get(self.route,{'url':self.url})
        self.assertContains(response,'Old value')
        self.assertContains(response,'Newest value')
        observations=response.context['url_sources'][0]['observations']
        self.assertEqual([o['record'].pk for o in observations],[latest.pk,old.pk])
        self.assertContains(response,reverse('ui:record',args=[self.project.pk,old.batch_id,old.pk]))
        latest.batch.delete()
        self.assertNotContains(self.client.get(self.route,{'url':self.url}),'Newest value')
        old.batch.delete()
        self.assertEqual(self.client.get(self.route,{'url':self.url}).status_code,404)

    def test_project_status_host_and_exact_primary_url_boundaries(self):
        self.add('sf',{'Address':self.url,'Title 1':'Pending secret'},status='pending')
        other=Project.objects.create(name='Other',website='https://example.com',created_by=self.user)
        self.add('sf',{'Address':self.url,'Title 1':'Other secret'},project=other)
        self.add('sf',{'Address':'https://example.com/other','URL':self.url,'Title 1':'Secondary URL'})
        self.assertEqual(self.client.get(self.route,{'url':self.url}).status_code,404)
        self.add('sf',{'Address':self.url,'Title 1':'Visible'})
        for url in ('https://external.test/','https://blog.example.com/a','javascript:alert(1)'):
            self.assertEqual(self.client.get(self.route,{'url':url}).status_code,404)
        self.assertEqual(self.client.get(self.route).status_code,404)
        self.client.force_login(get_user_model().objects.create_user('detail-outsider'))
        self.assertEqual(self.client.get(self.route,{'url':self.url}).status_code,404)
        self.client.logout()
        self.assertEqual(self.client.get(self.route,{'url':self.url}).status_code,302)
