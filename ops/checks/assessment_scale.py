"""Synthetic assessment scale check, sem_test migration role only; rolls back."""
import json
import os
import resource
import time
from django.db import connection, transaction
from accounts.models import User
from projects.models import Project, Membership
from sources.models import ImportBatch
from sources.services import publish_records
from assessments.models import PageMeasurement, LinkObservation, Finding

assert connection.settings_dict['NAME'] == 'sem_test'
assert connection.settings_dict['USER'] == 'sem_test_migrate'
total = int(os.environ.get('ASSESSMENT_SCALE_URLS', '10000'))
assert 100 <= total <= 100000
started = time.monotonic()
queries = 0


def count_query(execute, sql, params, many, context):
    global queries
    queries += 1
    return execute(sql, params, many, context)


class RollbackCheck(Exception):
    pass


try:
    with transaction.atomic(), connection.execute_wrapper(count_query):
        user = User.objects.create_user('assessment-scale')
        project = Project.objects.create(name='Synthetic assessment scale', website='https://example.com/', created_by=user)
        Membership.objects.create(project=project, user=user, role='admin')
        batch = ImportBatch.objects.create(project=project, source='semscan', method='scan', label='Synthetic assessment',
            created_by=user, request_metadata={'scan': 'crawl'})
        def records():
            for index in range(total):
                yield {'locator': f'page:{index}', 'data': {'url': f'https://example.com/{index}',
                    'status': 404 if index == total - 1 else 200, 'final_status': 404 if index == total - 1 else 200,
                    'content_type': 'text/html', 'titles': [f'Page {index}'], 'meta_descriptions': [f'Description {index}'],
                    'h1': [f'Heading {index}'], 'text_hash': f'{index:064x}', 'links_truncated': False,
                    'link_details': [{'url': f'https://example.com/{(index + offset) % total}', 'anchor_text': f'Link {offset}'} for offset in range(1, 11)]}}
        publish_records(batch_id=batch.pk, records=records())
        assert PageMeasurement.objects.filter(batch=batch).count() == total
        assert LinkObservation.objects.filter(source__batch=batch).count() == total * 10
        assert Finding.objects.filter(result__run__batch=batch, rule='link_to_404').count() == 10
        run = batch.analysis_runs.get()
        assert run.results.count() == total
        print(json.dumps({'pages': total, 'links': total * 10, 'queries': queries,
            'elapsed_seconds': round(time.monotonic() - started, 2),
            'peak_rss_mib': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)}, indent=2), flush=True)
        raise RollbackCheck
except RollbackCheck:
    print('PASS: synthetic records rolled back; no external requests.', flush=True)
