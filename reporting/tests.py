from unittest.mock import patch
from datetime import datetime,timezone as dt_timezone
import json
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase,SimpleTestCase,override_settings,Client
from django.urls import reverse
from projects.models import Project,Membership
from sources.models import ImportBatch,SourceRecord
from assessments.models import AnalysisDefinition,AnalysisRun,AnalysisResult,Finding
from browser_scans.models import BrowserRun,BrowserVisit
from .models import Dossier,IssueGroup,DossierPage,ActionItem
from .catalog import EXPECTED,score
from .services import create_dossier,assemble
from .actions import create_action,update_action,verify_action
from .tasks import build_dossier


class ScoreTests(SimpleTestCase):
    def test_unknown_never_earns_points_and_low_coverage_hides_score(self):
        state,value,coverage,calc=score([{'http_error':'pass'}],['http_error','canonical_target'])
        self.assertEqual(state,'insufficient');self.assertIsNone(value)
        self.assertEqual(coverage['percent'],50)
        state,value,coverage,calc=score([dict.fromkeys(['a','b','c','d'],'pass')],['a','b','c','d','e'])
        self.assertEqual(state,'measured');self.assertEqual(value,80)
        self.assertEqual(calc['earned_weight'],4)

    def test_not_applicable_is_separate_and_not_a_perfect_score(self):
        state,value,coverage,_=score([{'a':'not_applicable'}],['a'])
        self.assertEqual(state,'not_applicable');self.assertIsNone(value)


    def test_unknown_timezone_is_not_invented_for_verification(self):
        from .services import date
        self.assertIsNone(date('2026-11-03T10:00:00'))
        self.assertIsNotNone(date('2026-11-03T10:00:00+00:00'))


    def test_group_signatures_preserve_multiple_targets_and_primary_metadata(self):
        from .services import signature
        first=signature('link_to_5xx',{'observations':[{'destination':'https://example.com/a'},{'destination':'https://example.com/b'}]})
        reverse=signature('link_to_5xx',{'observations':[{'destination':'https://example.com/b'},{'destination':'https://example.com/a'}]})
        single=signature('link_to_5xx',{'observations':[{'destination':'https://example.com/a'}]})
        self.assertEqual(first,reverse);self.assertNotEqual(first,single)
        self.assertEqual(signature('duplicate_titles',{'value':[' Primary ','Second']}),signature('duplicate_titles',{'value':['primary','Other']}))


    def test_engine_rule_names_and_additional_accessibility_signals_are_not_lost(self):
        from .catalog import normalized_checks
        from .services import browser_checks
        from .actions import check_rule
        self.assertEqual(normalized_checks({'links_to_404':'issue'}),{'link_to_404':'issue'})
        checks=browser_checks({'_complete':True,'coverage':{'resource_count':2}},{'accessibility_aria-required-attr'})
        self.assertEqual(checks['a11y_structure'],'issue')
        self.assertEqual(check_rule('accessibility_aria-required-attr'),'a11y_structure')


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'},'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'}})
class ReportTests(TestCase):
    def setUp(self):
        user=get_user_model()
        self.owner=user.objects.create_user('report-owner');self.reader=user.objects.create_user('report-reader');self.other=user.objects.create_user('report-other')
        self.project=Project.objects.create(name='Report fixture',website='https://example.com/',created_by=self.owner)
        Membership.objects.create(project=self.project,user=self.owner,role='admin');Membership.objects.create(project=self.project,user=self.reader,role='viewer')
        self.crawl=self.make_crawl('2026-11-03T10:00:00+00:00',issue=True)

    def make_crawl(self,measured,issue=False,version='3'):
        batch=ImportBatch.objects.create(project=self.project,created_by=self.owner,source='semscan',method='scan',label='Fixture crawl',status='complete',
            request_metadata={'scan':'crawl','url':'https://example.com/'})
        definition,_=AnalysisDefinition.objects.get_or_create(key='crawl-basics',version=version)
        run=AnalysisRun.objects.create(batch=batch,definition=definition,status='complete',extractor_version='assessment-v3')
        for index,path in enumerate(('/','/page/')):
            source=SourceRecord.objects.create(batch=batch,locator='page:'+str(index),data={'url':'https://example.com'+path,'measured_at':measured})
            checks={rule:'pass' for category in ('technical','content') for rule in EXPECTED[category]}
            if issue:checks['link_to_404']='issue'
            checks['links_to_404']=checks.pop('link_to_404') # original analysis engine key
            result=AnalysisResult.objects.create(run=run,source_record=source,outcome='issue' if issue else 'pass',
                answer={'input':{'url':source.data['url']},'measured_at':measured,'checks':checks})
            if issue:
                # Many occurrences are one technical signal on each of two URLs.
                for number in range(3):Finding.objects.create(result=result,rule='link_to_404',severity='high',title='Link leidt naar een 404',
                    recommendation='Herstel de gedeelde verwijzing.',details={'source_url':source.data['url'],'destination':'https://example.com/missing'})
        return batch

    def report(self,batch=None,browser=None):
        with patch('reporting.services.enqueue'):
            dossier=create_dossier(self.project,self.owner,(batch or self.crawl).pk,browser.pk if browser else None)
        return assemble(dossier.pk)

    def action(self,dossier=None):
        dossier=dossier or self.report();group=dossier.groups.get(rule='link_to_404')
        return create_action(self.project,self.owner,[group],{'title':'Herstel verwijzing','description':'Navigatie','acceptance':'De link leidt naar een bereikbare pagina.',
            'owner':self.owner,'priority':'high','effort':'small'})

    def browser(self):
        batch=ImportBatch.objects.create(project=self.project,created_by=self.owner,source='semscan',method='scan',label='Fixture browser',status='incomplete',request_metadata={'scan':'browser'})
        browser=BrowserRun.objects.create(batch=batch,source_batch=self.crawl,config={})
        basic,_=AnalysisDefinition.objects.get_or_create(key='crawl-basics',version='3')
        run=AnalysisRun.objects.create(batch=batch,definition=basic,status='complete')
        source=SourceRecord.objects.create(batch=batch,locator='browser:0',data={'url':'https://example.com/','privacy_scenario':'baseline','measured_at':'2026-11-03T10:01:00+00:00'})
        AnalysisResult.objects.create(run=run,source_record=source,outcome='unknown',answer={'input':{'url':source.data['url']},'checks':{'image_alt_missing':'pass','mixed_content':'pass'}})
        BrowserVisit.objects.create(run=browser,url=source.data['url'],url_hash='a'*64,source_record=source,status='incomplete')
        definition,_=AnalysisDefinition.objects.get_or_create(key='browser-basics',version='1')
        extra=AnalysisRun.objects.create(batch=batch,definition=definition,status='complete')
        result=AnalysisResult.objects.create(run=extra,source_record=source,outcome='unknown',answer={'coverage':{'a11y_incomplete':[],'resource_count':2}})
        Finding.objects.create(result=result,rule='accessibility_color-contrast',severity='high',title='Contrast',recommendation='Pas kleur aan.',details={'url':source.data['url'],'nodes':[{'target':['.button']} ]})
        return batch

    def test_grouping_counts_urls_and_occurrences_separately(self):
        dossier=self.report();group=dossier.groups.get()
        self.assertEqual(group.occurrences,6);self.assertEqual(group.affected_urls,2)
        self.assertEqual(dossier.pages.count(),2)
        self.assertEqual(dossier.categories.get(key='content').score,100)
        self.assertEqual(dossier.categories.get(key='experience').state,'insufficient')
        self.assertIsNone(dossier.categories.get(key='privacy').score)
        self.assertEqual(group.basis,'Zelfde gemeten bestemming')

    def test_repeated_occurrences_do_not_multiply_score_penalty(self):
        first=self.report();before=first.categories.get(key='technical').score
        source=self.crawl.records.first();result=source.analysisresult_set.first()
        Finding.objects.create(result=result,rule='link_to_404',severity='high',title='Again',recommendation='Fix',details={'source_url':source.data['url'],'destination':'https://example.com/missing'})
        after=self.report().categories.get(key='technical').score
        self.assertEqual(before,after)

    def test_frozen_inputs_do_not_switch_to_a_later_analysis(self):
        with patch('reporting.services.enqueue'):dossier=create_dossier(self.project,self.owner,self.crawl.pk)
        old=dossier.inputs.get().run_id
        definition=AnalysisDefinition.objects.get(key='crawl-basics',version='3')
        AnalysisRun.objects.create(batch=self.crawl,definition=definition,status='complete')
        assembled=assemble(dossier.pk)
        self.assertEqual(assembled.inputs.get().run_id,old)
        self.assertEqual(assembled.groups.count(),1)

    def test_completed_report_is_idempotent_and_historical(self):
        dossier=self.report();snapshot=dossier.categories.get(key='technical').calculation
        assemble(dossier.pk)
        self.assertEqual(dossier.groups.count(),1);self.assertEqual(dossier.pages.count(),2)
        self.assertEqual(dossier.categories.get(key='technical').calculation,snapshot)

    def test_browser_sample_and_unknown_privacy_are_separate(self):
        dossier=self.report(browser=self.browser())
        self.assertEqual(dossier.coverage['crawl_urls'],2);self.assertEqual(dossier.coverage['browser_urls'],1)
        self.assertEqual(dossier.categories.get(key='experience').state,'insufficient')
        self.assertEqual(dossier.categories.get(key='privacy').coverage['known'],0)
        self.assertEqual(dossier.categories.get(key='privacy').coverage['counts']['unknown'],4)
        self.assertEqual(dossier.categories.get(key='experience').coverage['url_count'],1)

    def test_other_crawl_browser_pair_is_rejected(self):
        other=self.make_crawl('2026-11-03T11:00:00+00:00')
        with self.assertRaises(ValidationError):create_dossier(self.project,self.owner,other.pk,self.browser().pk)

    def test_revoked_actor_fails_without_partial_report(self):
        with patch('reporting.services.enqueue'):dossier=create_dossier(self.project,self.owner,self.crawl.pk)
        self.project.memberships.filter(user=self.owner).delete();build_dossier(str(dossier.pk));dossier.refresh_from_db()
        self.assertEqual(dossier.status,'failed');self.assertEqual(dossier.pages.count(),0)

    def test_same_old_measurement_does_not_resolve_action(self):
        action=self.action();old=self.report()
        verify_action(action.pk,old.pk,self.owner,True);action.refresh_from_db()
        self.assertEqual(action.status,'verify')
        self.assertEqual(action.events.latest('created_at').snapshot['counts']['not_remeasured'],2)

    def test_newer_passing_measurement_and_acceptance_are_both_required(self):
        action=self.action();new=self.report(self.make_crawl('2026-11-03T11:00:00+00:00'))
        verify_action(action.pk,new.pk,self.owner,False);action.refresh_from_db();self.assertEqual(action.status,'verify')
        verify_action(action.pk,new.pk,self.owner,True);action.refresh_from_db();self.assertEqual(action.status,'resolved')
        self.assertTrue(action.events.latest('created_at').snapshot['resolved'])

    def test_newer_failed_or_missing_measurement_does_not_resolve(self):
        action=self.action();new=self.report(self.make_crawl('2026-11-03T11:00:00+00:00',issue=True))
        verify_action(action.pk,new.pk,self.owner,True);action.refresh_from_db();self.assertEqual(action.status,'verify')
        self.assertEqual(action.events.latest('created_at').snapshot['counts']['remaining'],2)
        DossierPage.objects.filter(dossier=new).delete()
        verify_action(action.pk,new.pk,self.owner,True);action.refresh_from_db();self.assertNotEqual(action.status,'resolved')

    def test_changed_rule_version_requires_review_not_resolution(self):
        action=self.action();new=self.report(self.make_crawl('2026-11-03T11:00:00+00:00',version='4'))
        verify_action(action.pk,new.pk,self.owner,True)
        self.assertEqual(action.events.latest('created_at').snapshot['counts']['not_remeasured'],2)

    def test_manual_acceptance_requires_reason_and_keeps_scores(self):
        dossier=self.report();action=self.action(dossier);score_before=dossier.categories.get(key='technical').score
        values={'title':action.title,'acceptance':action.acceptance,'status':'accepted','rationale':'kort'}
        with self.assertRaises(ValidationError):update_action(action.pk,self.owner,values,action.updated_at.isoformat())
        values['rationale']='Bewust behouden vanwege een vastgelegde uitzondering.'
        update_action(action.pk,self.owner,values,action.updated_at.isoformat());action.refresh_from_db()
        self.assertEqual(action.status,'accepted');self.assertEqual(dossier.categories.get(key='technical').score,score_before)

    def test_conflicting_edits_and_manual_resolve_are_rejected(self):
        action=self.action();values={'title':action.title,'acceptance':action.acceptance,'status':'resolved'}
        with self.assertRaises(ValidationError):update_action(action.pk,self.owner,values,action.updated_at.isoformat())
        values['status']='planned'
        with self.assertRaises(ValidationError):update_action(action.pk,self.owner,values,'stale')

    def test_action_group_scope_and_owner_scope_are_enforced(self):
        dossier=self.report();group=dossier.groups.get()
        with self.assertRaises(PermissionDenied):create_action(self.project,self.reader,[group],{'title':'x','acceptance':'y'})
        with self.assertRaises(PermissionDenied):create_action(self.project,self.owner,[group],{'title':'x','acceptance':'y','owner':self.other})

    def test_single_group_creation_is_idempotent(self):
        dossier=self.report();first=self.action(dossier);again=self.action(dossier)
        self.assertEqual(first.pk,again.pk);self.assertEqual(ActionItem.objects.count(),1)

    def test_deleting_browser_marks_missing_input_and_retains_snapshot(self):
        browser=self.browser();dossier=self.report(browser=browser);browser.delete();dossier.refresh_from_db()
        self.assertIsNone(dossier.browser_id);self.assertTrue(dossier.inputs.filter(run=None).exists())
        self.assertTrue(dossier.categories.exists())
        self.client.force_login(self.owner)
        response=self.client.get(reverse('ui:reports:dossier',args=[self.project.pk,dossier.pk]))
        self.assertContains(response,'historische momentopname')

    def test_deleting_crawl_removes_dossier_but_keeps_action_history(self):
        dossier=self.report();action=self.action(dossier);self.crawl.delete()
        self.assertFalse(Dossier.objects.filter(pk=dossier.pk).exists());self.assertTrue(ActionItem.objects.filter(pk=action.pk).exists())
        self.assertEqual(action.groups.count(),0)

    def test_views_exports_print_and_project_boundaries(self):
        dossier=self.report();group=dossier.groups.get();action=self.action(dossier)
        names=[('overview',[]),('dossier',[dossier.pk]),('category',[dossier.pk,'technical']),('group',[group.pk]),('prospect',[dossier.pk]),('actions',[]),('action',[action.pk])]
        self.client.force_login(self.reader)
        for name,args in names:self.assertEqual(self.client.get(reverse('ui:reports:'+name,args=[self.project.pk,*args])).status_code,200,name)
        download=self.client.get(reverse('ui:reports:prospect',args=[self.project.pk,dossier.pk])+'?download=1')
        self.assertIn('attachment',download['Content-Disposition']);self.assertIn('sandbox',download['Content-Security-Policy'])
        self.assertNotContains(download,'<script')
        self.assertContains(download,'Voorbeeldpagina:');self.assertContains(download,'https://example.com/missing')
        export=self.client.get(reverse('ui:reports:export',args=[self.project.pk,dossier.pk]))
        rows=[json.loads(line) for line in b''.join(export.streaming_content).decode().splitlines()]
        self.assertTrue(any(row['type']=='evidence' for row in rows));self.assertTrue(any(row['type']=='category' and row['calculation']['version']=='report-v1' for row in rows))
        self.assertEqual(self.client.get(reverse('ui:reports:start',args=[self.project.pk])).status_code,403)
        self.client.force_login(self.other)
        for name,args in names:self.assertEqual(self.client.get(reverse('ui:reports:'+name,args=[self.project.pk,*args])).status_code,404,name)

    def test_mutating_forms_csrf_and_xss(self):
        dossier=self.report();group=dossier.groups.get();action=self.action(dossier)
        self.client.force_login(self.owner)
        for name,args in [('start',[]),('action-create',[]),('action-edit',[action.pk])]:
            self.assertEqual(self.client.get(reverse('ui:reports:'+name,args=[self.project.pk,*args])).status_code,200,name)
        client=Client(enforce_csrf_checks=True);client.force_login(self.owner)
        self.assertEqual(client.post(reverse('ui:reports:start',args=[self.project.pk]),{'crawl':self.crawl.pk}).status_code,403)
        group.title='<script>alert(1)</script>';group.save()
        response=self.client.get(reverse('ui:reports:prospect',args=[self.project.pk,dossier.pk])+'?download=1')
        self.assertContains(response,'&lt;script&gt;');self.assertNotContains(response,'<script>')

    def test_resolved_action_needs_reverification_if_acceptance_changes(self):
        action=self.action();new=self.report(self.make_crawl('2026-11-03T11:00:00+00:00'))
        verify_action(action.pk,new.pk,self.owner,True);action.refresh_from_db()
        values={'title':action.title,'acceptance':'Een nieuw, zwaarder acceptatiecriterium.','status':'resolved'}
        with self.assertRaises(ValidationError):update_action(action.pk,self.owner,values,action.updated_at.isoformat())

    def test_unknown_checks_and_unknown_measurement_dates_never_resolve(self):
        action=self.action();new=self.report(self.make_crawl('2026-11-03T11:00:00+00:00'))
        page=new.pages.first();page.checks['link_to_404']='unknown';page.save()
        verify_action(action.pk,new.pk,self.owner,True)
        self.assertEqual(action.events.latest('created_at').snapshot['counts']['not_remeasured'],1)
        page.checks['link_to_404']='pass';page.measured_at=None;page.save()
        verify_action(action.pk,new.pk,self.owner,True);action.refresh_from_db();self.assertNotEqual(action.status,'resolved')

    def test_posted_actions_and_verification_use_scoped_forms(self):
        dossier=self.report();group=dossier.groups.get();self.client.force_login(self.owner)
        response=self.client.post(reverse('ui:reports:action-create',args=[self.project.pk]),{'groups':[group.pk],'title':'Fix link','acceptance':'Link slaagt bij een nieuwe meting.','priority':'high','effort':'small','owner':self.owner.pk})
        self.assertEqual(response.status_code,302)
        action=ActionItem.objects.get()
        new=self.report(self.make_crawl('2026-11-03T11:00:00+00:00'))
        response=self.client.post(reverse('ui:reports:verify',args=[self.project.pk,action.pk]),{'dossier':new.pk,'acceptance_confirmed':'on'})
        self.assertEqual(response.status_code,302);action.refresh_from_db();self.assertEqual(action.status,'resolved')
        self.client.force_login(self.reader)
        self.assertEqual(self.client.post(reverse('ui:reports:verify',args=[self.project.pk,action.pk]),{'dossier':new.pk}).status_code,403)

    def test_foreign_groups_and_verification_reports_are_rejected(self):
        other_project=Project.objects.create(name='Other',website='https://other.example/',created_by=self.other)
        batch=ImportBatch.objects.create(project=other_project,created_by=self.other,source='semscan',method='scan',label='Other',status='complete',request_metadata={'scan':'crawl'})
        foreign=Dossier.objects.create(project=other_project,crawl=batch,actor=self.other,title='Other',status='complete')
        group=IssueGroup.objects.create(dossier=foreign,fingerprint='f'*64,rule='http_error',category='technical',title='Other problem',severity='high',basis='Rule')
        with self.assertRaises(ValidationError):create_action(self.project,self.owner,[group],{'title':'x','acceptance':'y'})
        action=self.action()
        with self.assertRaises(Dossier.DoesNotExist):verify_action(action.pk,foreign.pk,self.owner,True)

    def test_truncated_evidence_never_produces_a_numeric_score(self):
        with patch('reporting.services.MAX_OCCURRENCES',2):dossier=self.report()
        self.assertIn('occurrence_limit',dossier.coverage['limits'])
        self.assertIsNone(dossier.categories.get(key='content').score)

    def test_deleted_new_source_is_not_verified_as_resolved(self):
        action=self.action();new=self.report(self.make_crawl('2026-11-03T11:00:00+00:00'))
        new.pages.first().source_record.delete()
        verify_action(action.pk,new.pk,self.owner,True);action.refresh_from_db();self.assertNotEqual(action.status,'resolved')

    def test_score_threshold_is_not_rounded_up(self):
        state,value,coverage,_=score(({'a':'pass' if i<7996 else 'unknown'} for i in range(10000)),['a'])
        self.assertEqual(coverage['percent'],80.0);self.assertEqual(state,'insufficient');self.assertIsNone(value)

    def test_same_signature_in_new_report_requires_explicit_action_link(self):
        from .actions import link_action
        first=self.report();action=self.action(first)
        second=self.report();new_group=second.groups.get()
        self.assertEqual(action.groups.count(),1)
        other=create_action(self.project,self.owner,[new_group],{'title':'Nieuwe opdracht','acceptance':'Nieuwe criteria'})
        self.assertNotEqual(other.pk,action.pk)
        link_action(action.pk,new_group,self.owner)
        self.assertEqual(action.groups.count(),2)

    def test_link_action_endpoint_is_scoped_and_preserves_history(self):
        first=self.report();action=self.action(first);second=self.report();group=second.groups.get()
        self.client.force_login(self.owner)
        url=reverse('ui:reports:group-link',args=[self.project.pk,group.pk])
        self.assertEqual(self.client.post(url,{'action':action.pk}).status_code,302)
        self.assertEqual(action.groups.count(),2)
        self.assertTrue(action.events.filter(kind='linked').exists())
        self.client.force_login(self.reader)
        self.assertEqual(self.client.post(url,{'action':action.pk}).status_code,403)

    def test_actual_crawl_analysis_flows_into_report_and_verification(self):
        from sources.services import publish_records
        from scans.extraction import extract_page
        from types import SimpleNamespace
        def crawl(measured,broken):
            batch=ImportBatch.objects.create(project=self.project,created_by=self.owner,source='semscan',method='scan',label='Pipeline fixture',
                request_metadata={'scan':'crawl','url':'https://example.com/'})
            records=[]
            for index,(path,status,html) in enumerate([('/',200,'<a href="/target/">Target</a>'),('/target/',404 if broken else 200,'Content')]):
                url='https://example.com'+path
                fields,_=extract_page(SimpleNamespace(url=url,status=status,headers={'content-type':'text/html'},body=('<html lang="nl"><title>Page '+path+'</title><meta name="description" content="Description"><h1>Heading</h1>'+html+'</html>').encode()))
                records.append({'locator':'p:'+str(index),'data':{'url':url,'status':status,'final_status':status,'content_type':'text/html','measured_at':measured,**fields}})
            publish_records(batch_id=batch.pk,records=records)
            return batch
        old=self.report(crawl('2026-11-03T10:00:00+00:00',True))
        self.assertEqual(old.pages.get(url='https://example.com/').checks['link_to_404'],'issue')
        action=self.action(old)
        new=self.report(crawl('2026-11-03T11:00:00+00:00',False))
        verify_action(action.pk,new.pk,self.owner,True);action.refresh_from_db()
        self.assertEqual(action.status,'resolved')
