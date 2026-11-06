from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class LabelRemovalMigrationTests(TransactionTestCase):
    def test_removes_populated_labels_and_preserves_source_data(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        before = [(app, '0008_gmw_english_expertise_paths') if app == 'catalog' else (app, name)
                  for app, name in latest]
        try:
            executor.migrate(before)
            historical = executor.loader.project_state(before).apps
            user = historical.get_model('accounts', 'User').objects.create(username='migration-owner')
            project = historical.get_model('projects', 'Project').objects.create(
                name='Preserve', website='https://example.com/', created_by_id=user.pk)
            batch = historical.get_model('sources', 'ImportBatch').objects.create(
                project_id=project.pk, created_by_id=user.pk, source='ahrefs', method='upload', label='Keep')
            record = historical.get_model('sources', 'SourceRecord').objects.create(
                batch_id=batch.pk, locator='1', data={'URL':'https://example.com/'})
            url = historical.get_model('catalog', 'ProjectURL').objects.create(
                project_id=project.pk, normalized_url='https://example.com/', url_hash='test', host='example.com')
            label = historical.get_model('catalog', 'Label').objects.create(project_id=project.pk, name='Remove', key='remove')
            assignment = historical.get_model('catalog', 'URLLabel').objects.create(url_id=url.pk, label_id=label.pk)
            historical.get_model('catalog', 'LabelEvidence').objects.create(
                assignment_id=assignment.pk, source_record_id=record.pk, imported_by_id=user.pk,
                column='Topic', original_label='Remove', original_url='https://example.com/')
            MigrationExecutor(connection).migrate(latest)
            tables = connection.introspection.table_names()
            for name in ('catalog_label', 'catalog_urllabel', 'catalog_labelevidence'):
                self.assertNotIn(name, tables)
            from catalog.models import ProjectURL
            from sources.models import ImportBatch, SourceRecord
            self.assertTrue(ProjectURL.objects.filter(pk=url.pk).exists())
            self.assertTrue(ImportBatch.objects.filter(pk=batch.pk).exists())
            self.assertTrue(SourceRecord.objects.filter(pk=record.pk).exists())
        finally:
            MigrationExecutor(connection).migrate(latest)
