from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from projects.models import Project, Membership
from .models import ImportBatch


class FileImportRemovalTests(TestCase):
    def test_file_endpoints_are_gone_and_gsc_results_remain_available(self):
        user = get_user_model().objects.create_user('gsc-only')
        project = Project.objects.create(name='GSC', website='https://example.com/', created_by=user)
        Membership.objects.create(project=project, user=user, role='admin')
        batch = ImportBatch.objects.create(project=project, created_by=user, source='gsc', method='api', label='Keep')
        self.client.force_login(user)
        base = f'/projects/{project.pk}/sources/'
        for path in ('upload/', 'create/', f'{batch.pk}/download/'):
            self.assertEqual(self.client.post(base+path).status_code, 404)
        response = self.client.get(reverse('ui:sources', args=[project.pk]))
        self.assertContains(response, 'Keep')
        self.assertNotContains(response, 'name="file"')
        self.assertNotContains(response, 'Rapport uploaden')

    def test_gsc_model_has_no_file_import_fields(self):
        fields = {field.name for field in ImportBatch._meta.fields}
        self.assertFalse(fields & {'file', 'checksum', 'fingerprint', 'original_filename', 'sheet_summary'})
        self.assertEqual(ImportBatch.Source.values, ['gsc', 'sf', 'ahrefs'])
        self.assertEqual(ImportBatch.Method.values, ['api', 'file'])
