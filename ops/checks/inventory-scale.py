"""Run only in a disposable PostgreSQL DB using config.test_settings.

Example: python ops/checks/inventory-scale.py (inside the isolated test container).
Never loads or copies production data.
"""
import os
import sys
import time
import resource
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.test_settings'
import django
django.setup()
from django.conf import settings
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import Client
from django.urls import reverse
from projects.models import Project, Membership
from sources.models import ImportBatch, SourceRecord
from sources.inventory_index import rebuild

assert settings.DATABASES['default']['HOST'] == 'database'
assert settings.DATABASES['default']['NAME'] == 'sem_checks'
assert settings.DATABASES['default']['USER'] == 'sem_checks'
call_command('migrate', verbosity=0)
user = get_user_model().objects.create_user('scale-only')
project = Project.objects.create(name='Disposable 100k benchmark', domain='example.com', website='https://example.com', created_by=user)
Membership.objects.create(project=project, user=user, role='viewer')
started = time.monotonic()
for source in ('sf', 'ahrefs'):
    batch = ImportBatch.objects.create(project=project, source=source, method='file', created_by=user, status='complete', label=source,
        request_metadata={'type': 'internal_all' if source == 'sf' else 'ahrefs_site_audit_pages'})
    for offset in range(0, 100000, 1000):
        rows = []
        for index in range(offset, offset+1000):
            address = f'https://example.com/path/{index:06}'
            data = {'Address': address, 'Title 1': 'Title '+str(index), 'Status Code': '404' if index % 100 == 0 else '200', 'Content Type': 'text/html', 'Crawl Timestamp': '2026-11-01'} if source == 'sf' else {
                'URL': address, 'Title': 'Ahrefs '+str(index), 'Organic traffic': str(index % 1000),
                'No. of backlinks': str(index % 300), 'No. of keywords with Top 3 position': str(index % 3),
                'No. of keywords with Top 10 position': str(index % 10), 'Content type': 'text/html', 'Category': 'A' if index % 2 else 'B'}
            rows.append(SourceRecord(batch=batch, locator=str(index), data=data))
        SourceRecord.objects.bulk_create(rows, batch_size=1000)
print(f'fixture_seconds={time.monotonic()-started:.2f}', flush=True)
started = time.monotonic()
rebuild(project.pk)
print(f'rebuild_seconds={time.monotonic()-started:.2f} builder_peak_mib={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024:.1f}', flush=True)
# Requests must never fall back to synchronous rebuilding.
settings.INVENTORY_BUILD_INLINE = False

def request(number):
    close_old_connections()
    try:
        client = Client()
        client.force_login(user)
        start = time.monotonic()
        timings = []
        def timed(execute, sql, params, many, context):
            tick = time.monotonic()
            try: return execute(sql, params, many, context)
            finally:
                elapsed = time.monotonic()-tick
                if elapsed > 0.2: timings.append((round(elapsed, 3), sql))
        with connection.execute_wrapper(timed):
            response = client.get(reverse('ui:project', args=[project.pk]), {'url_page': str(number), 'sort': 'ahrefs:desc', 'lo_ahrefs': '100'})
        if os.environ.get('SEM_SCALE_QUERY_PROFILE'):
            print(f'page={number} slow_queries={timings}', flush=True)
        assert response.status_code == 200, response.status_code
        assert len(response.content) > 1000
        return round(time.monotonic()-start, 3), len(response.content)
    finally:
        connection.close()

with ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(request, (1, 500)))
print(f'concurrent_requests={results} process_peak_mib={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024:.1f}', flush=True)

# Also exercise the complete chart alongside a table page. The chart deliberately
# retains all eligible points; only its source field hydration is bounded.
def chart(_):
    close_old_connections()
    try:
        client = Client()
        client.force_login(user)
        start = time.monotonic()
        response = client.get(reverse('ui:performance', args=[project.pk]))
        assert response.status_code == 200, response.status_code
        assert b'performance-points' in response.content
        return round(time.monotonic()-start, 3), len(response.content)
    finally:
        connection.close()

with ThreadPoolExecutor(max_workers=2) as pool:
    page = pool.submit(request, 1)
    points = pool.submit(chart, None)
    print(f'table_and_chart={[page.result(), points.result()]} peak_mib={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024:.1f}', flush=True)
