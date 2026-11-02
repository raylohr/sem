from importlib import import_module
from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from projects.models import Project
from .models import ImportBatch, SourceRecord


class SEMScanMigrationTests(TestCase):
    def test_migration_preserves_individual_batches_records_and_provenance(self):
        user=get_user_model().objects.create_user('semscan-migration')
        project=Project.objects.create(name='Migration',created_by=user)
        batches=[ImportBatch.objects.create(project=project,created_by=user,source=source,method='scan',label=source,request_metadata={'respect_robots':True}) for source in ['crawler','sitemap']]
        for batch in batches:
            SourceRecord.objects.create(batch=batch,locator='row:1',data={'url':'https://example.com/'})
        migration=import_module('sources.migrations.0007_semscan')
        migration.forwards(apps,None)
        migration.forwards(apps,None)
        for batch in batches:
            original=batch.source
            stamp=batch.imported_at
            batch.refresh_from_db()
            self.assertEqual(batch.source,'semscan')
            self.assertEqual(batch.request_metadata['source_original'],original)
            self.assertEqual(batch.request_metadata['respect_robots'],True)
            self.assertEqual(batch.created_by_id,user.pk)
            self.assertEqual(batch.imported_at,stamp)
            self.assertEqual(batch.records.count(),1)
        self.assertEqual({b.scan_kind for b in ImportBatch.objects.all()},{'crawl','sitemap'})
