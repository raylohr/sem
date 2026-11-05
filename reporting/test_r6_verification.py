from datetime import timedelta
from types import SimpleNamespace
from django.test import TestCase
from django.utils import timezone
from . import tests as fixtures
from .models import Dossier, DossierPage
from .actions import removed_direct_script
from sources.models import SourceRecord

class RemovedScriptTests(TestCase):
    def setUp(self):
        fixture=fixtures.ReportTests();fixture.setUp()
        self.user=fixture.owner;self.batch=fixture.crawl;self.project=fixture.project
        self.dossier=Dossier.objects.create(project=self.project,crawl=self.batch,actor=self.user,title='Fresh source',status='complete')
        self.before=timezone.now()-timedelta(days=1)
        self.old=SimpleNamespace(snapshot={'resource':{'destination':'https://example.com/_sdk/data.js','direct_script_reference':True}},measured_at=self.before,finding_id=None)
        self.source=SourceRecord.objects.create(batch=self.batch,locator='new-source',data={'resource_discovery':{'version':'resources-v1','complete':True},'resource_candidates':[]})
        self.page=DossierPage.objects.create(dossier=self.dossier,source_record=self.source,url='https://example.com/calculator',url_hash='calc',scope='crawl',measured_at=timezone.now())

    def test_new_complete_inventory_proves_removed_direct_reference(self):
        self.assertTrue(removed_direct_script(self.old,self.page,self.dossier))

    def test_missing_inventory_incomplete_retained_old_or_indirect_are_not_proof(self):
        for data in ({}, {'resource_discovery':{'version':'resources-v1','complete':False},'resource_candidates':[]}, {'resource_discovery':{'version':'resources-v1','complete':True},'resource_candidates':[{'url':'https://example.com/_sdk/data.js'}]}):
            self.source.data=data;self.source.save()
            self.page.source_record=self.source
            self.assertFalse(removed_direct_script(self.old,self.page,self.dossier))
        self.source.data={'resource_discovery':{'version':'resources-v1','complete':True},'resource_candidates':[]};self.source.save()
        self.page.measured_at=self.before
        self.assertFalse(removed_direct_script(self.old,self.page,self.dossier))
        self.page.measured_at=timezone.now();self.old.snapshot['resource']['direct_script_reference']=False
        self.assertFalse(removed_direct_script(self.old,self.page,self.dossier))
