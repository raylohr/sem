"""sem_test only: actual page scan -> automatic browser worker -> private UI and dossier."""
import time,uuid
from django.db import connection
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from projects.models import Project,Membership
from scans.models import ScanTarget
from scans.tasks import process_scan
from sources.models import ImportBatch
from browser_scans.models import BrowserRun
from reporting.services import create_dossier

assert connection.settings_dict['NAME']=='sem_test'
user=project=client=batch=None
try:
    user=get_user_model().objects.create_user('r5-runtime-'+uuid.uuid4().hex)
    project=Project.objects.create(name='DEMO R5 actual workflow',website='https://example.com/',created_by=user)
    Membership.objects.create(project=project,user=user,role='admin')
    target=ScanTarget.objects.create(project=project,created_by=user,name='R5 example baseline',url='https://example.com/',allowed_hosts=['example.com'])
    config={'scan':'crawl','url':target.url,'allowed_hosts':['example.com'],'max_urls':1,'max_depth':0,'duration':180,'max_transfer':25*1024**2,
        'connect_timeout':10,'read_timeout':30,'respect_robots':False,'requests_per_second':1,'concurrency':1,
        'browser_phase':True,'browser_max_pages':1,'browser_timeout_seconds':20}
    batch=ImportBatch.objects.create(project=project,created_by=user,scan_target=target,source='semscan',method='scan',label='DEMO R5 workflow',request_metadata=config)
    process_scan.delay(str(batch.pk))
    run=None
    for iteration in range(110):
        batch.refresh_from_db();run=BrowserRun.objects.filter(source_batch=batch).select_related('batch').first()
        if batch.status=='failed' or run and run.batch.status in ('complete','incomplete','failed'):break
        time.sleep(.5)
    assert batch.status=='complete',(batch.status,batch.error)
    assert run and run.batch.status in ('complete','incomplete'),(run.batch.status if run else None,run.batch.error if run else None)
    visit=run.visits.get()
    assert visit.artifacts.filter(kind='rendered_dom').exists() and visit.artifacts.filter(kind='screenshot').exists()
    assert visit.metrics['title_width']['status']=='measured',visit.metrics
    assert batch.records.count()==1 and batch.scan_documents.count()>=1
    assert run.config['automatic_from_crawl'] and not run.config['privacy_enabled']
    client=Client(HTTP_HOST='localhost');client.force_login(user)
    for name,args in [('ui:batch',[project.pk,batch.pk]),('ui:browser:run',[project.pk,run.pk]),('ui:browser:visit',[project.pk,visit.pk])]:
        response=client.get(reverse(name,args=args));assert response.status_code==200,(name,response.status_code)
    assert 'Oorspronkelijke HTML-crawl' in client.get(reverse('ui:browser:visit',args=[project.pk,visit.pk])).content.decode()
    browser_export=client.get(reverse('ui:browser:export',args=[project.pk,run.pk]))
    assert 'browser_visit' in b''.join(browser_export.streaming_content).decode()
    original=list(batch.records.values_list('data',flat=True))
    dossier=create_dossier(project,user,batch.pk,run.batch_id,title='DEMO R5 integrated report')
    for iteration in range(50):
        dossier.refresh_from_db()
        if dossier.status in ('complete','failed'):break
        time.sleep(.5)
    assert dossier.status=='complete',(dossier.status,dossier.error)
    assert list(batch.records.values_list('data',flat=True))==original
    assert dossier.coverage['browser_urls']==1
    export=client.get(reverse('ui:reports:export',args=[project.pk,dossier.pk]))
    assert 'browser_title_width' in b''.join(export.streaming_content).decode()
    print('PASS: real page queue -> automatic baseline browser queue, artifacts, title model, preserved source, private UI, integrated report and export.',flush=True)
finally:
    if client:client.logout()
    if project:
        ImportBatch.objects.filter(project=project).delete()
        ScanTarget.objects.filter(project=project).delete()
        Project.objects.filter(pk=project.pk).delete()
    if user:get_user_model().objects.filter(pk=user.pk).delete()
    print('Temporary R5 runtime data removed.',flush=True)
