"""Check test-runtime UI -> Redis -> Celery -> analysis -> evidence, then clean up."""
import time
import uuid
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from projects.models import Project, Membership
from sources.models import ImportBatch
from sources.services import publish_records
from scans.models import ScanDocument
from assessments.models import ReprocessingJob, Finding

assert connection.settings_dict['NAME'] == 'sem_test', 'Test environment only'
user = project = batch = None
client = Client(HTTP_HOST='localhost')
try:
    with transaction.atomic():
        user = get_user_model().objects.create_user('assessment-smoke-' + uuid.uuid4().hex)
        project = Project.objects.create(name='Temporary assessment smoke', website='https://example.com/', created_by=user)
        Membership.objects.create(project=project, user=user, role='admin')
        batch = ImportBatch.objects.create(project=project, created_by=user, source='semscan', method='scan', label='Temporary smoke', request_metadata={'scan': 'crawl'})
        html = b'<title>Source</title><meta name="description" content="Description"><h1>Source</h1><a href="/missing" rel="nofollow">Broken example</a>'
        publish_records(batch_id=batch.pk, records=[
            {'locator': 'page:1', 'data': {'url': 'https://example.com/', 'status': 200, 'final_status': 200, 'content_type': 'text/html',
                'measured_at': timezone.now().isoformat(), 'titles': ['Source'], 'meta_descriptions': ['Description'], 'h1': ['Source'], 'outlinks': ['https://example.com/missing']}},
            {'locator': 'page:2', 'data': {'url': 'https://example.com/missing', 'status': 404, 'final_status': 404, 'content_type': 'text/html', 'measured_at': timezone.now().isoformat()}}])
        ScanDocument.objects.create(batch=batch, index=0, url='https://example.com/', status=200, content_type='text/html', fetched_at=timezone.now(), body=html)
        ScanDocument.objects.create(batch=batch, index=1, url='https://example.com/missing', status=404, content_type='text/html', fetched_at=timezone.now(), body=b'Not found')
    client.force_login(user)
    response = client.post(reverse('ui:assessments:reprocess-selection', args=[project.pk]), {'batch': str(batch.pk)})
    assert response.status_code == 302, response.status_code
    job = ReprocessingJob.objects.get(batch=batch)
    for attempt in range(40):
        job.refresh_from_db()
        if job.status in ('complete', 'failed'):
            break
        time.sleep(0.5)
    assert job.status == 'complete', (job.status, job.error)
    finding = Finding.objects.get(result__run=job.run, rule='link_to_404')
    assert finding.details['anchor_text'] == 'Broken example'
    assert finding.evidence.filter(document__isnull=False).count() == 2
    for name, args in [('overview', [project.pk]), ('run', [project.pk, job.run_id]), ('finding', [project.pk, finding.pk])]:
        response = client.get(reverse('ui:assessments:' + name, args=args))
        assert response.status_code == 200, name
    response = client.get(reverse('ui:assessments:export', args=[project.pk, job.run_id]))
    assert response.status_code == 200
    assert b'link_to_404' in b''.join(response.streaming_content)
    print('PASS: runtime UI, Redis/Celery heranalyse, HTML-herextractie, bron- en doelbewijs en export.', flush=True)
finally:
    client.logout()
    if batch:
        ImportBatch.objects.filter(pk=batch.pk).delete()
    if project:
        Project.objects.filter(pk=project.pk).delete()
    if user:
        get_user_model().objects.filter(pk=user.pk).delete()
    print('Temporary smoke-check data removed.', flush=True)
