from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from projects.models import Project, Membership
from sources.models import ImportBatch
from .models import Dossier, IssueGroup, IssueOccurrence
from .proposals import build, approve

@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'},'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'}})
class ProposalTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('proposal-owner')
        self.project=Project.objects.create(name='Proposal',website='https://example.com/',created_by=self.user)
        Membership.objects.create(project=self.project,user=self.user,role='admin')
        batch=ImportBatch.objects.create(project=self.project,created_by=self.user,source='semscan',method='scan',status='complete')
        self.dossier=Dossier.objects.create(project=self.project,crawl=batch,actor=self.user,title='Fixture',status='complete')
        for i in range(4):
            group=IssueGroup.objects.create(dossier=self.dossier,fingerprint=str(i),rule='resource_http_error',category='technical',title='Script 404',severity='high',recommendation='Repair',basis='measured')
            IssueOccurrence.objects.create(group=group,url='https://example.com/berekenen/',url_hash='same',scope='crawl',snapshot={'resource':{'http_status':404,'destination':f'https://example.com/_sdk/{i}.js'}})
        build(self.dossier)
        self.proposal=self.dossier.proposals.get()
        self.client.force_login(self.user)

    def test_bundle_approval_is_idempotent_and_keeps_evidence(self):
        self.assertEqual(self.proposal.groups.count(),4)
        values={'title':'Fix calculator','acceptance':'Check calculator operation','owner':self.user}
        action=approve(self.proposal,self.user,values)
        self.assertEqual(approve(self.proposal,self.user,values).pk,action.pk)
        self.assertEqual(action.groups.count(),4)
        self.assertEqual(self.proposal.reviews.count(),1)
        self.assertEqual(IssueOccurrence.objects.count(),4)

    def test_supplement_cannot_change_status_and_is_unverified(self):
        url=reverse('ui:reports:proposal-supplement',args=[self.project.pk,self.proposal.pk])
        data={'model':'test-model','prompt_reference':'prompt-v1','text':'Check the integration.'}
        self.assertEqual(self.client.post(url,{**data,'status':'resolved'},content_type='application/json').status_code,400)
        self.assertEqual(self.client.post(url,data,content_type='application/json').status_code,201)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status,'proposed')
        self.assertEqual(self.proposal.supplements.get().provenance['status'],'unverified_user_import')
        self.assertEqual(self.client.get(reverse('ui:reports:proposal',args=[self.project.pk,self.proposal.pk])).status_code,200)

    def test_unrelated_calculator_sources_are_not_bundled(self):
        self.dossier.proposals.all().delete()
        for i,occurrence in enumerate(IssueOccurrence.objects.all()):
            occurrence.url=f'https://example.com/berekenen/{i}/';occurrence.save()
        build(self.dossier)
        self.assertEqual(self.dossier.proposals.count(),4)

    def test_reader_cannot_approve_or_import_and_other_project_is_hidden(self):
        reader=get_user_model().objects.create_user('proposal-reader')
        Membership.objects.create(project=self.project,user=reader,role='viewer')
        self.client.force_login(reader)
        for route in ('proposal-approve','proposal-supplement','proposal-dismiss'):
            self.assertEqual(self.client.post(reverse('ui:reports:'+route,args=[self.project.pk,self.proposal.pk]),{}).status_code,403)
        other=Project.objects.create(name='Other',created_by=self.user)
        Membership.objects.create(project=other,user=self.user,role='admin')
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('ui:reports:proposal',args=[other.pk,self.proposal.pk])).status_code,404)

    def test_dismissal_preserves_measurements_and_records_reason(self):
        url=reverse('ui:reports:proposal-dismiss',args=[self.project.pk,self.proposal.pk])
        self.assertEqual(self.client.post(url,{'reason':'Bewust aanvaard na technische beoordeling.'}).status_code,302)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status,'dismissed')
        self.assertEqual(self.proposal.reviews.get().decision,'dismissed')
        self.assertEqual(IssueOccurrence.objects.count(),4)
        self.assertEqual(self.proposal.dossier.groups.count(),4)

    def test_locations_and_target_are_frozen_and_input_manifest_is_hashed(self):
        from .services import evidence_locations,evidence_summary
        from .models import DossierInput
        details={'elements':[{'element_path':'/html/body/script[1]','locator':'line:12','raw':'SECRET'}]}
        frozen=evidence_locations(details)
        self.assertEqual(frozen,[{'locator':'line:12','element_path':'/html/body/script[1]'}])
        self.assertEqual(evidence_locations({'observations':[details]}),frozen)
        occurrence=IssueOccurrence.objects.first()
        occurrence.snapshot['locations']=frozen;occurrence.save()
        self.dossier.proposals.all().delete();build(self.dossier)
        original=self.dossier.proposals.get()
        self.assertEqual(original.evidence['examples'][0]['locations'],frozen)
        self.assertIn('/_sdk/',original.evidence['examples'][0]['destination'])
        old_hash=original.provenance['input_sha256']
        DossierInput.objects.create(dossier=self.dossier,kind='crawl',snapshot={'rule_version':'new'})
        self.dossier.proposals.all().delete();build(self.dossier)
        self.assertNotEqual(self.dossier.proposals.get().provenance['input_sha256'],old_hash)
        self.assertTrue(evidence_summary({'elements':[{'reason':'Onbekend veld','path':'$.typo'}]}))
