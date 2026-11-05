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
