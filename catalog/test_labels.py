from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from projects.models import Project, Membership
from sources.models import ImportBatch
from sources.services import publish_records, delete_batch
from catalog.models import Label, URLLabel, LabelEvidence
from catalog.labels import import_labels


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}, 'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}})
class AuditLabelTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(username='labels-editor',password='test')
        self.project = Project.objects.create(name='GMW',website='https://example.com/',created_by=self.actor)
        Membership.objects.create(project=self.project,user=self.actor,role='admin')
        self.batch = ImportBatch.objects.create(project=self.project,source='audit',method='upload',label='Audit',created_by=self.actor)
        rows = [
            ('03 - LIVE CONTROLE', {'URL':'https://example.com/a/','Expertise':'Arbeidsrecht / pensioen'}),
            ('02 - URL ACTIEPLAN', {'URL':'https://example.com/a/','Expertise / cluster':'Arbeidsrecht / pensioen'}),
            ('01 - EXPERTISE OVERZICHT', {'URL expertisepagina':'https://example.com/a/','Expertise volgens overzichtspagina':'Arbeid'}),
            ('01 - EXPERTISE OVERZICHT', {'URL expertisepagina':'Nog te verifiëren','Expertise volgens overzichtspagina':'Bouw'}),
            ('Inhoudelijke audit - blogs', {'URL':'Niet vastgesteld — zie https://example.com/','Blog / cluster':'Arbeidsrecht'}),
            ('Inhoudelijke audit - blogs', {'URL':'https://example.com/b/','Blog / cluster':'An arbitrary article title'}),
            ('03 - LIVE CONTROLE', {'URL':'https://example.com/contact/','Expertise':'Contact / conversie'}),
            ('BEVINDINGEN PER ONDERWERP URL', {'URL':'https://example.com/a/','Onderwerp / expertise':'Arbeidsrecht / pensioen\n\nInhoudelijke audit - paginas'}),
        ]
        publish_records(batch_id=self.batch.pk, records=[{'locator':f'sheet:1:row:{i}', 'data':{'sheet':sheet,'row':i,'values':values}} for i,(sheet,values) in enumerate(rows,2)])

    def test_provenance_idempotence_and_dry_run(self):
        before=import_labels(batch_id=self.batch.pk,actor=self.actor,dry_run=True)
        self.assertFalse(Label.objects.exists())
        self.assertEqual(before['counts']['unique_urls'],2)
        result=import_labels(batch_id=self.batch.pk,actor=self.actor)
        self.assertEqual(result['counts']['unique_assignments'],3)
        self.assertEqual(URLLabel.objects.count(),3)
        self.assertEqual(LabelEvidence.objects.count(),5)
        self.assertEqual(Label.objects.filter(kind='expertise').count(),15)
        self.assertFalse(Label.objects.filter(name__contains='Inhoudelijke audit').exists())
        self.assertFalse(Label.objects.filter(name__contains='arbitrary').exists())
        self.assertTrue(Label.objects.filter(name='Arbeidsrecht / pensioen',kind='source').exists())
        again=import_labels(batch_id=self.batch.pk,actor=self.actor)
        self.assertEqual(again['counts']['assignments_created'],0)
        self.assertEqual(again['counts']['evidence_created'],0)
        self.assertTrue(all(not a.role and a.status=='source' for a in URLLabel.objects.all()))

    def test_permissions_and_project_isolation(self):
        outsider=get_user_model().objects.create_user(username='outsider')
        with self.assertRaises(PermissionDenied):import_labels(batch_id=self.batch.pk,actor=outsider)
        import_labels(batch_id=self.batch.pk,actor=self.actor)
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(reverse('ui:catalog:labels',args=[self.project.pk])).status_code,404)
        self.client.force_login(self.actor)
        response=self.client.get(reverse('ui:catalog:labels',args=[self.project.pk]))
        self.assertContains(response,'Labelregister')
        label=Label.objects.get(name='Arbeid')
        response=self.client.get(reverse('ui:catalog:labels',args=[self.project.pk]),{'label':label.pk})
        self.assertContains(response,'Bronverwijzingen')
        self.assertContains(response,'Expertise volgens overzichtspagina')

    def test_deleting_source_removes_only_unsupported_assignments(self):
        import_labels(batch_id=self.batch.pk,actor=self.actor)
        kept=URLLabel.objects.get(label__name='Arbeid')
        kept.status='reviewed';kept.save()
        delete_batch(batch_id=self.batch.pk,project=self.project,actor=self.actor)
        self.assertEqual(URLLabel.objects.count(),1)
        self.assertTrue(URLLabel.objects.filter(pk=kept.pk).exists())
        self.assertFalse(LabelEvidence.objects.exists())

    def test_deleting_source_preserves_unrelated_path_assignments(self):
        import_labels(batch_id=self.batch.pk,actor=self.actor)
        url=URLLabel.objects.get(label__name='Arbeid').url
        rule=URLLabel.objects.create(url=url,label=Label.objects.get(project=self.project,name='Huur'))
        delete_batch(batch_id=self.batch.pk,project=self.project,actor=self.actor)
        self.assertEqual(list(URLLabel.objects.values_list('pk',flat=True)),[rule.pk])
