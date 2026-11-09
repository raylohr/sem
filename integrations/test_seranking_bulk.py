from datetime import date
from unittest.mock import patch
from django.core.management.base import CommandError
from django.test import SimpleTestCase
from integrations.management.commands.import_seranking_projects import history_bounds, missing_periods


class HistoryImportTests(SimpleTestCase):
    def test_all_history_is_chunked_without_overlap_and_existing_coverage_skipped(self):
        periods = list(missing_periods(date(2026, 9, 1), date(2026, 11, 10),
            [(date(2026, 11, 2), date(2026, 11, 9))]))
        self.assertEqual(periods, [(date(2026, 9, 1), date(2026, 10, 1)),
                                  (date(2026, 10, 2), date(2026, 11, 1)),
                                  (date(2026, 11, 10), date(2026, 11, 10))])

    def test_overlapping_prior_imports_are_merged(self):
        covered = [(date(2026, 11, 1), date(2026, 11, 7)), (date(2026, 11, 5), date(2026, 11, 9))]
        self.assertEqual(list(missing_periods(date(2026, 11, 1), date(2026, 11, 10), covered)),
                         [(date(2026, 11, 10), date(2026, 11, 10))])

    @patch('integrations.management.commands.import_seranking_projects.timezone.localdate', return_value=date(2026, 11, 10))
    def test_history_spans_all_engines_and_requires_first_date(self, today):
        self.assertEqual(history_bounds([{'first':'2025-01-01','current':'2026-11-09'},
                                         {'first':'2024-06-01','current':'2026-11-10'}]),
                         (date(2024, 6, 1), date(2026, 11, 10)))
        with self.assertRaises(CommandError):
            history_bounds({'current':'2026-11-10'})


from io import StringIO
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from projects.models import Project, Membership
from integrations.models import SERankingConnection
from sources.models import ImportBatch


@override_settings(SERANKING_API_KEY='test-only')
class BulkImportTests(TestCase):
    @patch('integrations.management.commands.import_seranking_projects.verify_connection')
    @patch('integrations.management.commands.import_seranking_projects.site_audits', return_value=[])
    @patch('integrations.management.commands.import_seranking_projects.ProjectClient')
    @patch('integrations.seranking_tasks.fetch_report')
    def test_command_imports_each_supported_report_and_is_repeatable(self, fetch, client, audits, verify):
        user = get_user_model().objects.create_user('bulk-owner')
        project = Project.objects.create(name='Bulk', website='https://example.test/', created_by=user)
        Membership.objects.create(project=project,user=user,role='admin')
        SERankingConnection.objects.create(project=project,owner=user,site_id=123,site_name='example.test')
        client.return_value.get.return_value = {'first':'2026-11-07','current':'2026-11-10'}
        fetch.return_value = ([{'locator':'1','data':{'url':'https://example.test/', 'date':'2026-11-10'}}], [])
        call_command('import_seranking_projects', stdout=StringIO(), stderr=StringIO())
        first_count = ImportBatch.objects.count()
        self.assertEqual(first_count, 8)
        self.assertEqual(ImportBatch.objects.filter(status='complete').count(), 8)
        ranking = ImportBatch.objects.get(request_metadata__type='rankings')
        self.assertEqual(ranking.period_start,date(2026,11,7))
        self.assertEqual(ranking.period_end,date(2026,11,10))
        call_command('import_seranking_projects', stdout=StringIO(), stderr=StringIO())
        self.assertEqual(ImportBatch.objects.count(), first_count)
        self.assertEqual(fetch.call_count, 8)
