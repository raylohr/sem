from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, SimpleTestCase
from django.urls import reverse

from accounts.models import AuditEvent
from projects.models import Project, Membership
from sources.models import ImportBatch, SourceRecord
from .models import PlaywrightCrawlSettings
from .playwright_config import DESKTOP_PROFILES, choose_desktop_profile, in_scope, clean_urls, is_excluded
from .playwright_forms import PlaywrightCrawlForm
from .playwright_views import save_configuration


class ScopeTests(SimpleTestCase):
    def setUp(self):
        from types import SimpleNamespace
        self.project = SimpleNamespace(domain='example.test', website='https://www.example.test/', include_subdomains=True)

    def test_domain_boundaries_include_www_and_subdomains_without_suffix_confusion(self):
        for url in ('https://example.test/a', 'https://www.example.test/a', 'https://blog.example.test/a'):
            self.assertTrue(in_scope(self.project,url))
        for url in ('https://example.test.evil.test/', 'https://notexample.test/', 'https://other.test/',
                    'https://name:secret@example.test/', 'file:///tmp/test', 'http://127.0.0.1/', 'https://example.test:8443/'):
            self.assertFalse(in_scope(self.project,url),url)
        self.assertFalse(in_scope(self.project,'https://blog.example.test/',include_subdomains=False))
        self.assertTrue(in_scope(self.project,'https://www.example.test/',include_subdomains=False))

    def test_url_lists_are_normalized_and_deduplicated_but_keep_functional_queries(self):
        self.assertEqual(clean_urls(['https://EXAMPLE.test:443/a#section','https://example.test/a','https://example.test/a?language=en'],self.project),
                         ['https://example.test/a','https://example.test/a?language=en'])
        with self.assertRaises(ValidationError):
            clean_urls(['https://other.test/'],self.project)

    def test_excluded_host_trees_and_path_prefixes_do_not_match_unrelated_paths(self):
        self.assertTrue(is_excluded('https://a.staging.example.test/', ['staging.example.test'], []))
        self.assertTrue(is_excluded('https://example.test/admin/item', [], ['/admin']))
        self.assertTrue(is_excluded('https://example.test/path/../admin', [], ['/admin']))
        self.assertFalse(is_excluded('https://example.test/administrator', [], ['/admin']))
        self.assertFalse(is_excluded('https://example.test/?next=/admin', [], ['/admin']))

    def test_desktop_pool_has_ten_profiles_and_no_pinned_user_agent_versions(self):
        self.assertEqual(len(DESKTOP_PROFILES),10)
        self.assertIn(choose_desktop_profile().key,DESKTOP_PROFILES)
        self.assertEqual(choose_desktop_profile('firefox_linux').engine,'firefox')
        with self.assertRaises(ValidationError):
            choose_desktop_profile('invented')


