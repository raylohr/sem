from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.template import Template, Context
from django.template.loader import render_to_string
from django.test import SimpleTestCase


class NumberDisplayTests(SimpleTestCase):
    def test_counts_and_decimals_use_dutch_grouping(self):
        html = Template('{{ count }} | {{ metric }}').render(Context({'count': 100000, 'metric': Decimal('12345.67')}))
        self.assertEqual(html, '100.000 | 12.345,67')

    def test_large_page_numbers_remain_usable_in_links(self):
        page = Paginator(range(1005), 1).page(1000)
        html = render_to_string('accounts/pagination.html', {'page': page, 'query': ''})
        self.assertIn('Pagina 1.000 van 1.005', html)
        self.assertIn('page=1001', html)
        self.assertNotIn('page=1.001', html)

    def test_accordion_identifiers_are_not_grouped(self):
        project = SimpleNamespace(id=UUID('11111111-1111-1111-1111-111111111111'), name='Project', website='https://example.com')
        batch = SimpleNamespace(id=UUID('22222222-2222-2222-2222-222222222222'), label='Import', status='complete', record_count=100000, request_metadata={}, warnings=[])
        record = SimpleNamespace(id=123456, locator='row:123456', data={'count': 100000})
        html = render_to_string('ui/batch.html', {'project':project,'batch':batch,'records':[record]})
        self.assertIn('id="record-123456"', html)
        self.assertIn('data-bs-target="#record-123456"', html)
        self.assertNotIn('record-123.456', html)
        self.assertIn('100.000', html)

    def test_json_numbers_remain_numeric(self):
        self.assertEqual(JsonResponse({'count':100000}).content, b'{"count": 100000}')
