from datetime import datetime, timezone as dt_timezone
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from projects.models import Membership, Project
from .models import ImportBatch, SourceRecord
from .snapshots import classify, dataset_key, plan_project, url_key
from .url_inventory import project_inventory

S = ImportBatch.Snapshot
BASE = ['PR', 'URL', 'Title', 'Content type', 'Organic traffic', 'No. of all inlinks', 'First found at']
LINKS = ['No. of backlinks', 'No. of referring domains']


def row(path, columns=BASE, **values):
    data = {column: '1' for column in columns}
    data.update({'URL': f'https://example.com/{path}', 'Title': f'Pagina {path}', 'Content type': 'text/html',
                 'Organic traffic': '0' if path.startswith('zero') else '5'})
    data.update(values)
    return data


class SnapshotTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('snapshots')
        self.project = Project.objects.create(name='GMW Test', website='https://example.com', domain='example.com', created_by=self.user)
        Membership.objects.create(project=self.project, user=self.user, role='editor')

    def batch(self, rows, *, day=29, source='ahrefs', report='ahrefs_site_audit_pages', classify_now=True, **metadata):
        batch = ImportBatch.objects.create(
            project=self.project, source=source, method='file', created_by=self.user, status='complete',
            label=f'{report} {len(rows)}', record_count=len(rows),
            measured_at=datetime(2026, 10, day, tzinfo=dt_timezone.utc), request_metadata={'type': report, **metadata})
        SourceRecord.objects.bulk_create([SourceRecord(batch=batch, locator=str(i), data=data) for i, data in enumerate(rows)])
        return classify(batch) if classify_now else batch

    def status(self, batch):
        batch.refresh_from_db()
        return batch.snapshot_status


