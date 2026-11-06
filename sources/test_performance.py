from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from projects.models import Project, Membership
from .models import ImportBatch, SourceRecord
from .url_inventory import project_inventory
from .performance import scatter_data


class PerformanceTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('performance')
        self.project=Project.objects.create(name='Performance',website='https://example.com',domain='example.com',created_by=self.user)
        Membership.objects.create(project=self.project,user=self.user,role='viewer')
        self.client.force_login(self.user)
        self.url=reverse('ui:performance',args=[self.project.pk])
        self.batch=ImportBatch.objects.create(project=self.project,source='ahrefs',method='file',created_by=self.user,status='complete',label='Pages',request_metadata={'type':'ahrefs_site_audit_pages'})

    def add(self,path,**values):
        data={'URL':'https://example.com/'+path,'Title':'Page '+path,'Content type':'text/html; charset=utf-8','Organic traffic':'10','No. of backlinks':'5'}
        data.update(values)
        return SourceRecord.objects.create(batch=self.batch,locator=path,data=data)

    def test_default_chart_keeps_zero_and_omits_non_html_and_missing_values(self):
        self.add('zero',**{'Organic traffic':'0','No. of backlinks':'0'})
        self.add('normal')
        self.add('image',**{'Content type':'image/png'})
        self.add('missing',**{'Organic traffic':''})
        self.add('invalid',**{'No. of backlinks':'NaN'})
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['performance_x']['path'],['No. of backlinks'])
        points=response.context['performance_points']
        self.assertEqual(len(points),2)
        self.assertTrue(any(p['x']==p['y']==0 for p in points))
        self.assertEqual(response.context['performance_html_count'],4)
        self.assertEqual(response.context['performance_missing_count'],2)
        self.assertContains(response,'data-performance-chart')
        self.assertContains(response,'aria-current="page"')

    def test_sf_content_type_overrides_ahrefs_and_numeric_x_is_configurable(self):
        self.add('a',Depth='3')
        self.add('b',Depth='8')
        sf=ImportBatch.objects.create(project=self.project,source='sf',method='file',created_by=self.user,status='complete',label='SF')
        SourceRecord.objects.create(batch=sf,locator='a',data={'Address':'https://example.com/a','Content Type':'application/pdf','Status Code':'200'})
        _,rows=project_inventory(self.project,include_fields=True)
        data=scatter_data(rows)
        depth=next(f for f in data['performance_fields'] if f['path']==['Depth'])
        response=self.client.get(self.url,{'x':depth['id']})
        self.assertEqual(response.context['performance_points'],[{'url':'https://example.com/b','title':'Page b','x':8.0,'y':10.0}])
        self.assertEqual(self.client.get(self.url,{'x':'foreign-id'}).context['performance_x']['path'],['No. of backlinks'])

    def test_scope_permissions_host_rules_and_missing_backlinks(self):
        record=self.add('a')
        record.data.pop('No. of backlinks');record.save()
        record=self.add('foreign');record.data['URL']='https://external.test/a';record.save()
        response=self.client.get(self.url)
        self.assertIsNone(response.context['performance_x'])
        self.assertEqual(response.context['performance_points'],[])
        self.assertContains(response,'Aantal backlinks (niet beschikbaar)')
        Membership.objects.filter(project=self.project,user=self.user).delete()
        self.assertEqual(self.client.get(self.url).status_code,404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code,302)
