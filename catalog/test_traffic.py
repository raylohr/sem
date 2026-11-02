import datetime
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from projects.models import Project, Membership
from sources.models import ImportBatch
from sources.services import publish_records
from .queries import url_overview, summarize_urls


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