class ClassificationTests(SnapshotTestCase):
    def test_first_import_of_dataset_is_active(self):
        batch = self.batch([row('a'), row('b')])
        self.assertEqual(batch.snapshot_status, S.ACTIVE)
        self.assertEqual(batch.snapshot_reason, 'Eerste import van deze dataset.')
        self.assertEqual(batch.dataset_key, 'ahrefs:ahrefs_site_audit_pages')

    def test_filtered_subset_with_equal_values_is_redundant(self):
        full = self.batch([row('a'), row('b'), row('zero-c'), row('zero-d'), row('zero-e')])
        filtered = self.batch([row('a'), row('b')])
        self.assertEqual(filtered.snapshot_status, S.REDUNDANT)
        self.assertIn('al in de actieve import', filtered.snapshot_reason)
        self.assertEqual(self.status(full), S.ACTIVE)
        codes = {warning['code'] for warning in filtered.quality_warnings}
        self.assertEqual(codes, {'row_drop', 'filtered'})
        self.assertIn('Organic traffic > 0', next(w['message'] for w in filtered.quality_warnings if w['code'] == 'filtered'))
        _, rows = project_inventory(self.project)
        self.assertEqual({item['observations']['ahrefs']['batch_id'] for item in rows}, {full.pk})

    def test_gmw_filtered_subset_with_extra_columns_is_supplement_and_keeps_counting(self):
        full = self.batch([row('a'), row('b'), row('zero-c')])
        filtered = self.batch([row('a', BASE + LINKS), row('b', BASE + LINKS)])
        self.assertEqual(filtered.snapshot_status, S.SUPPLEMENT)
        self.assertIn('No. of backlinks', filtered.snapshot_reason)
        self.assertEqual(self.status(full), S.ACTIVE)
        _, rows = project_inventory(self.project, include_fields=True)
        self.assertEqual(len(rows), 3)
        backlinks = [r for r in rows if any(v['field']['path'] == ['No. of backlinks'] for v in r['field_values'].values())]
        self.assertEqual(len(backlinks), 2)

    def test_more_complete_export_replaces_active_and_absorbs_supplement(self):
        old = self.batch([row('a'), row('b')])
        supplement = self.batch([row('a', BASE + LINKS)])
        complete = self.batch([row('a', BASE + LINKS), row('b', BASE + LINKS), row('c', BASE + LINKS)])
        self.assertEqual(complete.snapshot_status, S.ACTIVE)
        self.assertIn('1 extra URLs', complete.snapshot_reason)
        old.refresh_from_db()
        self.assertEqual((old.snapshot_status, old.superseded_by_id), (S.SUPERSEDED, complete.pk))
        self.assertEqual(self.status(supplement), S.REDUNDANT)

    def test_completer_urls_but_fewer_columns_keeps_old_columns_as_supplement(self):
        old = self.batch([row('a', BASE + LINKS)])
        wider = self.batch([row('a'), row('b')])
        self.assertEqual(wider.snapshot_status, S.ACTIVE)
        self.assertEqual(self.status(old), S.SUPPLEMENT)

    def test_older_measurement_is_historical(self):
        current = self.batch([row('a'), row('b')], day=29)
        older = self.batch([row('a'), row('b'), row('c')], day=1)
        self.assertEqual(older.snapshot_status, S.HISTORICAL)
        self.assertIn('ouder', older.snapshot_reason)
        self.assertEqual(self.status(current), S.ACTIVE)
        _, rows = project_inventory(self.project)
        self.assertEqual(len(rows), 2)

    def test_newer_measurement_replaces_active_even_when_smaller(self):
        old = self.batch([row('a'), row('b'), row('c'), row('d'), row('e')], day=1)
        new = self.batch([row('a')], day=29)
        self.assertEqual(new.snapshot_status, S.ACTIVE)
        self.assertEqual(self.status(old), S.SUPERSEDED)
        self.assertIn('row_drop', {warning['code'] for warning in new.quality_warnings})

    def test_same_moment_with_different_values_is_conflict_until_a_human_chooses(self):
        current = self.batch([row('a'), row('b')])
        other = self.batch([row('a', PR='99')])
        self.assertEqual(other.snapshot_status, S.CONFLICT)
        self.assertIn('https://example.com/a · PR: "1" → "99"', other.snapshot_reason)
        self.assertIn('PR (1×)', other.snapshot_reason)
        _, rows = project_inventory(self.project)
        self.assertNotIn(other.pk, {item['observations']['ahrefs']['batch_id'] for item in rows})
        client = self.client
        client.force_login(self.user)
        detail = client.get(reverse('ui:batch', args=[self.project.pk, other.pk]))
        self.assertContains(detail, 'Maak deze import actief')
        self.assertContains(detail, 'Laat meetellen als aanvulling')
        response = client.post(reverse('ui:batch-resolve', args=[self.project.pk, other.pk]), {'choice': 'active'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.status(other), S.ACTIVE)
        self.assertEqual(self.status(current), S.SUPERSEDED)
        other.refresh_from_db()
        self.assertIn('Handmatig actief gemaakt door snapshots', other.snapshot_reason)

    def test_viewer_cannot_resolve_conflict_and_only_conflicts_can_be_activated(self):
        self.batch([row('a')])
        other = self.batch([row('a', PR='99')])
        Membership.objects.filter(user=self.user).update(role='viewer')
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse('ui:batch-resolve', args=[self.project.pk, other.pk]), {'choice': 'active'}).status_code, 403)
        self.assertEqual(self.status(other), S.CONFLICT)

    def test_gmw_conflict_with_extra_columns_can_be_accepted_as_supplement(self):
        full = self.batch([row('a'), row('b'), row('c')])
        filtered = self.batch([row('a', BASE + LINKS, PR='7'), row('b', BASE + LINKS)])
        self.assertEqual(filtered.snapshot_status, S.CONFLICT)
        self.assertIn('PR (1×)', filtered.snapshot_reason)
        self.assertIn('extra kolommen: No. of backlinks', filtered.snapshot_reason)
        self.client.force_login(self.user)
        self.client.post(reverse('ui:batch-resolve', args=[self.project.pk, filtered.pk]), {'choice': 'supplement'})
        self.assertEqual((self.status(full), self.status(filtered)), (S.ACTIVE, S.SUPPLEMENT))
        _, rows = project_inventory(self.project, include_fields=True)
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(any(v['field']['path'] == ['No. of backlinks'] for v in r['field_values'].values()) for r in rows), 2)
        response = self.client.post(reverse('ui:batch-resolve', args=[self.project.pk, filtered.pk]), {'choice': 'active'}, follow=True)
        self.assertContains(response, 'Alleen een import met een conflict')
        self.assertEqual(self.status(full), S.ACTIVE)

    def test_external_report_reason_is_specific(self):
        batch = self.batch([row('a')], source='sf', report='external_all', record_scope='external')
        self.assertEqual(batch.snapshot_status, S.INDEPENDENT)
        self.assertIn('extern rapport', batch.snapshot_reason)

    def test_tracking_parameters_and_volatile_columns_do_not_create_differences(self):
        self.batch([row('a', **{'Crawl Timestamp': '2026-10-29 10:00'}), row('b')], source='sf', report='internal_all')
        again = self.batch([row('a/?utm_source=x&gclid=1#top', Title='Pagina a', **{'Crawl Timestamp': '2026-10-29 11:00'})], source='sf', report='internal_all')
        self.assertEqual(again.snapshot_status, S.REDUNDANT)

    def test_reports_and_segments_are_separate_datasets(self):
        pages = self.batch([row('a'), row('b')])
        titles = self.batch([row('a')], report='ahrefs_site_audit_missing_titles')
        self.assertEqual((self.status(pages), titles.snapshot_status), (S.ACTIVE, S.ACTIVE))
        indexed = self.batch([row('a')], source='gsc', report='gsc_page_indexing', report_status='indexed', indexing_reason='')
        missing = self.batch([row('b')], source='gsc', report='gsc_page_indexing', report_status='not_indexed', indexing_reason='404')
        self.assertNotEqual(indexed.dataset_key, missing.dataset_key)
        self.assertEqual((self.status(indexed), missing.snapshot_status), (S.ACTIVE, S.ACTIVE))

    def test_time_series_reports_are_independent_and_keep_counting(self):
        first = self.batch([{'url': 'https://example.com/a', 'date': '2026-10-01', 'clicks': 1}], source='gsc', report='search_analytics')
        second = self.batch([{'url': 'https://example.com/a', 'date': '2026-10-08', 'clicks': 2}], source='gsc', report='search_analytics')
        self.assertEqual((self.status(first), second.snapshot_status), (S.INDEPENDENT, S.INDEPENDENT))
        self.assertEqual(ImportBatch.objects.analysable().filter(project=self.project).count(), 2)

    def test_database_allows_only_one_active_import_per_dataset(self):
        active = self.batch([row('a')])
        other = self.batch([row('b')], classify_now=False)
        other.dataset_key, other.snapshot_status = active.dataset_key, S.ACTIVE
        with self.assertRaises(IntegrityError), transaction.atomic():
            other.save()

    def test_foreign_host_share_warns(self):
        batch = self.batch([row('a')], input_rows=10, excluded_rows=6)
        self.assertIn('foreign_hosts', {warning['code'] for warning in batch.quality_warnings})

    def test_unclassified_imports_keep_counting(self):
        batch = self.batch([row('a')], classify_now=False)
        self.assertIsNone(batch.snapshot_status)
        self.assertTrue(batch.counts_in_analyses)
        self.assertEqual(list(ImportBatch.objects.analysable()), [batch])

    def test_summary_scope_stays_out_of_analysable_but_counts_for_exports(self):
        batch = self.batch([{'Issue Name': 'x'}], source='sf', report='issues_overview', record_scope='summary')
        self.assertEqual(batch.snapshot_status, S.INDEPENDENT)
        self.assertFalse(ImportBatch.objects.analysable().exists())
        self.assertTrue(ImportBatch.objects.counted().exists())


