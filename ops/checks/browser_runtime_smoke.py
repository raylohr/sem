"""Test-only live example.com -> Redis/Celery -> isolated browser -> DB/UI; cleans up."""
import time
import uuid
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from projects.models import Project, Membership
from sources.models import ImportBatch
from sources.services import publish_records
from scans.models import ScanTarget
from scans.network import SafeClient
from scans.extraction import extract_page
from browser_scans.forms import BrowserForm
from browser_scans.models import BrowserRun
from browser_scans.services import create_run
from browser_scans.views import enqueue

assert connection.settings_dict['NAME']=='sem_test', 'Test environment only'
user=project=source=run=None
client=Client(HTTP_HOST='localhost')
try:
    user=get_user_model().objects.create_user('browser-smoke-'+uuid.uuid4().hex)
    project=Project.objects.create(name='Temporary browser smoke',website='https://example.com/',created_by=user)
    Membership.objects.create(project=project,user=user,role='admin')
    target=ScanTarget.objects.create(project=project,created_by=user,name='Public example',url='https://example.com/',allowed_hosts=['example.com'])
    transport=SafeClient(['example.com'],check_access=lambda:None,duration=30,respect_robots=False)
    try:
        result=transport.fetch('https://example.com/')
        fields,_=extract_page(result)
    finally:
        transport.close()
    source=ImportBatch.objects.create(project=project,created_by=user,scan_target=target,source='semscan',method='scan',label='Temporary example.com HTTP',request_metadata={'scan':'crawl','url':target.url,'allowed_hosts':['example.com'],'respect_robots':False})
    publish_records(batch_id=source.pk,records=[{'locator':'page:0','data':{'url':target.url,'final_url':result.url,'status':result.status,'final_status':result.status,'content_type':result.headers.get('content-type',''),'measured_at':timezone.now().isoformat(),**fields}}])
    client.force_login(user)
    response=client.post(reverse('ui:browser:start',args=[project.pk,source.pk]),{'max_pages':1,'timeout_seconds':20,'requests_per_second':3})
    assert response.status_code==302,response.status_code
    run=BrowserRun.objects.get(source_batch=source)
    for iteration in range(110):
        run.batch.refresh_from_db()
        if run.batch.status in ('complete','incomplete','failed'):
            break
        time.sleep(0.5)
    assert run.batch.status in ('complete','incomplete'),(run.batch.status,run.batch.error)
    visit=run.visits.get()
    assert visit.http_status==200,(visit.status,visit.errors)
    assert visit.artifacts.count()==3,(visit.status,visit.errors)
    assert visit.source_record_id is not None
    from site_intelligence.models import EnrichmentRun
    for iteration in range(110):
        intelligence=EnrichmentRun.objects.filter(batch=run.batch).first()
        if intelligence and intelligence.status in ('complete','incomplete','failed'):break
        time.sleep(.5)
    assert intelligence and intelligence.status in ('complete','incomplete'), (intelligence.status,intelligence.error) if intelligence else 'missing enrichment'
    assert intelligence.domains.exists()
    assert run.batch.analysis_runs.count()==3
    for name,args in [('run',[project.pk,intelligence.pk]),('overview',[project.pk]),('registry',[project.pk])]:
        assert client.get(reverse('ui:intelligence:'+name,args=args)).status_code==200,name
    assert run.batch.page_measurements.count()==1
    for name,args in [('run',[project.pk,run.pk]),('visit',[project.pk,visit.pk])]:
        assert client.get(reverse('ui:browser:'+name,args=args)).status_code==200,name
    assert client.get(reverse('ui:batch',args=[project.pk,run.batch_id])).status_code==200
    record=run.batch.url_observations.get()
    assert client.get(reverse('ui:catalog:detail',args=[project.pk,record.url_id])).status_code==200
    for artifact in visit.artifacts.all():
        response=client.get(reverse('ui:browser:artifact',args=[project.pk,artifact.pk]))
        assert response.status_code==200
        assert len(response.content)==artifact.size
    print('PASS: live public example.com, browser queue, native isolated Chromium, three artifacts, three analyses, DNS/provider enrichment, registry, URL dossier and evidence downloads.',flush=True)
    print('Visit:',visit.status,'resources:',visit.resources.count(),'axe issues:',visit.accessibility_issues.count(),'lab metrics:',sorted(visit.metrics),flush=True)
finally:
    client.logout()
    if project:
        ImportBatch.objects.filter(project=project).delete()
        ScanTarget.objects.filter(project=project).delete()
        Project.objects.filter(pk=project.pk).delete()
    if user:
        get_user_model().objects.filter(pk=user.pk).delete()
    print('Temporary runtime data removed.',flush=True)
