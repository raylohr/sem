import datetime
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from projects.models import Project, Membership
from sources.models import ImportBatch
from sources.services import publish_records
from .queries import url_overview, summarize_urls
from .models import ProjectURL


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}, 'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}})
class TrafficColumnTests(TestCase):
    def setUp(self):
        self.actor=get_user_model().objects.create_user(username='traffic-editor')
        self.project=Project.objects.create(name='Traffic',website='https://example.com/',created_by=self.actor)
        Membership.objects.create(project=self.project,user=self.actor,role='admin')
        self.client.force_login(self.actor)

    def publish(self, traffic, measured_at=None):
        batch=ImportBatch.objects.create(project=self.project,created_by=self.actor,source='ahrefs',method='upload',label='Ahrefs',measured_at=measured_at)
        records=[]
        for i,(path,value) in enumerate(traffic.items()):
            values={'URL':'https://example.com/'+path,'Content type':'text/html','HTTP status code':200}
            if value is not None:values['Organic traffic']=value
            records.append({'locator':str(i),'data':{'values':values}})
        publish_records(batch_id=batch.pk,records=records)
        return batch

    def summarize(self, filters=None):
        urls,obs=url_overview(self.project,filters or {})
        return {u.normalized_url:u.ahrefs_traffic for u in summarize_urls(urls,obs)}

    def test_latest_estimate_is_not_summed_and_zero_is_retained(self):
        self.publish({'a/':100,'zero/':0,'unknown/':None})
        self.publish({'a/':20})
        self.assertEqual(self.summarize(),{'https://example.com/a/':20,'https://example.com/zero/':0,'https://example.com/unknown/':None})
        response=self.client.get(reverse('ui:catalog:overview',args=[self.project.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'>Traffic</span>')
        self.assertNotContains(response,'<th>Indexeerbaarheid</th>')

    def test_measurement_date_and_active_filters_are_respected(self):
        newest=self.publish({'a/':30},datetime.datetime(2026,10,29,tzinfo=datetime.timezone.utc))
        self.publish({'a/':90},datetime.datetime(2026,10,1,tzinfo=datetime.timezone.utc))
        self.assertEqual(self.summarize()['https://example.com/a/'],30)
        self.assertEqual(self.summarize({'batch':newest})['https://example.com/a/'],30)

    def test_numeric_sorting_applies_before_pagination_and_keeps_unknown_last(self):
        self.publish({f'{i:02d}/':v for i,v in enumerate([5,100,20,0]+list(range(6,32))+[None])})
        url=reverse('ui:catalog:overview',args=[self.project.pk])
        response=self.client.get(url,{'sort':'traffic','direction':'desc'})
        page=response.context['page']
        values=[u.ahrefs_traffic for u in page.object_list]
        self.assertEqual(values[:3],[100,31,30])
        self.assertEqual(len(values),25)
        self.assertContains(response,'aria-sort="descending"')
        self.assertContains(response,'name="sort" value="traffic"')
        second=self.client.get(url,{'sort':'traffic','direction':'desc','page':2}).context['page']
        self.assertIsNone(second.object_list[-1].ahrefs_traffic)
        self.assertFalse({u.pk for u in page}&{u.pk for u in second})
        asc=self.client.get(url,{'sort':'traffic','direction':'asc'}).context['page']
        self.assertEqual([u.ahrefs_traffic for u in asc][:3],[0,5,6])

    def test_latest_traffic_used_for_sort_and_filters_retained_in_toggles(self):
        self.publish({'a/':100,'b/':90})
        self.publish({'a/':20})
        response=self.client.get(reverse('ui:catalog:overview',args=[self.project.pk]),
            {'sort':'traffic','direction':'desc','source':'ahrefs','q':'example.com','page':2})
        self.assertEqual([u.ahrefs_traffic for u in response.context['page']],[90,20])
        columns={c['key']:c for c in response.context['columns']}
        from urllib.parse import parse_qs
        self.assertEqual(parse_qs(columns['traffic']['query']),
            {'sort':['traffic'],'direction':['asc'],'source':['ahrefs'],'q':['example.com']})

    def test_all_columns_are_supported(self):
        self.publish({'a/':10,'b/':20,'c/':None})
        url=reverse('ui:catalog:overview',args=[self.project.pk])
        for column in ['url','sources','http','traffic','date']:
            for direction in ['asc','desc']:
                response=self.client.get(url,{'sort':column,'direction':direction})
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.context['url_count'],3)
        self.assertEqual(self.client.get(url,{'sort':'invalid'}).status_code,400)
        self.assertEqual(self.client.get(url,{'direction':'invalid'}).status_code,400)