class UploadFlowTests(SnapshotTestCase):
    def upload(self, rows, filename):
        import csv, io
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .ahrefs import BASE_COLUMNS
        headers = sorted(BASE_COLUMNS | {'No. of all inlinks', 'First found at'})
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        for path, traffic in rows:
            writer.writerow(dict.fromkeys(headers, '1') | {'URL': f'https://example.com/{path}', 'Organic traffic': traffic,
                                                            'Content type': 'text/html'})
        response = self.client.post(reverse('ui:import', args=[self.project.pk]), {'file': SimpleUploadedFile(filename, stream.getvalue().encode())})
        if '/import/' not in response.url:
            return self.client.get(response.url)
        return self.client.post(response.url, {'action': 'confirm', 'observed_on': '2026-10-29'}, follow=True)

    def test_identical_file_is_refused_and_filtered_upload_is_announced_as_redundant(self):
        self.client.force_login(self.user)
        rows = [('a', '5'), ('b', '3'), ('c', '0'), ('d', '0')]
        first = self.upload(rows, 'full.csv')
        self.assertContains(first, 'Actief')
        again = self.upload(rows, 'full.csv')
        self.assertContains(again, 'Dit bestand is al geïmporteerd')
        self.assertEqual(ImportBatch.objects.count(), 1)
        filtered = self.upload(rows[:2], 'filtered.csv')
        self.assertContains(filtered, 'Redundant')
        self.assertContains(filtered, 'telt niet mee in de analyses')
        self.assertContains(filtered, 'Vermoedelijk een gefilterde export')
        listing = self.client.get(reverse('ui:sources', args=[self.project.pk]))
        self.assertContains(listing, 'snapshot-redundant')
        self.assertContains(listing, 'snapshot-active')
        only = self.client.get(reverse('ui:sources', args=[self.project.pk]), {'snapshot': 'redundant'})
        self.assertEqual(len(only.context['page'].object_list), 1)


