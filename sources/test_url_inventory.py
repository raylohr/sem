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
        _, rows = project_inventory(self.project)
        self.assertEqual([r['path'] for r in rows], ['/complete'])
        self.project.include_subdomains = True
        _, rows = project_inventory(self.project)
        self.assertEqual([r['path'] for r in rows], ['/complete', '/sub'])

    def test_future_source_import_fallback_and_distinct_url_variants(self):
        stamp = datetime(2026, 11, 4, 12, tzinfo=dt_timezone.utc)
        for url in ['https://example.com/a', 'https://example.com/a?x=1', 'https://www.example.com/a']:
            self.add('future_source', {'url': url}, imported_at=stamp)
        columns, rows = project_inventory(self.project)
        self.assertIn('future_source', [c['key'] for c in columns])
        self.assertEqual(len(rows), 3)
        observation = rows[0]['observations']['future_source']
        self.assertTrue(observation['fallback'])
        self.assertEqual(observation['date'].isoformat(), '2026-11-04')
        response = self.client.get(reverse('ui:project', args=[self.project.pk]))
        self.assertContains(response, '04.11*')
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
        self.assertContains(response, reverse('ui:record', args=[self.project.pk, batch.pk, record.pk]))
        self.assertEqual(len(response.context['inventory_page']), 100)
        self.assertEqual(len(self.client.get(url, {'url_page': 2}).context['inventory_page']), 2)
        filtered = self.client.get(url, {'url_q': 'find-me'})
        self.assertEqual(filtered.context['inventory_page'].paginator.count, 1)
        self.assertContains(self.client.get(url, {'url_q': 'absent'}), 'Geen URLs gevonden voor deze zoekterm.')
        self.client.force_login(get_user_model().objects.create_user('inventory-outsider'))
        self.assertEqual(self.client.get(url).status_code, 404)
