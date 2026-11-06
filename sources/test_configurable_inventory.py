import io
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs,urlsplit
from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import TestCase,Client
from django.urls import reverse
from openpyxl import load_workbook
from projects.models import Project,Membership
from .models import ImportBatch,SourceRecord,InventoryPreference
from .url_inventory import project_inventory
from .column_fields import field_catalog,value_key
from .configurable_inventory import configured_field,build_table,selected_columns


class ConfigurableInventoryTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('columns-user')
        self.other=get_user_model().objects.create_user('columns-other')
        self.project=Project.objects.create(name='Columns',website='https://example.com',domain='example.com',created_by=self.user)
        for user in (self.user,self.other):Membership.objects.create(project=self.project,user=user,role='viewer')
        self.client.force_login(self.user)
        self.url=reverse('ui:project',args=[self.project.pk])
        batch=ImportBatch.objects.create(project=self.project,source='ahrefs',method='file',created_by=self.user,label='Pages',status='complete',request_metadata={'type':'ahrefs_site_audit_pages'})
        for i in range(12):
            SourceRecord.objects.create(batch=batch,locator=str(i),data={'URL':f'https://example.com/{i:02}',
                'Organic traffic':str(i),'Title':f'Unique title {i} '+('very long text '*5),'Category':'A' if i%2==0 else 'B','Count':str(i%3),'date':'2026-11-01'})
        self.batch=batch

    def data(self):
        _,rows=project_inventory(self.project,include_fields=True)
        return rows,field_catalog(rows)

    def field(self,catalog,name):
        return next(f for f in catalog.values() if f['path']==[name])

    def test_catalog_types_thresholds_and_latest_field_value_by_report(self):
        rows,catalog=self.data()
        for name,kind,mode in [('Organic traffic','number','range'),('Count','number','choices'),('Category','text','choices'),('Title','text','keyword'),('date','date','date')]:
            field=self.field(catalog,name)
            self.assertEqual(field['type'],kind)
            self.assertEqual(configured_field(field,rows)['mode'],mode)
        # A later observation without the field must not erase its last known value.
        newer=ImportBatch.objects.create(project=self.project,source='ahrefs',method='file',created_by=self.user,label='New',status='complete',measured_at='2026-11-03T12:00:00Z',request_metadata={'type':'ahrefs_site_audit_pages'})
        SourceRecord.objects.create(batch=newer,locator='1',data={'URL':'https://example.com/00','Other':'new'})
        # Same field in another report remains independently selectable.
        newer.request_metadata={'type':'other_report'};newer.save()
        SourceRecord.objects.create(batch=newer,locator='2',data={'URL':'https://example.com/00','Organic traffic':'999'})
        rows,catalog=self.data()
        traffic=[f for f in catalog.values() if f['path']==['Organic traffic']]
        self.assertEqual(len(traffic),2)
        original=next(f for f in traffic if f['report']=='ahrefs_site_audit_pages')
        self.assertEqual(rows[0]['field_values'][original['id']]['value'],'0')

    def test_number_range_text_keyword_choices_date_and_missing_zero(self):
        rows,catalog=self.data()
        columns=[configured_field(self.field(catalog,name),rows,column_id=key) for name,key in [('Organic traffic','n'),('Title','t'),('Category','c'),('date','d')]]
        params=QueryDict('lo_n=2&hi_n=8&q_t=UNIQUE+TITLE&sort=n:desc',mutable=True)
        params['a_c']='1';params.setlist('v_c',[value_key('A','text')])
        params['lo_d']='2026-11-01';params['hi_d']='2026-11-01'
        for col in columns:col['available']=True
        result,state=build_table(rows,columns,params)
        self.assertEqual([r['url'].rsplit('/',1)[-1] for r in result],['08','06','04','02'])
        self.assertEqual(columns[0]['present_count'],4)
        self.assertEqual(columns[0]['total_count'],4)
        self.assertIn('lo_n=2',columns[1]['sort_url'])
        numeric=[configured_field(self.field(catalog,'Organic traffic'),rows,column_id='n')|{'available':True}]
        rows[0]['field_values'].pop(numeric[0]['field_id'])
        result,_=build_table(rows,numeric,QueryDict('lo_n=0&hi_n=2&m_n=include&sort=n:desc'))
        self.assertEqual(result[-1]['url'],'https://example.com/00')
        self.assertEqual(len(result),3)
        result,state=build_table(rows,numeric,QueryDict('lo_n=9&hi_n=2'))
        self.assertEqual(result,[])
        self.assertTrue(state['table_errors'])

    def test_saved_layout_is_user_project_scoped_five_slots_and_filter_mode_frozen(self):
        rows,catalog=self.data();category=self.field(catalog,'Category')
        post={'columns_action':'save','field_0':category['id'],'name_0':'My categories'}
        response=self.client.post(self.url,post)
        self.assertEqual(response.status_code,302)
        pref=InventoryPreference.objects.get(user=self.user,project=self.project)
        self.assertEqual(len(pref.columns),1)
        self.assertEqual(pref.columns[0]['mode'],'choices')
        for i in range(12):SourceRecord.objects.filter(batch=self.batch,locator=str(i)).update(data={'URL':f'https://example.com/{i:02}','Category':f'Category {i}'})
        response=self.client.get(self.url)
        self.assertEqual(response.context['value_columns'][0]['mode'],'choices')
        self.assertEqual(len(response.context['value_columns'][0]['choices']),12)
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(self.url),'My categories')
        self.assertFalse(InventoryPreference.objects.filter(user=self.other).exists())
        self.client.force_login(self.user)
        # Saving configuration explicitly recalculates the mode after cardinality changes.
        self.client.post(self.url,post)
        pref.refresh_from_db();self.assertEqual(pref.columns[0]['mode'],'keyword')
        form=self.client.get(self.url).context['column_form']
        self.assertEqual(len(form.slots),5)

    def test_remove_column_clears_its_filter_and_sort_and_reset_restores_defaults(self):
        rows,catalog=self.data();field=self.field(catalog,'Organic traffic')
        self.client.post(self.url,{'columns_action':'save','field_0':field['id']})
        pref=InventoryPreference.objects.get(user=self.user);key=pref.columns[0]['id']
        response=self.client.post(self.url,{'columns_action':'save','field_0':'','return_state':f'sort={key}:desc&lo_{key}=1&url_q=example'})
        query=parse_qs(urlsplit(response.url).query)
        self.assertNotIn('sort',query);self.assertNotIn('lo_'+key,query)
        self.assertEqual(query['url_q'],['example'])
        pref.refresh_from_db();self.assertEqual(pref.columns,[])
        self.client.post(self.url,{'columns_action':'reset'})
        self.assertFalse(InventoryPreference.objects.filter(user=self.user).exists())
        self.assertTrue(self.client.get(self.url).context['value_columns'])

    def test_unavailable_source_retains_replaceable_column_and_invalid_fields_are_rejected(self):
        rows,catalog=self.data();field=self.field(catalog,'Title')
        self.client.post(self.url,{'columns_action':'save','field_0':field['id']})
        self.batch.delete()
        response=self.client.get(self.url)
        self.assertFalse(response.context['value_columns'][0]['available'])
        self.assertContains(response,'Niet beschikbaar')
        invalid=self.client.post(self.url,{'columns_action':'save','field_0':'not-in-this-project'})
        self.assertEqual(invalid.status_code,200)
        self.assertTrue(invalid.context['column_form'].errors)
        self.assertEqual(InventoryPreference.objects.get(user=self.user).columns[0]['field_id'],field['id'])

    def test_truncation_preserves_full_filter_values_and_export_values(self):
        record=SourceRecord.objects.get(batch=self.batch,locator='0')
        record.data['Title']='A'*100+'needle at end';record.save(update_fields=['data'])
        rows,catalog=self.data();field=self.field(catalog,'Title')
        self.client.post(self.url,{'columns_action':'save','field_0':field['id'],'name_0':'Long titles'})
        response=self.client.get(self.url)
        cell=response.context['inventory_page'][0]['value_cells'][0]
        self.assertLessEqual(len(cell['text']),40)
        self.assertGreater(len(cell['full']),40)
        self.assertContains(response,cell['full'])
        key=InventoryPreference.objects.get(user=self.user).columns[0]['id']
        filtered=self.client.get(self.url,{'q_'+key:'needle at end'})
        self.assertEqual(filtered.context['inventory_page'].paginator.count,1)
        response=self.client.get(reverse('ui:export-urls',args=[self.project.pk]))
        raw=b''.join(response.streaming_content)
        book=load_workbook(io.BytesIO(raw),data_only=True)
        sheet=book['01 - URL INVENTARIS'];headers=[c.value for c in sheet[1]]
        title_column=next(i for i,h in enumerate(headers,1) if h.startswith('Long titles ('))
        self.assertEqual(sheet.cell(2,title_column).value,cell['full'])
        book.close()

    def test_personal_configuration_still_requires_membership_and_csrf(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        self.assertEqual(client.post(self.url,{'columns_action':'reset'}).status_code,403)
        Membership.objects.filter(project=self.project,user=self.user).delete()
        self.assertEqual(self.client.post(self.url,{'columns_action':'reset'}).status_code,404)

    def test_exact_threshold_and_five_column_limit(self):
        rows,catalog=self.data()
        for name in ('Organic traffic','Title'):
            field=self.field(catalog,name)
            self.assertEqual(configured_field(field,rows[:9])['mode'],'choices')
            self.assertEqual(configured_field(field,rows[:10])['mode'],'range' if name=='Organic traffic' else 'keyword')
        field=self.field(catalog,'Title')
        payload={'columns_action':'save',**{f'field_{i}':field['id'] for i in range(8)}}
        self.client.post(self.url,payload)
        columns=InventoryPreference.objects.get(user=self.user).columns
        self.assertEqual(len(columns),5)
        self.assertEqual(len({col['id'] for col in columns}),5)

    def test_dataset_filters_work_without_visible_column_and_survive_layout_changes(self):
        rows,catalog=self.data()
        field=self.field(catalog,'Category');key='field_'+field['id']
        params=QueryDict(mutable=True)
        params['a_'+key]='1';params.setlist('v_'+key,[value_key('A','text')])
        response=self.client.get(self.url,params)
        self.assertEqual(response.context['url_filtered_count'],6)
        self.assertNotIn(field['id'],[column['field_id'] for column in response.context['value_columns']])
        self.assertEqual(response.context['dataset_filters'][0]['field_id'],field['id'])
        response=self.client.post(self.url,{'columns_action':'save','return_state':params.urlencode()},follow=True)
        self.assertEqual(response.context['value_columns'],[])
        self.assertEqual(response.context['url_filtered_count'],6)
        response=self.client.post(self.url,{'columns_action':'reset','return_state':params.urlencode()},follow=True)
        self.assertEqual(response.context['url_filtered_count'],6)
        response=self.client.get(self.url,{'filter_field':field['id']})
        self.assertEqual(response.context['filter_editor']['field_id'],field['id'])
        self.assertContains(response,'data-open-modal')
        self.assertNotContains(response,'data-bs-target="#columnFilter-')
        response=self.client.get(self.url,{'filter_field':'not-in-project','a_field_invalid':'1'})
        self.assertIsNone(response.context['filter_editor'])
        self.assertEqual(response.context['url_filtered_count'],12)

    def test_dataset_numeric_keyword_and_combined_filters(self):
        rows,catalog=self.data()
        number_key='field_'+self.field(catalog,'Organic traffic')['id']
        text_key='field_'+self.field(catalog,'Title')['id']
        response=self.client.get(self.url,{'lo_'+number_key:'2','hi_'+number_key:'5','q_'+text_key:'TITLE 3'})
        self.assertEqual(response.context['url_filtered_count'],1)
        self.assertEqual(len(response.context['dataset_filters']),2)
        response=self.client.get(self.url,{'lo_'+number_key:'5','hi_'+number_key:'2'})
        self.assertEqual(response.context['url_filtered_count'],0)
        self.assertTrue(response.context['table_errors'])

    def test_metrics_count_urls_with_rankings_and_prefer_sf_status(self):
        first=SourceRecord.objects.get(batch=self.batch,locator='0')
        first.data.update({'No. of keywords with Top 3 position':'4','No. of keywords with Top 10 position':'8','HTTP status code':'404'})
        first.save()
        second=SourceRecord.objects.get(batch=self.batch,locator='1')
        second.data.update({'No. of keywords with Top 3 position':'0','No. of keywords with Top 10 position':'2','HTTP status code':'404'})
        second.save()
        sf=ImportBatch.objects.create(project=self.project,source='sf',method='file',created_by=self.user,label='SF',status='complete')
        SourceRecord.objects.create(batch=sf,locator='0',data={'Address':'https://example.com/00','Status Code':'200'})
        response=self.client.get(self.url)
        self.assertEqual(response.context['inventory_metrics'],{'top3':1,'top10':2,'not_found':1})
        response=self.client.get(self.url,{'url_q':'/01'})
        self.assertEqual(response.context['inventory_metrics'],{'top3':0,'top10':1,'not_found':1})
        self.assertEqual(response.context['url_filtered_count'],1)
        self.assertEqual(response.context['url_total'],12)
        response=self.client.get(self.url,{'url_q':'/02'})
        self.assertEqual(response.context['inventory_metrics'],{'top3':None,'top10':None,'not_found':None})
