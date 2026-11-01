from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from projects.models import Project, Membership
from sources.models import ImportBatch
from scans.models import ScanTarget


class WorkspaceTests(TestCase):
    def setUp(self):
        U=get_user_model()
        self.owner=U.objects.create_user('ui-owner')
        self.viewer=U.objects.create_user('ui-viewer')
        self.other=U.objects.create_user('ui-other')
        self.project=Project.objects.create(name='Visible project',website='https://example.com/',created_by=self.owner)
        self.hidden=Project.objects.create(name='Hidden project',website='https://example.org/',created_by=self.other)
        Membership.objects.create(project=self.project,user=self.owner,role='admin')
        Membership.objects.create(project=self.project,user=self.viewer,role='viewer')
        self.batch=ImportBatch.objects.create(project=self.project,created_by=self.owner,source='ahrefs',method='upload',label='<script>untrusted</script>')
        self.client.force_login(self.owner)

    def test_dashboard_is_html_and_scoped(self):
        response=self.client.get(reverse('ui:dashboard'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Visible project')
        self.assertNotContains(response,'Hidden project')
        self.assertTrue(response['Content-Type'].startswith('text/html'))

    def test_project_pages_render(self):
        for name in ('project','sources','scans','connections'):
            with self.subTest(name=name):
                response=self.client.get(reverse('ui:'+name,args=[self.project.pk]))
                self.assertEqual(response.status_code,200)
                self.assertContains(response,'/vendor/bootstrap/5.3.8/bootstrap.min.')
                self.assertContains(response,'/vendor/bootstrap/5.3.8/bootstrap.bundle.min.')
                self.assertNotContains(response,'cdn.jsdelivr.net')

    def test_hidden_project_404_on_all_pages(self):
        for name in ('project','sources','scans','connections'):
            self.assertEqual(self.client.get(reverse('ui:'+name,args=[self.hidden.pk])).status_code,404)

    def test_batch_from_other_project_not_visible(self):
        batch=ImportBatch.objects.create(project=self.hidden,created_by=self.other,source='gsc',method='api',label='Hidden')
        self.assertEqual(self.client.get(reverse('ui:batch',args=[self.project.pk,batch.pk])).status_code,404)

    def test_labels_are_escaped(self):
        response=self.client.get(reverse('ui:batch',args=[self.project.pk,self.batch.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'&lt;script&gt;untrusted&lt;/script&gt;')
        self.assertNotContains(response,'<script>untrusted</script>')

    def test_viewer_has_no_upload_or_delete_controls(self):
        self.client.force_login(self.viewer)
        response=self.client.get(reverse('ui:sources',args=[self.project.pk]))
        self.assertNotContains(response,'name="file"')
        response=self.client.get(reverse('ui:batch',args=[self.project.pk,self.batch.pk]))
        self.assertNotContains(response,'Definitief verwijderen')

    def test_scans_show_target_and_start_form(self):
        ScanTarget.objects.create(project=self.project,created_by=self.owner,name='Own website',url='https://example.com/',allowed_hosts=['example.com'])
        response=self.client.get(reverse('ui:scans',args=[self.project.pk]))
        self.assertContains(response,'Own website')
        self.assertContains(response,'Onderzoek starten')

    def test_json_api_is_unchanged(self):
        response=self.client.get(reverse('projects:list'))
        self.assertTrue(response['Content-Type'].startswith('application/json'))
        self.assertEqual(response.json()['projects'][0]['name'],'Visible project')

    def test_login_uses_only_local_assets(self):
        self.client.logout()
        response=self.client.get(reverse('login'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'/static/vendor/bootstrap/5.3.8/')
        self.assertContains(response,'autocomplete="current-password"')
        self.assertNotContains(response,'https://cdn.')

    def test_anonymous_workspace_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('ui:dashboard')).status_code,302)
