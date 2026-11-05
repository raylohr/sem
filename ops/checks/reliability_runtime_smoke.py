"""sem_test only: real task queue, safe HTTP transport, attempt storage and UI."""
import time
import uuid
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.urls import reverse
from projects.models import Project,Membership
from sources.models import ImportBatch
from scans.models import ScanTarget

assert connection.settings_dict['NAME']=='sem_test'
user=project=target=batch=client=None
try:
    user=get_user_model().objects.create_user('reliability-smoke-'+uuid.uuid4().hex)
    project=Project.objects.create(name='DEMO · downloadbetrouwbaarheid',website='https://example.com/',created_by=user)
    Membership.objects.create(project=project,user=user,role='admin')
    target=ScanTarget.objects.create(project=project,created_by=user,name='DEMO example.com',url=project.website,allowed_hosts=['example.com','www.example.com'])
    batch=ImportBatch.objects.create(project=project,created_by=user,scan_target=target,source='semscan',method='scan',label='DEMO · echte queue/HTTP-proef',request_metadata={
        'scan':'crawl','url':project.website,'allowed_hosts':target.allowed_hosts,'max_urls':1,'max_depth':0,'duration':120,
        'max_transfer':25*1024*1024,'respect_robots':False,'robots_mode':'ignore','discover_sitemaps':False,
        'concurrency':1,'requests_per_second':1,'connect_timeout':10,'read_timeout':30,'max_attempts':3,'reliability_version':'page-retry-v1'})
    from scans.tasks import enqueue_scan
    enqueue_scan(batch.pk)
    for _ in range(300):
        batch.refresh_from_db()
        if batch.status in ('complete','incomplete','failed'):break
        time.sleep(.5)
    assert batch.status=='complete',(batch.status,batch.error)
    assert batch.records.count()==1 and batch.crawl_attempts.count()>=1
    attempt=batch.crawl_attempts.order_by('-ordinal').first()
    assert attempt.http_status==200 and attempt.details['read_timeout']==30
    assert batch.crawl_url_coverage.get().outcome=='success'
    client=Client(HTTP_HOST='localhost');client.force_login(user)
    for name in ('coverage','attempts'):
        url=reverse('projects:scans:'+name,args=[project.pk,batch.pk])
        assert client.get(url).status_code==200
        response=client.get(url+'?download=1')
        assert response.status_code==200 and b'https://example.com/' in b''.join(response.streaming_content)
    response=client.get(reverse('ui:scans',args=[project.pk]))
    assert response.status_code==200 and b'name="read_timeout"' in response.content and b'name="max_attempts"' in response.content
    print('PASS: real queue + safe HTTP + configured timeouts + attempt ledger + URL inventory + authenticated UI/CSV.',flush=True)
finally:
    if client:client.logout()
    if batch:ImportBatch.objects.filter(pk=batch.pk).delete()
    if target:ScanTarget.objects.filter(pk=target.pk).delete()
    if project:Project.objects.filter(pk=project.pk).delete()
    if user:get_user_model().objects.filter(pk=user.pk).delete()
    print('Temporary reliability smoke data removed.',flush=True)