class CommandTests(SnapshotTestCase):
    def test_dry_run_reports_without_writing_and_apply_writes(self):
        full = self.batch([row('a'), row('b'), row('zero-c')], classify_now=False)
        filtered = self.batch([row('a'), row('b')], classify_now=False)
        enriched = self.batch([row('a', BASE + LINKS)], classify_now=False)
        out = StringIO()
        call_command('classify_snapshots', '--project', 'GMW', stdout=out)
        report = out.getvalue()
        self.assertIn('DRY-RUN', report)
        self.assertIn('niet geclassificeerd → redundant', report)
        self.assertIn('niet geclassificeerd → supplement', report)
        self.assertIsNone(self.status(filtered))
        call_command('classify_snapshots', '--project', str(self.project.pk), '--apply', stdout=StringIO())
        self.assertEqual((self.status(full), self.status(filtered), self.status(enriched)), (S.ACTIVE, S.REDUNDANT, S.SUPPLEMENT))
        self.assertEqual(ImportBatch.objects.count(), 3)
        # Idempotent: a second run proposes no changes.
        out = StringIO()
        call_command('classify_snapshots', stdout=out)
        self.assertIn('0 import(s) krijgen een andere status', out.getvalue())

    def test_replay_orders_by_measurement_not_upload_time(self):
        newer = self.batch([row('a')], day=29, classify_now=False)
        older = self.batch([row('a'), row('b')], day=1, classify_now=False)
        outcome = plan_project(self.project)
        self.assertEqual((outcome[older.pk]['status'], outcome[newer.pk]['status']), (S.SUPERSEDED, S.ACTIVE))

    def test_unknown_project_is_an_error(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command('classify_snapshots', '--project', 'bestaat-niet', stdout=StringIO())


class URLKeyTests(SimpleTestCase):
    def test_normalization(self):
        self.assertEqual(url_key('https://Example.COM/a/?utm_medium=x&fbclid=y&page_id=3#frag'), 'https://example.com/a?page_id=3')
        self.assertEqual(url_key('https://example.com/a'), url_key('https://example.com/a/'))
        self.assertEqual(url_key('https://example.com:443/'), 'https://example.com/')
        self.assertNotEqual(url_key('https://example.com/?page_id=1'), url_key('https://example.com/?page_id=2'))
        self.assertIsNone(url_key(''))

    def test_dataset_key_defaults(self):
        batch = ImportBatch(source='sf', request_metadata={})
        self.assertEqual(dataset_key(batch), 'sf:internal_all')
