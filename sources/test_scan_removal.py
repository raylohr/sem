from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from projects.models import Project, Membership
from catalog.models import ProjectURL, URLObservation
from sources.models import ImportBatch
from sources.services import publish_records


class ScanRemovalTests(TestCase):
    def test_removal_preserves_imports_and_shared_urls(self):
        user = get_user_model().objects.create_user('cleanup-owner')
        project = Project.objects.create(name='Cleanup', website='https://example.com/', created_by=user)
        Membership.objects.create(project=project, user=user, role='admin')
        imported = ImportBatch.objects.create(project=project, created_by=user, source='ahrefs', method='upload', label='Keep')
        scan = ImportBatch.objects.create(project=project, created_by=user, source='semscan', method='scan', label='Remove')
        publish_records(batch_id=imported.pk, records=[{'locator':'1', 'data':{'URL':'https://example.com/shared'}}])
        publish_records(batch_id=scan.pk, records=[{'locator':str(i), 'data':{'url':url}} for i,url in enumerate(['https://example.com/shared', 'https://example.com/scan-only'])])
        import_module('sources.migrations.0010_delete_retired_scan_data').purge(apps, SimpleNamespace(connection=connection))
        self.assertTrue(ImportBatch.objects.filter(pk=imported.pk).exists())
        self.assertFalse(ImportBatch.objects.filter(pk=scan.pk).exists())
        self.assertEqual(URLObservation.objects.count(), 1)
        self.assertEqual(list(ProjectURL.objects.values_list('normalized_url', flat=True)), ['https://example.com/shared'])
