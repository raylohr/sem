import io
from datetime import datetime
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook
from projects.models import Project, Membership
from .models import ImportBatch, SourceRecord


class ExcelInventoryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('export-user')
        self.project = Project.objects.create(name='Export', website='https://example.com', domain='example.com', created_by=self.user)
        Membership.objects.create(project=self.project, user=self.user, role='viewer')
        self.client.force_login(self.user)
        self.url = reverse('ui:export-urls', args=[self.project.pk])

    def record(self, source, data, *, metadata=None, status='complete', project=None):
        batch = ImportBatch.objects.create(project=project or self.project, created_by=self.user, source=source,
            method='file', label='Import '+source, status=status, request_metadata=metadata or {})
        return SourceRecord.objects.create(batch=batch, locator='1', data=data)

    def download(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('.xlsx', response['Content-Disposition'])
        data = b''.join(response.streaming_content)
        return load_workbook(io.BytesIO(data), data_only=False)

    def test_workbook_structure_separate_sources_dates_and_action_signals(self):
        url='https://example.com/a'
        record = self.record('gsc', {'URL':url,'Last crawled':'2026-10-13', 'gsc_indexing_reason':'Niet gevonden (404)', 'gsc_report_status':'not_indexed'})
        self.record('gsc', {'url':url,'date':'2026-11-03','raw':{'clicks':1,'impressions':10}})
        self.record('sf', {'Address':url,'Crawl Timestamp':'2026-11-01 12:00:00','Status Code':'200','Content Type':'text/html','Title 1':'Titel','H1-1':'H1','Canonical Link Element 1':url})
        self.record('ahrefs', {'URL':url,'HTTP status code':'200','Content type':'text/html','Title':'','PR':'60','Organic traffic':'10'}, metadata={'type':'ahrefs_site_audit_missing_titles'})
        self.record('sf', {'Address':'https://example.com/z','Crawl Timestamp':'2026-11-01','Status Code':'404'})
        book=self.download(url_q='not-matching',url_page=99)
        self.assertEqual(book.sheetnames,['00 - HOOFDOVERZICHT','01 - URL INVENTARIS','02 - URL ACTIEPLAN','03 - BRONWAARNEMINGEN','LEESMIJ'])
        sheet=book['01 - URL INVENTARIS']
        headers=[cell.value for cell in sheet[1]]
        values=dict(zip(headers,[cell.value for cell in sheet[2]]))
        self.assertEqual(sheet.max_row,3)
        self.assertEqual(values['URL'],url)
        self.assertEqual(values['GSC indexeringsreden'],'Niet gevonden (404)')
        self.assertEqual(values['SF HTTP-status'],'200')
        self.assertEqual(values['GSC laatste waarneming'],datetime(2026,11,3))
        self.assertEqual(values['GSC indexeringswaarneming'],datetime(2026,10,13))
        self.assertIsNone(values['Concrete actie'])
        self.assertIsNone(values['Prioriteit'])
        self.assertEqual(sheet.freeze_panes,'D2')
        self.assertEqual(len(sheet.data_validations.dataValidation),2)
        self.assertEqual(sheet.cell(2,headers.index('GSC laatste waarneming')+1).number_format,'dd.mm.yyyy')
        self.assertEqual(sheet['A2'].hyperlink.target,url)
        signals=[row[3] for row in book['02 - URL ACTIEPLAN'].iter_rows(min_row=2,values_only=True)]
        self.assertTrue(any('GSC: Niet gevonden (404)' in signal and 'Ahrefs: ontbrekende titel' in signal for signal in signals))
        observation_headers=[c.value for c in book['03 - BRONWAARNEMINGEN'][1]]
        observations=[dict(zip(observation_headers,row)) for row in book['03 - BRONWAARNEMINGEN'].iter_rows(min_row=2,values_only=True)]
        self.assertEqual(len(observations),5)
        self.assertTrue(any(str(record.pk)==str(row['Record-ID']) and reverse('ui:record',args=[self.project.pk,record.batch_id,record.pk]) in row['Bronrecord'] for row in observations))
        book.close()

    def test_filters_pending_foreign_hosts_and_other_projects_and_omits_empty_columns(self):
        self.record('sf',{'Address':'https://example.com/a','Crawl Timestamp':'2026-11-01'})
        self.record('ahrefs',{'URL':'https://example.com/pending'},status='pending')
        self.record('gsc',{'URL':'https://external.test/a'})
        other=Project.objects.create(name='Other',website='https://example.com',created_by=self.user)
        self.record('gsc',{'url':'https://example.com/secret'},project=other)
        book=self.download()
        sheet=book['01 - URL INVENTARIS']
        headers=[cell.value for cell in sheet[1]]
        self.assertEqual(sheet.max_row,2)
        self.assertIn('SF laatste waarneming',headers)
        self.assertFalse(any(header.startswith(('GSC','Ahrefs')) for header in headers))
        self.assertEqual(book['03 - BRONWAARNEMINGEN'].max_row,2)
        book.close()

    def test_source_text_is_not_an_excel_formula_and_fallback_is_explicit(self):
        self.record('sf',{'Address':'https://example.com/a','Title 1':'=HYPERLINK("https://evil.test","click")','Content Type':'text/html'})
        book=self.download()
        sheet=book['01 - URL INVENTARIS']
        headers=[c.value for c in sheet[1]]
        cell=sheet.cell(2,headers.index('SF titel')+1)
        self.assertEqual(cell.data_type,'s')
        self.assertTrue(cell.value.startswith('='))
        self.assertEqual(sheet.cell(2,headers.index('Importdatum gebruikt voor bronnen')+1).value,'SF')
        book.close()

    def test_empty_project_has_valid_workbook_and_access_is_scoped(self):
        book=self.download()
        self.assertEqual(book['01 - URL INVENTARIS'].max_row,1)
        self.assertEqual(book['02 - URL ACTIEPLAN'].max_row,1)
        book.close()
        self.client.force_login(get_user_model().objects.create_user('export-outsider'))
        self.assertEqual(self.client.get(self.url).status_code,404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code,302)
