import json
from unittest.mock import patch
from django.test import TestCase,Client,override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from projects.models import Project,Membership
from sources.models import ImportBatch
from .models import ScanTarget

@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'},'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'}})
class SavedSettingsTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('settings-owner')
        self.reader=get_user_model().objects.create_user('settings-reader')
        self.project=Project.objects.create(name='Saved config',website='https://example.com/',created_by=self.user)
        Membership.objects.create(project=self.project,user=self.user,role='admin')
        Membership.objects.create(project=self.project,user=self.reader,role='viewer')
        self.target=ScanTarget.objects.create(project=self.project,created_by=self.user,name='Site',url=self.project.website,allowed_hosts=['example.com'])
        self.client.force_login(self.user)
        self.url=reverse('projects:scans:settings',args=[self.project.pk,self.target.pk])

    def save(self,**values):return self.client.post(self.url,json.dumps({'kind':'crawl',**values}),content_type='application/json')

    def test_save_reload_new_crawl_and_frozen_existing_config(self):
        response=self.save(max_urls=1234,max_depth=7,read_timeout=90,browser_max_pages=12,check_resources=False,schema_profiles=[])
        self.assertEqual(response.status_code,200,response.content)
        self.target.refresh_from_db();self.assertEqual(self.target.scan_settings['crawl']['max_urls'],1234)
        page=self.client.get(reverse('ui:scans',args=[self.project.pk]))
        self.assertContains(page,'name="max_urls" data-value-type="integer" data-min="1" data-max="100000" value="1234"')
        self.assertContains(page,'name="browser_max_pages" value="12"')
        with patch('scans.views.enqueue_scan'),self.captureOnCommitCallbacks(execute=True):
            started=self.client.post(reverse('projects:scans:start',args=[self.project.pk,self.target.pk]),json.dumps({'kind':'crawl'}),content_type='application/json')
        self.assertEqual(started.status_code,202,started.content)
        batch=ImportBatch.objects.get(pk=started.json()['id'])
        self.assertEqual(batch.request_metadata['max_urls'],1234)
        self.assertFalse(batch.request_metadata['check_resources']);self.assertEqual(batch.request_metadata['schema_profiles'],[])
        self.save(max_urls=2222)
        batch.refresh_from_db();self.assertEqual(batch.request_metadata['max_urls'],1234)

    def test_invalid_settings_keep_previous_values(self):
        self.assertEqual(self.save(max_urls=500).status_code,200)
        self.assertEqual(self.save(max_urls=100001).status_code,400)
        self.assertEqual(self.save(url='https://evil.net/').status_code,400)
        self.assertEqual(self.save(title_min_chars=80,title_max_chars=20).status_code,400)
        self.target.refresh_from_db();self.assertEqual(self.target.scan_settings['crawl']['max_urls'],500)

    def test_modes_and_targets_are_isolated_but_shared_with_project_editors(self):
        self.save(max_urls=500)
        response=self.client.post(self.url,json.dumps({'kind':'sitemap','max_urls':321}),content_type='application/json')
        self.assertEqual(response.status_code,200)
        self.target.refresh_from_db()
        self.assertEqual(self.target.scan_settings['crawl']['max_urls'],500)
        self.assertEqual(self.target.scan_settings['sitemap']['max_urls'],321)
        other=ScanTarget.objects.create(project=self.project,created_by=self.user,name='Other',url='https://other.net/',allowed_hosts=['other.net'])
        self.assertEqual(other.scan_settings,{})
        editor=get_user_model().objects.create_user('settings-editor')
        Membership.objects.create(project=self.project,user=editor,role='editor')
        self.client.force_login(editor)
        self.assertEqual(self.save(max_urls=600).status_code,200)
        self.target.refresh_from_db();self.assertEqual(self.target.scan_settings['crawl']['max_urls'],600)

    def test_permissions_project_boundary_and_csrf(self):
        self.client.force_login(self.reader);self.assertEqual(self.save(max_urls=500).status_code,403)
        self.client.force_login(self.user)
        project=Project.objects.create(name='Other',website='https://other.net/',created_by=self.reader)
        foreign=ScanTarget.objects.create(project=project,created_by=self.reader,name='Other',url='https://other.net/')
        self.assertEqual(self.client.post(reverse('projects:scans:settings',args=[self.project.pk,foreign.pk]),json.dumps({'kind':'crawl'}),content_type='application/json').status_code,404)
        csrf=Client(enforce_csrf_checks=True);csrf.force_login(self.user)
        self.assertEqual(csrf.post(self.url,json.dumps({'kind':'crawl'}),content_type='application/json').status_code,403)