class ConfigurationTests(TestCase):
    def setUp(self):
        users=get_user_model()
        self.owner=users.objects.create_user('pw-owner')
        self.editor=users.objects.create_user('pw-editor')
        self.viewer=users.objects.create_user('pw-viewer')
        self.other=users.objects.create_user('pw-other')
        self.project=Project.objects.create(name='Playwright',website='https://www.example.test/',created_by=self.owner)
        for user,role in ((self.owner,'admin'),(self.editor,'editor'),(self.viewer,'viewer')):
            Membership.objects.create(project=self.project,user=user,role=role)
        self.url=reverse('ui:playwright-settings',args=[self.project.pk])
        self.client.force_login(self.owner)

    def data(self,**changes):
        return {'include_subdomains':'on','discover_urls':'on','use_sitemaps':'on',
            'start_urls':'https://blog.example.test/article', 'sitemap_urls':'https://www.example.test/sitemap.xml',
            'excluded_hosts':'staging.example.test','excluded_paths':'/beheer\n/zoeken',
            'desktop_profile':'random','pages_per_second':'1.00','parallel_agents':'1',
            'slow_after_seconds':'10','slow_pages_per_second':'0.25',**changes}

    def test_defaults_match_design_and_get_does_not_create_configuration_or_change_scope(self):
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        form=response.context['form']
        self.assertTrue(form['include_subdomains'].value())
        self.assertTrue(form['use_sitemaps'].value())
        self.assertTrue(form['discover_urls'].value())
        self.assertFalse(form['respect_robots'].value())
        self.assertEqual(form['parallel_agents'].value(),1)
        self.assertEqual(form['pages_per_second'].value(),Decimal('1'))
        self.assertFalse(PlaywrightCrawlSettings.objects.exists())
        self.project.refresh_from_db()
        self.assertFalse(self.project.include_subdomains)
        self.assertContains(response,'Uitvoering gereed')
        self.assertContains(response,'Geen screenshots.')

    @patch('requests.sessions.Session.request')
    @patch('celery.app.task.Task.apply_async')
    def test_save_persists_only_configuration_and_explicitly_updates_shared_subdomain_scope(self, enqueue, request):
        with self.captureOnCommitCallbacks(execute=True):
            response=self.client.post(self.url,self.data())
        self.assertEqual(response.status_code,302)
        config=PlaywrightCrawlSettings.objects.get(project=self.project)
        self.assertEqual(config.start_urls,['https://blog.example.test/article'])
        self.assertEqual(config.sitemap_urls,['https://www.example.test/sitemap.xml'])
        self.assertFalse(config.respect_robots)
        self.assertEqual(config.updated_by,self.owner)
        self.project.refresh_from_db()
        self.assertTrue(self.project.include_subdomains)
        self.assertFalse(ImportBatch.objects.exists())
        self.assertFalse(SourceRecord.objects.exists())
        enqueue.assert_not_called()
        request.assert_not_called()
        event=AuditEvent.objects.get(project=self.project,details__area='playwright_configuration')
        self.assertTrue(event.details['include_subdomains_after'])
        self.assertNotIn('screenshots',{field.name for field in config._meta.fields})

    def test_saved_configuration_is_reused_and_project_scope_remains_authoritative(self):
        self.client.post(self.url,self.data())
        first=PlaywrightCrawlSettings.objects.get()
        self.client.post(self.url,self.data(parallel_agents='20',respect_robots='on'))
        self.assertEqual(PlaywrightCrawlSettings.objects.count(),1)
        first.refresh_from_db()
        self.assertEqual(first.parallel_agents,20)
        self.assertTrue(first.respect_robots)
        Project.objects.filter(pk=self.project.pk).update(include_subdomains=False)
        response=self.client.get(self.url)
        self.assertFalse(response.context['form']['include_subdomains'].value())

    def test_out_of_scope_seeds_and_sitemaps_and_exclusions_are_rejected_atomically(self):
        for changes in ({'start_urls':'https://other.test/'}, {'sitemap_urls':'https://example.test.evil.test/sitemap.xml'},
                        {'start_urls':'https://user:secret@example.test/'}, {'excluded_hosts':'other.test'},
                        {'excluded_paths':'https://other.test/'}, {'start_urls':'https://staging.example.test/'},
                        {'start_urls':'https://example.test/beheer/private'}, {'excluded_hosts':'example.test'}):
            with self.subTest(changes=changes):
                response=self.client.post(self.url,self.data(**changes))
                self.assertEqual(response.status_code,200)
                self.assertTrue(response.context['form'].errors)
                self.assertFalse(PlaywrightCrawlSettings.objects.exists())
                self.project.refresh_from_db()
                self.assertFalse(self.project.include_subdomains)

    def test_rate_limits_and_agent_bounds_are_validated(self):
        for changes in ({'parallel_agents':'21'}, {'parallel_agents':'0'}, {'pages_per_second':'0'},
                        {'pages_per_second':'21'}, {'slow_pages_per_second':'2'}, {'slow_after_seconds':'0'},
                        {'desktop_profile':'invented'}):
            with self.subTest(changes=changes):
                form=PlaywrightCrawlForm(self.data(**changes),project=self.project)
                self.assertFalse(form.is_valid())

    def test_database_enforces_agent_cap_even_when_model_validation_is_bypassed(self):
        with self.assertRaises(IntegrityError),transaction.atomic():
            PlaywrightCrawlSettings.objects.create(project=self.project,parallel_agents=21)

    def test_viewers_and_editors_can_read_but_cannot_save_and_other_users_cannot_read(self):
        for user in (self.viewer,self.editor):
            self.client.force_login(user)
            response=self.client.get(self.url)
            self.assertEqual(response.status_code,200)
            self.assertNotContains(response,'Instellingen opslaan')
            self.assertTrue(all(field.disabled for field in response.context['form'].fields.values()))
            self.assertEqual(self.client.post(self.url,self.data()).status_code,403)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code,404)
        self.assertEqual(self.client.post(self.url,self.data()).status_code,404)
        self.assertFalse(PlaywrightCrawlSettings.objects.exists())

    def test_connection_card_links_to_configuration_without_starting_a_crawl(self):
        response=self.client.get(reverse('ui:connections',args=[self.project.pk]))
        self.assertContains(response,'Playwright')
        self.assertContains(response,self.url)
        self.assertContains(response,'Crawlinstellingen')
        self.assertFalse(ImportBatch.objects.exists())

    def test_direct_save_rechecks_access(self):
        Membership.objects.filter(project=self.project,user=self.owner).update(role='viewer')
        with self.assertRaises(PermissionDenied):
            save_configuration(actor=self.owner,project_id=self.project.pk,data=self.data())
        self.assertFalse(PlaywrightCrawlSettings.objects.exists())

    def test_configuration_is_removed_when_project_is_deleted(self):
        from projects.services import delete_project
        self.client.post(self.url,self.data())
        self.owner.is_superuser=True
        self.owner.save()
        self.owner.refresh_from_db()
        delete_project(actor=self.owner,project_id=self.project.pk,confirmation_name=self.project.name)
        self.assertFalse(PlaywrightCrawlSettings.objects.exists())
