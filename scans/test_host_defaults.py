import json
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase,TestCase,override_settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from projects.models import Project,Membership
from sources.models import ImportBatch
from .models import ScanTarget,HostPolicy
from .network import SafeClient,FetchResult,ScanError,normalize_url
from .scope import host_scope
from .host_rules import navigation_hosts,resource_hosts
from .resources import ResourceClient
from .tasks import check_scan


class DiscoveredHostTests(SimpleTestCase):
    config={'allowed_hosts':['example.co.uk'],'domain':'example.co.uk','include_subdomains':True,'allow_external_resources':True,'blocked_hosts':['portal.example.co.uk'],'blocked_resource_hosts':['blocked.vendor.com']}

    def test_subdomains_are_allowed_but_boundary_lookalikes_and_exclusions_are_not(self):
        hosts=navigation_hosts(self.config)
        for url in ('https://example.co.uk/','https://www.example.co.uk/','https://deep.blog.example.co.uk/page'):
            self.assertEqual(normalize_url(url,hosts),url)
        for url in ('https://example.co.uk.evil.com/','https://otherexample.co.uk/','https://portal.example.co.uk/','https://child.portal.example.co.uk/'):
            with self.assertRaises(ScanError):normalize_url(url,hosts)

    def test_external_resources_and_dependency_hops_do_not_expand_navigation(self):
        resources=resource_hosts(self.config)
        for url in ('https://fonts.googleapis.com/style.css','https://fonts.gstatic.com/font.woff2','https://new.vendor.net/dependency.js'):
            self.assertEqual(normalize_url(url,resources),url)
            with self.assertRaises(ScanError):normalize_url(url,navigation_hosts(self.config))
        for url in ('https://blocked.vendor.com/a.js','https://nested.blocked.vendor.com/a.js','http://127.0.0.1/','http://169.254.169.254/','https://user:pass@vendor.com/'):
            with self.assertRaises(ScanError):normalize_url(url,resources)

    def test_safe_client_follows_found_subdomain_redirect_but_stops_external(self):
        client=SafeClient(navigation_hosts(self.config),check_access=lambda:None)
        try:
            with patch.object(client,'fetch_once',side_effect=[FetchResult('https://example.co.uk/',301,{'location':'https://blog.example.co.uk/'},b''),FetchResult('https://blog.example.co.uk/',302,{'location':'https://elsewhere.net/'},b'')]) as fetch:
                result=client.fetch('https://example.co.uk/')
                self.assertEqual(fetch.call_count,2)
                self.assertTrue(result.blocked_redirect)
                self.assertEqual(result.blocked_destination,'https://elsewhere.net/')
        finally:client.close()

    def test_public_resource_name_resolving_to_private_ip_is_still_blocked(self):
        client=SafeClient(resource_hosts(self.config),check_access=lambda:None,respect_robots=False)
        try:
            with patch('scans.network.socket.getaddrinfo',return_value=[(2,1,6,'',('10.0.0.1',443))]),patch('scans.network.urllib3.HTTPSConnectionPool') as pool:
                with self.assertRaises(ScanError):client._request('https://vendor.com/file.js',0)
                pool.assert_not_called()
        finally:client.close()

    def test_resource_and_external_exclusions_are_independent(self):
        config={**self.config,'check_external_links':True,'blocked_external_hosts':['external.vendor.com']}
        resource=ResourceClient(config,'resource',check_access=lambda:None)
        external=ResourceClient(config,'external',check_access=lambda:None)
        try:
            self.assertTrue(resource.permitted('https://external.vendor.com/a.js'))
            self.assertFalse(external.permitted('https://sub.external.vendor.com/'))
            self.assertTrue(external.permitted('https://blocked.vendor.com/'))
            self.assertFalse(resource.permitted('https://blocked.vendor.com/a.js'))
        finally:resource.close();external.close()

    def test_nonstandard_status_never_passes_link_check(self):
        from assessments.r4 import checks
        for status in (999,199):
            for purpose in ('external','resource'):
                target=SimpleNamespace(http_status=status,outcome='http_error',purpose=purpose,measurements={},headers={})
                self.assertEqual(checks(target,102400,set())[purpose+'_http_error'],'unknown')


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'},'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'}})
class DefaultScopeWorkflowTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('default-host-owner')
        self.project=Project.objects.create(name='Scope',website='https://example.co.uk/',created_by=self.user)
        Membership.objects.create(project=self.project,user=self.user,role='admin')
        self.target=ScanTarget.objects.create(project=self.project,created_by=self.user,name='Site',url=self.project.website,allowed_hosts=['example.co.uk'])
        self.client.force_login(self.user)

    def test_new_scan_snapshots_defaults_without_individual_host_approval(self):
        HostPolicy.objects.create(project=self.project,host='fonts.googleapis.com')
        with patch('scans.views.enqueue_scan'),self.captureOnCommitCallbacks(execute=True):
            response=self.client.post(reverse('projects:scans:start',args=[self.project.pk,self.target.pk]),json.dumps({'kind':'crawl','check_external_links':False}),content_type='application/json')
        self.assertEqual(response.status_code,202,response.content)
        self.batch=ImportBatch.objects.get(pk=response.json()['id']);config=self.batch.request_metadata
        self.assertTrue(config['check_external_links']);self.assertTrue(config['check_resources'])
        self.assertTrue(config['allow_external_resources']);self.assertEqual(config['host_policy_version'],'discovered-hosts-v2')
        self.assertIn('news.example.co.uk',navigation_hosts(config))
        self.assertIn('fonts.googleapis.com',resource_hosts(config))
        HostPolicy.objects.create(project=self.project,host='news.example.co.uk',navigation='blocked')
        with self.assertRaises(PermissionDenied):check_scan(self.batch.pk)

    def test_private_suffix_does_not_include_other_tenants(self):
        self.target.url='https://tenant.github.io/';self.target.allowed_hosts=['tenant.github.io'];self.target.save()
        scope=host_scope(self.target)
        self.assertIn('sub.tenant.github.io',navigation_hosts(scope))
        self.assertNotIn('other.github.io',navigation_hosts(scope))

    def test_ui_exposes_exclusions_instead_of_three_permission_questions(self):
        HostPolicy.objects.create(project=self.project,host='fonts.googleapis.com')
        response=self.client.get(reverse('ui:scans',args=[self.project.pk]))
        self.assertContains(response,'Uitsluitingen (optioneel)')
        self.assertContains(response,'Gebruikte externe bestanden worden geladen')
        self.assertNotContains(response,'Geen antwoord betekent')

    def test_explicit_allow_reset_to_default_does_not_revoke_domain_scope(self):
        policy=HostPolicy.objects.create(project=self.project,host='blog.example.co.uk',navigation='allowed')
        config={**host_scope(self.target),'scan':'crawl','url':self.target.url}
        batch=ImportBatch.objects.create(project=self.project,created_by=self.user,scan_target=self.target,source='semscan',method='scan',request_metadata=config)
        policy.navigation='pending';policy.save()
        self.assertEqual(check_scan(batch.pk).pk,batch.pk)
        HostPolicy.objects.create(project=self.project,host='external.vendor.com',navigation='blocked')
        self.assertEqual(check_scan(batch.pk).pk,batch.pk)
