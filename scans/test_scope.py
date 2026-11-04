import json
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, Client
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from projects.models import Project, Membership
from sources.models import ImportBatch
from .models import ScanTarget, HostPolicy
from .scope import registered_domain, default_hosts, host_scope, HostDiscovery
from .forms import TargetForm
from .network import FetchResult, SafeClient
from .outcomes import summarize
from .tasks import check_scan


class DomainTests(SimpleTestCase):
    def test_suffixes_private_tenants_and_wildcard_exceptions(self):
        for host,expected in [('www.company.co.uk','company.co.uk'),('a.b.example.nl','example.nl'),
                              ('a.tenant.github.io','tenant.github.io'),('www.city.kawasaki.jp','city.kawasaki.jp'),
                              ('a.b.ck','a.b.ck'),('www.ck','www.ck')]:
            self.assertEqual(registered_domain(host),expected)

    def test_default_www_pair_without_broadening_other_subdomains(self):
        self.assertEqual(default_hosts('https://www.example.co.uk/path?q=1'),{'example.co.uk','www.example.co.uk'})
        self.assertEqual(default_hosts('https://blog.example.co.uk/'),{'blog.example.co.uk'})
        self.assertEqual(default_hosts('https://8.8.8.8/'),{'8.8.8.8'})

    def test_preserves_start_path_query_and_normalizes_idn(self):
        form=TargetForm({'name':'Site','kind':'own','url':'https://www.bücher.de/route?q=1#anchor'})
        self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual(form.cleaned_data['url'],'https://www.xn--bcher-kva.de/route?q=1')
        self.assertEqual(form.cleaned_data['allowed_hosts'],['www.xn--bcher-kva.de','xn--bcher-kva.de'])

    def test_redirect_www_followed_other_host_recorded_not_requested(self):
        client=SafeClient(default_hosts('https://example.com/'),check_access=lambda:None)
        try:
            with patch.object(client,'fetch_once',side_effect=[FetchResult('https://example.com/',301,{'location':'https://www.example.com/'},b''),FetchResult('https://www.example.com/',200,{},b'ok')]) as fetch:
                self.assertEqual(client.fetch('https://example.com/').status,200)
                self.assertEqual(fetch.call_count,2)
            with patch.object(client,'fetch_once',return_value=FetchResult('https://example.com/',301,{'location':'https://blog.example.com/'},b'')) as fetch:
                result=client.fetch('https://example.com/')
                self.assertTrue(result.blocked_redirect)
                self.assertEqual(result.blocked_destination,'https://blog.example.com/')
                self.assertEqual(fetch.call_count,1)
                self.assertTrue(summarize([{'data':{'status':301,'blocked_redirect':result.blocked_redirect}}])['incomplete'])
        finally:client.close()


class ScopeAccessTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('host-admin')
        self.viewer=get_user_model().objects.create_user('host-viewer')
        self.project=Project.objects.create(name='Hosts',website='https://example.com/',created_by=self.user)
        Membership.objects.create(project=self.project,user=self.user,role='admin')
        Membership.objects.create(project=self.project,user=self.viewer,role='viewer')
        self.target=ScanTarget.objects.create(project=self.project,created_by=self.user,name='Site',url='https://example.com/path',allowed_hosts=['example.com'])
        self.batch=ImportBatch.objects.create(project=self.project,created_by=self.user,scan_target=self.target,source='semscan',method='scan',label='Test',request_metadata={'scan':'crawl','url':self.target.url,'allowed_hosts':['example.com','www.example.com']})
        self.client.force_login(self.user)

    def decision(self,policy,**data):
        return self.client.post(reverse('projects:scans:host-decision',args=[self.project.pk,policy.pk]),json.dumps(data),content_type='application/json')

    def test_discovery_never_fetches_and_keeps_prior_decision(self):
        observe=HostDiscovery(self.batch)
        observe.observe(['https://example.com/style.css'],'https://example.com/','resources')
        self.assertFalse(HostPolicy.objects.exists())
        observe.observe(['https://blog.example.com/a','https://example.com.evil.org/','https://tenant.github.io/'],'https://example.com/')
        policy=HostPolicy.objects.get()
        self.assertEqual((policy.host,policy.navigation),('blog.example.com','pending'))
        self.assertNotIn(policy.host,host_scope(self.target)['allowed_hosts'])
        self.assertEqual(self.decision(policy,navigation='allowed',resources='blocked').status_code,200)
        HostDiscovery(self.batch).observe(['https://blog.example.com/b'],'https://example.com/')
        policy.refresh_from_db()
        self.assertEqual(policy.navigation,'allowed')
        self.assertIn(policy.host,host_scope(self.target)['allowed_hosts'])
        self.assertIn(policy.host,host_scope(self.target)['blocked_resource_hosts'])

    def test_block_overrides_default_and_revokes_running_scan(self):
        HostPolicy.objects.create(project=self.project,host='www.example.com',navigation='blocked')
        self.assertNotIn('www.example.com',host_scope(self.target)['allowed_hosts'])
        with self.assertRaises(PermissionDenied):check_scan(self.batch.pk)

    def test_resource_permission_is_not_navigation_permission(self):
        HostPolicy.objects.create(project=self.project,host='cdn.example.com',resources='allowed')
        scope=host_scope(self.target)
        self.assertIn('cdn.example.com',scope['resource_hosts'])
        self.assertNotIn('cdn.example.com',scope['allowed_hosts'])

    def test_new_resource_block_revokes_browser_but_existing_block_is_supported(self):
        from browser_scans.models import BrowserRun
        from browser_scans.services import check_run
        batch=ImportBatch.objects.create(project=self.project,created_by=self.user,scan_target=self.target,source='semscan',method='scan',label='Browser',request_metadata={'scan':'browser'})
        run=BrowserRun.objects.create(batch=batch,source_batch=self.batch,config={'allowed_hosts':['example.com'],'resource_hosts':[]})
        HostPolicy.objects.create(project=self.project,host='example.com',resources='blocked')
        with self.assertRaises(PermissionDenied):check_run(run.pk)
        run.config['blocked_resource_hosts']=['example.com'];run.save(update_fields=['config'])
        self.assertEqual(check_run(run.pk).pk,run.pk)

    def test_update_existing_target_and_render_project_decisions(self):
        response=self.client.post(reverse('projects:scans:target-update',args=[self.project.pk,self.target.pk]),json.dumps({'name':'Updated','kind':'own','url':'https://www.example.com/a?q=1','allowed_hosts':['blog.example.com'],'blocked_hosts':['accounts.example.com'],'resource_hosts':['cdn.example.com']}),content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.target.refresh_from_db()
        self.assertEqual(self.target.url,'https://www.example.com/a?q=1')
        page=self.client.get(reverse('ui:scans',args=[self.project.pk]))
        self.assertContains(page,'Website en hostinstellingen bewerken')
        self.assertContains(page,'accounts.example.com')
        self.assertContains(page,'value="blocked" selected')

    def test_new_target_does_not_clear_existing_project_block(self):
        HostPolicy.objects.create(project=self.project,host='www.example.com',navigation='blocked')
        response=self.client.post(reverse('projects:scans:target-create',args=[self.project.pk]),json.dumps({'name':'New','url':'https://example.com/','kind':'own'}),content_type='application/json')
        self.assertEqual(response.status_code,201)
        self.assertNotIn('www.example.com',response.json()['allowed_hosts'])

    def test_permissions_project_scope_and_csrf(self):
        policy=HostPolicy.objects.create(project=self.project,host='blog.example.com')
        self.client.force_login(self.viewer)
        self.assertEqual(self.decision(policy,navigation='allowed',resources='pending').status_code,403)
        self.client.force_login(self.user)
        other=Project.objects.create(name='Other',website='https://other.com',created_by=self.user)
        stranger=HostPolicy.objects.create(project=other,host='other.com')
        self.assertEqual(self.decision(stranger,navigation='allowed',resources='pending').status_code,404)
        csrf=Client(enforce_csrf_checks=True);csrf.force_login(self.user)
        self.assertEqual(csrf.post(reverse('projects:scans:host-decision',args=[self.project.pk,policy.pk]),'{}',content_type='application/json').status_code,403)

    def test_start_snapshots_scope_and_preserves_entered_url(self):
        with patch('scans.views.enqueue_scan'),self.captureOnCommitCallbacks(execute=True):
            response=self.client.post(reverse('projects:scans:start',args=[self.project.pk,self.target.pk]),json.dumps({'kind':'crawl'}),content_type='application/json')
        self.assertEqual(response.status_code,202)
        batch=ImportBatch.objects.exclude(pk=self.batch.pk).get()
        self.assertEqual(batch.request_metadata['url'],'https://example.com/path')
        self.assertEqual(batch.request_metadata['allowed_hosts'],['example.com','www.example.com'])
