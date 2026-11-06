from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class CatalogRemovalTests(TransactionTestCase):
    def test_removal_preserves_imports_and_raw_records(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        before = [(app, '0009_remove_urllabel_label_and_more') if app == 'catalog' else (app, name)
                  for app, name in latest]
        try:
            executor.migrate(before)
            historical = executor.loader.project_state(before).apps
            user = historical.get_model('accounts', 'User').objects.create(username='catalog-removal')
            project = historical.get_model('projects', 'Project').objects.create(
                name='Keep', website='https://example.com/', created_by_id=user.pk)
            batch = historical.get_model('sources', 'ImportBatch').objects.create(
                project_id=project.pk, created_by_id=user.pk, source='ahrefs', method='upload', label='Keep')
            record = historical.get_model('sources', 'SourceRecord').objects.create(
                batch_id=batch.pk, locator='1', data={'URL':'https://example.com/'})
            url = historical.get_model('catalog', 'ProjectURL').objects.create(
                project_id=project.pk, normalized_url='https://example.com/', host='example.com', url_hash='test')
            historical.get_model('catalog', 'URLObservation').objects.create(
                url_id=url.pk, batch_id=batch.pk, source_record_id=record.pk, original_url='https://example.com/')
            MigrationExecutor(connection).migrate(latest)
            self.assertFalse(any(t.startswith('catalog_') for t in connection.introspection.table_names()))
            from sources.models import ImportBatch, SourceRecord
            self.assertTrue(ImportBatch.objects.filter(pk=batch.pk).exists())
            self.assertEqual(SourceRecord.objects.get(pk=record.pk).data, {'URL':'https://example.com/'})
        finally:
            MigrationExecutor(connection).migrate(latest)
