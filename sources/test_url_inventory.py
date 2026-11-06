from datetime import datetime, timezone as dt_timezone
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from projects.models import Project, Membership
from .models import ImportBatch, SourceRecord
from .url_inventory import project_inventory


class URLInventoryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('inventory-user')
        self.project = Project.objects.create(name='Inventory', website='https://example.com', domain='example.com', created_by=self.user)
        Membership.objects.create(project=self.project, user=self.user, role='admin')
        self.client.force_login(self.user)

    def add(self, source, data, status='complete', project=None, imported_at=None):
        batch = ImportBatch.objects.create(project=project or self.project, created_by=self.user,
                                          source=source, method='file', status=status, label=source)
        if imported_at:
            ImportBatch.objects.filter(pk=batch.pk).update(imported_at=imported_at)
        record = SourceRecord.objects.create(batch=batch, locator='1', data=data)
        return batch, record

    def test_groups_full_url_and_latest_observation_per_source_sorted_by_path(self):
        url = 'https://example.com/a'
        self.add('gsc', {'url': url, 'date': '2026-10-30'})
        latest, record = self.add('gsc', {'url': url, 'date': '2026-11-03'})
        self.add('gsc', {'url': url, 'date': '2026-10-01'})
        self.add('sf', {'Address': url, 'Crawl Timestamp': '2026-11-02 13:00:00'})
        self.add('gsc', {'URL': 'https://example.com/z', 'Last crawled': '2026-10-24'})
        self.add('ahrefs', {'url': 'https://www.example.com/b', 'last_seen': '2026-11-01'})
        columns, rows = project_inventory(self.project)
        self.assertEqual([c['key'] for c in columns], ['ahrefs', 'gsc', 'sf'])
        self.assertEqual([r['path'] for r in rows], ['/a', '/b', '/z'])
        self.assertEqual(len(rows), 3)
        gsc = rows[0]['observations']['gsc']
        self.assertEqual(gsc['date'].isoformat(), '2026-11-03')
        self.assertEqual(gsc['record_id'], record.pk)
        self.assertEqual(gsc['batch_id'], latest.pk)
        self.assertIsNone(rows[0]['cells'][0])
        self.assertEqual(rows[2]['observations']['gsc']['date'].isoformat(), '2026-10-24')

    def test_scopes_published_rows_to_project_and_current_host_rules(self):
        self.add('gsc', {'url': 'https://example.com/complete', 'date': '2026-11-01'})
        self.add('sf', {'Address': 'https://example.com/pending'}, status='pending')
        self.add('gsc', {'url': 'https://example.com/failed'}, status='failed')
        self.add('sf', {'Address': 'https://blog.example.com/sub'})
        self.add('gsc', {'url': 'https://elsewhere.test/foreign'})
        other = Project.objects.create(name='Other', website='https://example.com', created_by=self.user)
        self.add('gsc', {'url': 'https://example.com/other-project'}, project=other)
        columns, rows = project_inventory(self.project)
        self.assertEqual([column['key'] for column in columns], ['gsc'])
        self.assertEqual([r['path'] for r in rows], ['/complete'])
        self.project.include_subdomains = True
        _, rows = project_inventory(self.project)
        self.assertEqual([r['path'] for r in rows], ['/complete', '/sub'])

    def test_future_source_import_fallback_and_distinct_url_variants(self):
        stamp = datetime(2026, 11, 4, 12, tzinfo=dt_timezone.utc)
        for url in ['https://example.com/a', 'https://example.com/a?x=1', 'https://www.example.com/a']:
            self.add('future_source', {'url': url}, imported_at=stamp)
        columns, rows = project_inventory(self.project)
        self.assertEqual([c['key'] for c in columns], ['future_source'])
        self.assertEqual(len(rows), 3)
        observation = rows[0]['observations']['future_source']
        self.assertTrue(observation['fallback'])
        self.assertEqual(observation['date'].isoformat(), '2026-11-04')
        response = self.client.get(reverse('ui:project', args=[self.project.pk]))
        self.assertContains(response, '04.11.2026')
        self.assertContains(response, 'Future source')

    def test_overview_renders_dates_source_links_search_and_pagination(self):
        batch, record = self.add('gsc', {'url': 'https://example.com/find-me', 'date': '2026-11-03'})
        for index in range(101):
            SourceRecord.objects.create(batch=batch, locator=str(index + 2), data={
                'url': f'https://example.com/item-{index:03}', 'date': '2026-11-02'})
        url = reverse('ui:project', args=[self.project.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '102 unieke URLs')
        self.assertContains(response, '03.11')
        self.assertNotContains(response, reverse('ui:record', args=[self.project.pk, batch.pk, record.pk]))
        self.assertEqual(len(response.context['inventory_page']), 100)
        self.assertEqual(len(self.client.get(url, {'url_page': 2}).context['inventory_page']), 2)
        filtered = self.client.get(url, {'url_q': 'find-me'})
        self.assertEqual(filtered.context['inventory_page'].paginator.count, 1)
        self.assertContains(self.client.get(url, {'url_q': 'absent'}), 'Geen URLs gevonden voor deze zoekterm.')
        self.client.force_login(get_user_model().objects.create_user('inventory-outsider'))
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_sf_badges_keep_date_provenance_and_ahrefs_traffic(self):
        for index, data in enumerate([
            {'Content Type':'text/html; charset=UTF-8','Status Code':'200'},
            {'Content Type':'image/png','Status Code':'200'},
            {'Content Type':'text/html','Status Code':'404'},
        ]):
            self.add('sf', {'Address':f'https://example.com/{index}', 'Crawl Timestamp':'2026-11-02 12:00:00', **data})
        self.add('ahrefs', {'URL':'https://example.com/0','last_seen':'2026-11-03','Organic traffic':'1152'})
        self.add('ahrefs', {'URL':'https://example.com/0','last_seen':'2026-10-01','Organic traffic':'9000'})
        self.add('ahrefs', {'URL':'https://example.com/1','last_seen':'2026-11-03','Organic traffic':'0'})
        self.add('ahrefs', {'URL':'https://example.com/2','last_seen':'2026-11-03'})
        response=self.client.get(reverse('ui:project',args=[self.project.pk]))
        for expected in ('content-type-html','content-type-other','content-type-error','HTTP 404','Afbeelding','03.11.2026','02.11.2026','1.152','>0</a>'):
            self.assertContains(response,expected)
        columns,rows=project_inventory(self.project)
        self.assertEqual(rows[0]['observations']['sf']['badge']['label'],'HTML')
        self.assertNotIn('badge',rows[0]['observations']['ahrefs'])
        self.assertEqual(rows[0]['observations']['ahrefs']['traffic'],1152)
        self.assertEqual(rows[1]['observations']['ahrefs']['traffic'],0)
        self.assertIsNone(rows[2]['observations']['ahrefs']['traffic'])

    def test_titles_prefer_sf_then_ahrefs_and_show_source_or_dash(self):
        url = 'https://example.com/a'
        self.add('sf', {'Address':url,'Crawl Timestamp':'2026-10-01','Title 1':'Oude SF-titel'})
        self.add('sf', {'Address':url,'Crawl Timestamp':'2026-10-14','Title 1':'SF <titel>'})
        self.add('ahrefs', {'URL':url,'last_seen':'2026-10-29','Title':'Nieuwe <titel>'})
        self.add('gsc', {'URL':url,'Last crawled':'2026-11-01'})
        self.add('gsc', {'URL':'https://example.com/no-title','Last crawled':'2026-11-01'})
        self.add('ahrefs', {'URL':'https://example.com/fallback','last_seen':'2026-10-29','Title':'Ahrefs terugval'})
        self.add('sf', {'Address':'https://example.com/fallback','Crawl Timestamp':'2026-11-01','Title 1':'   '})
        response = self.client.get(reverse('ui:project',args=[self.project.pk]))
        self.assertContains(response, 'SF &lt;titel&gt;')
        self.assertContains(response, 'Ahrefs terugval')
        self.assertNotContains(response, 'title="Screaming Frog · 14.10.2026"')
        self.assertNotContains(response, 'title="Ahrefs · 29.10.2026"')
        self.assertContains(response, 'class="small text-muted inventory-full-url" href="https://example.com/a"')
        self.assertContains(response, 'aria-label="URL-details · geen titel beschikbaar"')
        self.assertNotContains(response, 'Oude SF-titel')
        self.assertEqual(response.context['inventory_page'][0]['title'], 'SF <titel>')
        _, rows = project_inventory(self.project)
        by_url = {row['url']: row for row in rows}
        self.assertEqual(by_url[url]['title_source'], 'SF')
        self.assertEqual(by_url['https://example.com/fallback']['title_source'], 'Ahrefs')
        self.assertEqual(by_url['https://example.com/no-title']['title'], '')

    def test_column_headers_modal_and_controls_survive_search_and_pagination(self):
        batch,_=self.add('sf',{'Address':'https://example.com/a','Content Type':'text/html','Status Code':'200'})
        for index in range(101):
            SourceRecord.objects.create(batch=batch,locator=str(index+2),data={
                'Address':f'https://example.com/item-{index:03}','Content Type':'text/html','Status Code':'200'})
        self.add('ahrefs',{'URL':'https://example.com/a','Organic traffic':'10'})
        url=reverse('ui:project',args=[self.project.pk])
        response=self.client.get(url,{'traffic_sort':'desc','content_filter':'1','content_type':'html','url_page':'2'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['inventory_page'].paginator.count,102)
        self.assertEqual(len(response.context['inventory_page']),2)
        for value in ('o-traffic','AHREFS','contenttype','datasetFilterModal','aria-sort="descending"','a_sf=1','sort=ahrefs%3Adesc'):
            self.assertContains(response,value)
        self.assertContains(response,'name="v_sf"')
        self.assertContains(self.client.get(url,{'content_filter':'1'}),'Geen URLs gevonden voor deze combinatie van filters.')

    def test_gsc_header_status_filter_and_unlinked_latest_indexing_status(self):
        _,old=self.add('gsc', {'URL':'https://example.com/a','Last crawled':'2026-10-13',
                            'gsc_report_status':'not_indexed','gsc_indexing_reason':'Niet gevonden (404)'})
        _,latest=self.add('gsc', {'url':'https://example.com/a','date':'2026-11-03','raw':{'clicks':1}})
        self.add('gsc', {'URL':'https://example.com/b','Last crawled':'2026-11-01','gsc_report_status':'indexed'})
        self.add('gsc', {'url':'https://example.com/c','date':'2026-11-03'})
        self.add('sf', {'Address':'https://example.com/a','Content Type':'text/html','Status Code':'200'})
        self.add('ahrefs', {'URL':'https://example.com/a','Organic traffic':'10'})
        url=reverse('ui:project',args=[self.project.pk])
        response=self.client.get(url)
        for value in ('Index Status','datasetFilterModal','gsc_report_status','not_indexed','indexed'):
            self.assertContains(response,value)
        self.assertNotContains(response,reverse('ui:record',args=[self.project.pk,latest.batch_id,latest.pk]))
        self.assertNotContains(response,reverse('ui:record',args=[self.project.pk,old.batch_id,old.pk]))
        _,rows=project_inventory(self.project)
        observation=rows[0]['observations']['gsc']
        self.assertEqual(observation['index_status'],'not_indexed')
        self.assertEqual(observation['index_date'].isoformat(),'2026-10-13')
        filtered=self.client.get(url,{'index_filter':'1','index_status':'not_indexed','content_filter':'1','content_type':'html','traffic_sort':'desc'})
        self.assertEqual(filtered.context['inventory_page'].paginator.count,1)
        self.assertEqual(filtered.context['inventory_page'][0]['url'],'https://example.com/a')
        for value in ('a_gsc=1','a_sf=1','sort=ahrefs%3Adesc'):
            self.assertIn(value,filtered.context['url_state_query'])
        self.assertContains(filtered,'name="v_gsc"')

    def test_long_urls_are_abbreviated_with_full_tooltip_and_original_link(self):
        prefix='https://example.com/'
        short=prefix+'a'*(47-len(prefix))
        long=prefix+'b'*(48-len(prefix))
        self.add('sf',{'Address':short})
        self.add('sf',{'Address':long})
        response=self.client.get(reverse('ui:project',args=[self.project.pk]))
        self.assertContains(response,f'<a class="small text-muted inventory-full-url" href="{short}" title="{short}" aria-label="{short}">{short}</a>',html=True)
        self.assertContains(response,f'<a class="small text-muted inventory-full-url" href="{long}" title="{long}" aria-label="{long}">{long[:47]}...</a>',html=True)
