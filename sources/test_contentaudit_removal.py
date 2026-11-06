from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from projects.models import Project
from .forms import BatchForm
from .models import ImportBatch, SourceRecord, PendingFileDeletion


class ContentauditRemovalTests(TestCase):
    def test_deletes_audit_records_and_queues_file_preserving_other_imports(self):
        user = get_user_model().objects.create_user('audit-removal')
        project = Project.objects.create(name='Keep', website='https://example.com/', created_by=user)
        audit = ImportBatch.objects.create(project=project, created_by=user, source='audit', method='upload',
                                          label='Remove', file='imports/old-audit.xlsx')
        keep = ImportBatch.objects.create(project=project, created_by=user, source='ahrefs', method='upload', label='Keep')
        for batch in (audit, keep):
            SourceRecord.objects.create(batch=batch, locator='1', data={'original':'data'})
        import_module('sources.migrations.0012_alter_importbatch_source').remove_contentaudits(
            apps, SimpleNamespace(connection=connection))
        self.assertFalse(ImportBatch.objects.filter(pk=audit.pk).exists())
        self.assertFalse(SourceRecord.objects.filter(batch_id=audit.pk).exists())
        self.assertTrue(SourceRecord.objects.filter(batch=keep).exists())
        self.assertTrue(PendingFileDeletion.objects.filter(path='imports/old-audit.xlsx').exists())

    def test_audit_is_no_longer_an_allowed_source(self):
        form = BatchForm(data={'source':'audit', 'method':'upload', 'label':'Removed'})
        self.assertFalse(form.is_valid())
        self.assertIn('source', form.errors)
