import csv
import io
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from openpyxl import Workbook

from projects.models import Membership, Project
from .file_detection import detect_export
from .models import ImportBatch, SourceRecord
from .screamingfrog import DUTCH_HEADERS, content_checksum
from .sf_reports import parse_table
from .url_inventory import project_inventory

REPORT_ROWS = {
    'response_codes': {'Adres':'https://example.com/a', 'Inhoudstype':'text/html', 'Statuscode':'200',
                       'Indexeerbaarheid':'Indexeerbaar', 'Reactietijd':'0.2', 'Omleidings-URL':''},
    'external_all': {'Adres':'https://external.test/a', 'Inhoudstype':'text/html', 'Statuscode':'200',
                     'Status':'OK', 'Crawldiepte':'1', 'Inlinks':'2'},
    'page_titles': {'Adres':'https://example.com/a', 'Voorkomens':'1', 'Titel 1':'Example',
                    'Lengte van titel 1':'7', 'Pixelbreedte van titel 1':'40', 'Indexeerbaarheid':'Indexeerbaar'},
    'hreflang': {'Adres':'https://example.com/a', 'Voorkomens':'2', 'HTML hreflang 1':'en',
                 'HTML hreflang 1 URL':'https://example.com/a', 'Indexeerbaarheid':'Indexeerbaar'},
    'issues_overview': {'Probleemnaam':'Example issue', 'Probleemtype':'Waarschuwing',
                        'Probleemprioriteit':'Laag', "URL's":'2', 'Beschrijving':'Example', 'Hoe op te lossen':'Fix'},
    'indexability_summary': {'Pad 1':'https/', 'Pad 2':'example.com/', 'Pad 3':'', "URL's":'3',
                             'Indexeerbaar':'2', 'Niet-indexeerbaar':'1'},
    'sitemap_inlinks': {'Type':'Sitemap', 'Bron':'https://example.com/sitemap.xml',
                        'Bestemming':'https://example.com/a', 'Linkpad':'', 'Linkpositie':''},
}


def tabular_upload(kind, *, english=False, xlsx=False, empty=False, row=None):
    data = dict(REPORT_ROWS[kind] if row is None else row)
    if english:
        data = {DUTCH_HEADERS.get(k,k):v for k,v in data.items()}
        if 'Indexability' in data:
            data['Indexability'] = 'Indexable'
        if 'Issue Type' in data:
            data.update({'Issue Type':'Warning', 'Issue Priority':'Low'})
    headers = list(data)
    if xlsx:
        book = Workbook()
        book.active.append(headers)
        if not empty:
            book.active.append(list(data.values()))
        stream = io.BytesIO()
        book.save(stream)
        book.close()
        return SimpleUploadedFile('renamed.xlsx', stream.getvalue())
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(headers)
    if not empty:
        writer.writerow(list(data.values()))
    return SimpleUploadedFile('renamed.csv', stream.getvalue().encode('utf-8'))


def archive_upload(*, wal=b'', invalid_url=False, view=False):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'crawl.sqlite'
        connection = sqlite3.connect(path)
        connection.execute('CREATE TABLE urls (encoded_url TEXT, is_internal INT, response_code INT, '
                           'content_type TEXT, timestamp INT, title_1 TEXT, response_time_ms INT)')
        for address, internal in [('https://example.com/a',1), ('https://external.test/a',0)]:
            connection.execute('INSERT INTO urls VALUES (?,?,?,?,?,?,?)',
                               ('javascript:bad' if invalid_url else address, internal,200,'text/html',
                                1793955600000,'Example',200))
        if view:
            connection.execute('ALTER TABLE urls RENAME TO hidden_urls')
            connection.execute('CREATE VIEW urls AS SELECT * FROM hidden_urls')
        connection.commit()
        connection.close()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('DbSeoSpiderFileKey', 'url=https://example.com/')
            archive.writestr('serialised_data/spiderconfig.json', '{"password":"do-not-import"}')
            archive.write(path, 'results_test/sql')
            archive.writestr('results_test/sql-wal',wal)
        return SimpleUploadedFile('renamed.zip',stream.getvalue())


