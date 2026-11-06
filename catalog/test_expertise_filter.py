import csv
import io
import zipfile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from projects.models import Project, Membership
from sources.models import ImportBatch
from sources.services import publish_records
from .forms import URLFilterForm
from .labels import label_key
from .models import Label, URLLabel, ProjectURL


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}, 'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}})
class ExpertiseFilterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='filter-editor')
        self.project = Project.objects.create(name='Labels',website='https://example.com/',created_by=self.user)
        Membership.objects.create(project=self.project,user=self.user,role='admin')
        self.client.force_login(self.user)
        batch=ImportBatch.objects.create(project=self.project,source='ahrefs',method='upload',label='Import',created_by=self.user)
        publish_records(batch_id=batch.pk,records=[{'locator':f'row:{i}','data':{'url':f'https://example.com/{name}/','HTTP status code':status,'Content Type':'text/html'}} for i,(name,status) in enumerate([('a',200),('b',404),('c',200)])])
        self.expertise=Label.objects.create(project=self.project,name='Arbeidsrecht',key=label_key('Arbeidsrecht'),kind='source')
        self.topic=Label.objects.create(project=self.project,name='Onderwerp',key=label_key('Onderwerp'),label_type='topic')
        self.empty=Label.objects.create(project=self.project,name='Erfrecht',key=label_key('Erfrecht'))
        for name in ['a','b']:
            URLLabel.objects.create(url=ProjectURL.objects.get(project=self.project,normalized_url=f'https://example.com/{name}/'),label=self.expertise)

    def test_dropdown_uses_type_rather_than_source_classification(self):
        form=URLFilterForm({},project=self.project)
        self.assertEqual(set(form.fields['expertise'].queryset),{self.expertise,self.empty})
        response=self.client.get(reverse('ui:catalog:overview',args=[self.project.pk]),{'expertise':self.expertise.pk})
        self.assertContains(response,'name="expertise"')
        self.assertContains(response,'class="collapse show"')
        self.assertEqual(response.context['url_count'],2)
        self.assertIn('expertise=',response.context['filter_query'])

    def test_filter_combines_with_measurements_and_empty_selection(self):
        url=reverse('projects:urls:list',args=[self.project.pk])
        response=self.client.get(url,{'expertise':self.expertise.pk,'http':200}).json()
        self.assertEqual([r['url'] for r in response['urls']],['https://example.com/a/'])
        self.assertEqual(response['observation_count'],1)
        self.assertEqual(self.client.get(url,{'expertise':self.empty.pk}).json()['total'],0)
        self.assertEqual(self.client.get(url).json()['total'],3)

    def test_other_types_and_foreign_project_labels_are_rejected(self):
        foreign=Project.objects.create(name='Other',website='https://other.test/',created_by=self.user)
        label=Label.objects.create(project=foreign,name='Private',key=label_key('Private'))
        for value in [self.topic.pk,label.pk,'invalid']:
            response=self.client.get(reverse('projects:urls:list',args=[self.project.pk]),{'expertise':value})
            self.assertEqual(response.status_code,400)

    def test_export_uses_same_expertise_selection(self):
        response=self.client.get(reverse('ui:catalog:export',args=[self.project.pk]),{'expertise':self.expertise.pk,'http':200})
        with zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content))) as archive:
            rows=list(csv.DictReader(io.StringIO(archive.read('Data-01.csv').decode('utf-8-sig'))))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['normalized_url'],'https://example.com/a/')
