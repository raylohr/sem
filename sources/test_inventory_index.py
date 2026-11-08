from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from projects.models import Project, Membership
from .models import ImportBatch, SourceRecord, InventoryState, InventoryURL, InventoryExport
from .inventory_index import Inventory, InventoryPending, InventoryChanged, invalidate, rebuild
from .inventory_query import DatabaseRows
from .configurable_inventory import selected_columns, build_dataset_table
from .url_inventory import project_inventory
from .tasks import build_inventory_export, cleanup_inventory_exports


class InventoryIndexTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('indexed')
        self.project = Project.objects.create(name='Index', domain='example.com', website='https://example.com', created_by=self.user)
        Membership.objects.create(project=self.project, user=self.user, role='viewer')
        self.batch = ImportBatch.objects.create(project=self.project, source='ahrefs', created_by=self.user,
            method='file', label='Pages', status='complete', request_metadata={'type': 'ahrefs_site_audit_pages'})
        self.client.force_login(self.user)

    def add(self, count=1):
        SourceRecord.objects.bulk_create([SourceRecord(batch=self.batch, locator=str(index), data={
            'URL': f'https://example.com/{index:05}', 'Title': f'Title {index}',
            'Organic traffic': str(index), 'Category': 'A' if index % 2 else 'B'}) for index in range(count)])

    def test_projection_matches_reference_and_bulk_updates_invalidate(self):
        self.add(125)
        inventory = Inventory(self.project)
        _, expected = project_inventory(self.project, include_fields=True)
        actual = list(inventory.rows)
        for left, right in zip(actual, expected, strict=True):
            self.assertEqual(left['url'], right['url'])
            self.assertEqual(left['title'], right['title'])
            self.assertEqual({key: value['value'] for key, value in left['field_values'].items()},
                             {key: value['value'] for key, value in right['field_values'].items()})
        SourceRecord.objects.filter(batch=self.batch, locator='0').update(data={'URL': 'https://example.com/00000', 'Organic traffic': '999'})
        with override_settings(INVENTORY_BUILD_INLINE=False):
            with self.assertRaises(InventoryPending): Inventory(self.project)
        self.assertEqual(Inventory(self.project).rows[0]['observations']['ahrefs']['traffic'], 999)

    def test_web_reads_only_a_page_and_does_not_rebuild_ready_inventory(self):
        self.add(205)
        Inventory(self.project)
        with override_settings(INVENTORY_BUILD_INLINE=False), patch('sources.inventory_index.rebuild', side_effect=AssertionError('HTTP rebuild')):
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(reverse('ui:project', args=[self.project.pk]), {'url_page': '2'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['inventory_page']), 100)
        self.assertEqual(response.context['inventory_page'].paginator.count, 205)
        self.assertTrue(any('LIMIT 100 OFFSET 100' in query['sql'] for query in queries))
        self.assertFalse(any('FROM "sources_sourcerecord"' in query['sql'] for query in queries))

    def test_delete_and_host_changes_never_serve_stale_projection(self):
        self.add()
        Inventory(self.project)
        self.batch.delete()
        with override_settings(INVENTORY_BUILD_INLINE=False):
            response = self.client.get(reverse('ui:project', args=[self.project.pk]))
            self.assertEqual(response.status_code, 202)
            self.assertNotContains(response, 'https://example.com/00000', status_code=202)
        self.assertEqual(len(Inventory(self.project).rows), 0)
        self.project.domain = 'other.test'
        self.project.save()
        with override_settings(INVENTORY_BUILD_INLINE=False):
            with self.assertRaises(InventoryPending): Inventory(self.project)

    def test_generation_replacement_does_not_load_old_url_payloads(self):
        self.add(125)
        first = Inventory(self.project).state.generation
        with CaptureQueriesContext(connection) as queries:
            rebuild(self.project.pk)
        self.assertNotEqual(InventoryState.objects.get(project=self.project).generation, first)
        self.assertEqual(InventoryURL.objects.filter(project=self.project).count(), 125)
        self.assertFalse(any(query['sql'].lstrip().upper().startswith('SELECT') and
                             '"sources_inventoryurl"."payload"' in query['sql'] for query in queries))

    def test_changed_inputs_do_not_publish_half_built_generation(self):
        self.add()
        original = Inventory(self.project).state.generation
        invalidate(self.project.pk)
        from . import inventory_index
        reference = inventory_index.project_inventory
        def changing(*args, **kwargs):
            result = reference(*args, **kwargs)
            invalidate(self.project.pk)
            return result
        with patch('sources.inventory_index.project_inventory', side_effect=changing):
            with self.assertRaises(InventoryChanged): rebuild(self.project.pk)
        self.assertEqual(InventoryState.objects.get(project=self.project).generation, original)
        self.assertEqual(set(InventoryURL.objects.filter(project=self.project).values_list('generation', flat=True)), {original})

    def test_sql_filters_match_reference_including_facets_and_missing(self):
        self.add(25)
        from .column_fields import field_catalog, value_key
        from .configurable_inventory import build_dataset_table
        _, reference = project_inventory(self.project, include_fields=True)
        catalog = field_catalog(reference)
        category = next(field for field in catalog.values() if field['path'] == ['Category'])
        key = 'field_' + category['id']
        for raw in ('lo_ahrefs=5&hi_ahrefs=18&sort=ahrefs:desc', 'm_ahrefs=only',
                    f'a_{key}=1&v_{key}={value_key("A", "text")}&lo_ahrefs=2'):
            inventory = Inventory(self.project)
            params = QueryDict(raw)
            expected, old = build_dataset_table(reference, selected_columns(self.project, self.user, catalog, reference), params, catalog)
            rows = DatabaseRows(inventory)
            actual, new = build_dataset_table(rows, selected_columns(self.project, self.user, inventory.catalog, rows), params, inventory.catalog)
            self.assertEqual([row['url'] for row in actual], [row['url'] for row in expected])
            for before, after in zip(old['value_columns'], new['value_columns'], strict=True):
                self.assertEqual(after['present_count'], before['present_count'])
                self.assertEqual(after['missing_count'], before['missing_count'])
                if before['mode'] == 'choices':
                    self.assertEqual(after['choices'], before['choices'])

    def test_drafts_and_project_renames_leave_ready_overview_available(self):
        self.add()
        first = Inventory(self.project).state.generation
        draft = ImportBatch.objects.create(project=self.project, source='sf', created_by=self.user,
            method='file', label='Preview', status='pending')
        SourceRecord.objects.bulk_create([SourceRecord(batch=draft, locator='1', data={'Address': 'https://example.com/new'})])
        self.project.name = 'Renamed'
        self.project.save()
        with override_settings(INVENTORY_BUILD_INLINE=False):
            self.assertEqual(Inventory(self.project).state.generation, first)
            draft.status = 'complete'
            draft.save()
            with self.assertRaises(InventoryPending): Inventory(self.project)
        self.assertEqual(len(Inventory(self.project).rows), 2)

    def test_raw_url_fallbacks_and_unicode_order_match_reference(self):
        for index, primary in enumerate((False, 0, 0.0, [], {}, None, '')):
            SourceRecord.objects.create(batch=self.batch, locator='fallback'+str(index), data={
                'url': primary, 'URL': 'https://example.com/'+('Z', 'a', 'é', '_', '-', 'A', 'z')[index]})
        _, expected = project_inventory(self.project, include_fields=True)
        actual = list(Inventory(self.project).rows)
        self.assertEqual([row['url'] for row in actual], [row['url'] for row in expected])

    def test_numeric_choices_keep_compact_labels_and_tiny_precision(self):
        from decimal import Decimal
        from .models import InventoryValue
        from .column_fields import number
        SourceRecord.objects.create(batch=self.batch, locator='tiny', data={
            'URL': 'https://example.com/tiny', 'Organic traffic': '1.23456789e-308'})
        inventory = Inventory(self.project)
        field = next(field for field in inventory.catalog.values() if field['path'] == ['Organic traffic'])
        value = InventoryValue.objects.get(url__project=self.project, field_key=field['id']).number
        self.assertEqual(value, Decimal('1.23456789e-308'))
        self.assertEqual(str(number(Decimal('404.'+'0'*512))), '404')
        rows = DatabaseRows(inventory)
        selected = selected_columns(self.project, self.user, inventory.catalog, rows)
        _, controls = build_dataset_table(rows, selected, QueryDict(), inventory.catalog)
        self.assertEqual(len(controls['value_columns'][0]['choices']), 1)

    def test_record_delete_is_fast_and_invalidates(self):
        self.add(5)
        Inventory(self.project)
        with CaptureQueriesContext(connection) as queries:
            SourceRecord.objects.filter(batch=self.batch).delete()
        self.assertFalse(any('"sources_sourcerecord"."data"' in item['sql'] for item in queries))
        with override_settings(INVENTORY_BUILD_INLINE=False):
            with self.assertRaises(InventoryPending): Inventory(self.project)

    def test_broker_failure_leaves_export_for_durable_dispatch(self):
        from .tasks import enqueue_inventory_export, dispatch_inventory_exports
        token = uuid4()
        job = InventoryExport.objects.create(project=self.project, user=self.user, token=token)
        with patch('sources.tasks.build_inventory_export.apply_async', side_effect=RuntimeError('broker down')):
            enqueue_inventory_export(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.state, 'queued')
        with patch('sources.tasks.build_inventory_export.apply_async') as queue:
            dispatch_inventory_exports()
            queue.assert_called_once()

    def test_queued_export_is_private_downloadable_and_cleaned_up(self):
        self.add()
        Inventory(self.project)
        token = str(uuid4())
        url = reverse('ui:export-urls', args=[self.project.pk])
        with TemporaryDirectory() as media, override_settings(INVENTORY_BUILD_INLINE=False, MEDIA_ROOT=media):
            with patch('sources.tasks.build_inventory_export.apply_async') as enqueue:
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.get(url, {'export_token': token})
                self.assertEqual(response.status_code, 202)
                enqueue.assert_called_once()
            job = InventoryExport.objects.get(token=token)
            build_inventory_export(str(job.pk))
            job.refresh_from_db()
            self.assertEqual(job.state, 'ready')
            response = self.client.get(url, {'export_token': token})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(b''.join(response.streaming_content).startswith(b'PK'))
            other = get_user_model().objects.create_user('other-indexed')
            Membership.objects.create(project=self.project, user=other, role='viewer')
            self.client.force_login(other)
            with patch('sources.tasks.build_inventory_export.apply_async'):
                self.assertEqual(self.client.get(url, {'export_token': token}).status_code, 202)
            self.client.force_login(self.user)
            Membership.objects.filter(project=self.project, user=self.user).delete()
            self.assertEqual(self.client.get(url, {'export_token': token}).status_code, 404)
            storage, filename = job.file.storage, job.file.name
            InventoryExport.objects.filter(pk=job.pk).update(created_at=timezone.now() - timedelta(days=2))
            cleanup_inventory_exports()
            self.assertFalse(storage.exists(filename))