class ReportParserTests(SimpleTestCase):
    def test_every_report_detected_in_dutch_csv_and_english_xlsx(self):
        for kind in REPORT_ROWS:
            with self.subTest(kind=kind):
                dutch = detect_export(tabular_upload(kind))
                english = detect_export(tabular_upload(kind, english=True, xlsx=True))
                self.assertEqual(dutch['summary']['type'],kind)
                self.assertEqual(english['summary']['type'],kind)
                self.assertEqual(dutch['rows'],english['rows'])
                self.assertEqual(content_checksum(dutch['rows'],kind),content_checksum(english['rows'],kind))
                self.assertNotEqual(dutch['checksum'],english['checksum'])

    def test_empty_sitemap_is_valid_and_relations_keep_repeated_destinations(self):
        result = detect_export(tabular_upload('sitemap_inlinks',empty=True))
        self.assertEqual(result['rows'],[])
        self.assertTrue(result['summary']['allow_empty'])
        row = REPORT_ROWS['sitemap_inlinks']
        values = [list(row.values()),list(dict(row, Bron='https://example.com/sitemap2.xml').values())]
        result = parse_table(list(row),values,checksum='test')
        self.assertEqual(len(result['rows']),2)
        self.assertEqual(result['rows'][0]['Address'],'https://example.com/a')

    def test_rejects_invalid_urls_duplicate_aliases_and_malformed_rows(self):
        row = dict(REPORT_ROWS['page_titles'],Adres='javascript:bad')
        with self.assertRaises(ValueError):
            detect_export(tabular_upload('page_titles',row=row))
        row = dict(REPORT_ROWS['page_titles'],Address='https://example.com/a')
        with self.assertRaises(ValueError):
            detect_export(tabular_upload('page_titles',row=row))
        row = REPORT_ROWS['page_titles']
        with self.assertRaises(ValueError):
            parse_table(list(row),[list(row.values())[:-1]],checksum='test')
        with self.assertRaises(ValueError):
            parse_table(list(row),[list(row.values())]*2,checksum='test')
        with patch('sources.sf_reports.MAX_ROWS',0),self.assertRaises(ValueError):
            detect_export(tabular_upload('page_titles'))

    def test_report_type_is_part_of_fingerprint(self):
        row = {'Address':'https://example.com/a'}
        self.assertNotEqual(content_checksum([row],'page_titles'),content_checksum([row],'hreflang'))

    def test_archive_reads_snapshot_and_omits_configuration(self):
        result = detect_export(archive_upload())
        self.assertEqual(result['summary']['type'],'crawl_archive')
        self.assertEqual(len(result['rows']),2)
        self.assertEqual(result['rows'][0]['Title 1'],'Example')
        self.assertEqual(result['rows'][0]['Response Time'],'0.2')
        self.assertIn('+00:00',result['rows'][0]['Crawl Timestamp'])
        self.assertNotIn('do-not-import',str(result))
        self.assertTrue(result['summary']['archive_snapshot'])

    def test_archive_rejects_wal_views_invalid_urls_and_size_overruns(self):
        for kwargs in ({'wal':b'pending'}, {'view':True}, {'invalid_url':True}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                detect_export(archive_upload(**kwargs))
        with patch('sources.sf_archive.MAX_ARCHIVE_BYTES',20),self.assertRaises(ValueError):
            detect_export(archive_upload())
        with patch('sources.sf_archive.MAX_ROWS',1),self.assertRaises(ValueError):
            detect_export(archive_upload())


class ReportImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('reports')
        self.project = Project.objects.create(name='Example',domain='example.com',website='https://example.com/',created_by=self.user)
        Membership.objects.create(project=self.project,user=self.user,role='editor')
        self.client.force_login(self.user)
        self.upload_url = reverse('ui:import',args=[self.project.pk])

    def complete(self, upload):
        response = self.client.post(self.upload_url,{'file':upload})
        self.assertEqual(response.status_code,302)
        preview = response.url
        self.assertEqual(self.client.get(preview).status_code,200)
        response = self.client.post(preview,{'action':'confirm'})
        self.assertEqual(response.status_code,302)
        return ImportBatch.objects.latest('imported_at')

    def test_summary_preserved_and_all_rows_visible_without_inventory_urls(self):
        for kind in ('indexability_summary','issues_overview'):
            batch = self.complete(tabular_upload(kind))
            self.assertEqual(batch.status,'complete')
            self.assertEqual(batch.record_count,1)
            page = self.client.get(reverse('ui:batch',args=[self.project.pk,batch.pk]))
            self.assertContains(page,REPORT_ROWS[kind][next(iter(REPORT_ROWS[kind]))])
            query = 'Example issue' if kind == 'issues_overview' else 'https/'
            self.assertContains(self.client.get(reverse('ui:batch',args=[self.project.pk,batch.pk]),{'q':query}),query)
        self.assertEqual(project_inventory(self.project)[1],[])

    def test_external_urls_stay_in_report_and_out_of_inventory(self):
        batch = self.complete(tabular_upload('external_all'))
        self.assertEqual(batch.record_count,1)
        self.assertEqual(batch.records.get().data['Address'],'https://external.test/a')
        self.assertEqual(project_inventory(self.project)[1],[])

    def test_empty_sitemap_can_confirm_and_remains_empty(self):
        batch = self.complete(tabular_upload('sitemap_inlinks',empty=True))
        self.assertEqual(batch.status,'complete')
        self.assertEqual(batch.record_count,0)
        self.assertFalse(batch.records.exists())
        self.assertContains(self.client.get(reverse('ui:batch',args=[self.project.pk,batch.pk])),'Dit rapport bevat geen records')

    def test_language_and_file_format_duplicate_uses_original_batch(self):
        batch = self.complete(tabular_upload('page_titles'))
        response = self.client.post(self.upload_url,{'file':tabular_upload('page_titles',english=True,xlsx=True)})
        self.assertEqual(response.url,reverse('ui:batch',args=[self.project.pk,batch.pk]))
        self.assertEqual(ImportBatch.objects.count(),1)

    def test_archive_filters_foreign_urls(self):
        batch = self.complete(archive_upload())
        self.assertEqual(batch.record_count,1)
        self.assertEqual(batch.request_metadata['excluded_rows'],1)
        self.assertEqual(project_inventory(self.project)[1][0]['url'],'https://example.com/a')

    def test_foreign_url_report_cannot_become_empty_valid_report(self):
        row = dict(REPORT_ROWS['page_titles'],Adres='https://foreign.test/a')
        response = self.client.post(self.upload_url,{'file':tabular_upload('page_titles',row=row)})
        self.assertContains(response,'Geen URLs binnen de toegestane hosts')
        self.assertFalse(ImportBatch.objects.exists())

    def test_new_title_report_does_not_erase_existing_content_badge(self):
        self.complete(archive_upload())
        self.complete(tabular_upload('page_titles'))
        rows = project_inventory(self.project)[1]
        self.assertEqual(rows[0]['observations']['sf']['badge']['label'],'HTML')

    def test_summary_permissions_and_project_isolation(self):
        batch = self.complete(tabular_upload('issues_overview'))
        other = Project.objects.create(name='Other',domain='other.test',website='https://other.test/',created_by=self.user)
        Membership.objects.create(project=other,user=self.user,role='editor')
        self.client.post(reverse('ui:import',args=[other.pk]),{'file':tabular_upload('issues_overview')})
        self.assertEqual(ImportBatch.objects.count(),2)
        member = Membership.objects.get(project=self.project,user=self.user)
        member.role = 'viewer'
        member.save()
        self.assertEqual(self.client.post(self.upload_url,{'file':tabular_upload('indexability_summary')}).status_code,403)
        self.assertEqual(self.client.get(reverse('ui:batch',args=[other.pk,batch.pk])).status_code,404)

    def test_staged_title_report_rechecks_hosts(self):
        response = self.client.post(self.upload_url,{'file':tabular_upload('page_titles')})
        batch = ImportBatch.objects.get()
        self.project.domain = 'other.test'
        self.project.save()
        self.client.post(response.url,{'action':'confirm'})
        batch.refresh_from_db()
        self.assertEqual(batch.status,'pending')
        self.assertEqual(batch.record_count,0)
        self.assertFalse(batch.records.exists())

    def test_new_hash_version_rechecks_legacy_normalized_statuses(self):
        batch = self.complete(tabular_upload('page_titles'))
        batch.request_metadata['content_checksum'] = 'old-normalization-hash'
        batch.request_metadata.pop('content_checksum_version')
        batch.save(update_fields=['request_metadata'])
        response = self.client.post(self.upload_url,{'file':tabular_upload('page_titles',english=True,xlsx=True)})
        self.assertEqual(response.url,reverse('ui:batch',args=[self.project.pk,batch.pk]))
        self.assertEqual(ImportBatch.objects.count(),1)
