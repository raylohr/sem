"""sem_test only: synthetic page checkpoint -> real worker, five real asset GETs, dossier/UI."""
import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from projects.models import Project,Membership
from sources.models import ImportBatch
from scans.models import ScanTarget,HostPolicy,ResourceMeasurement
from scans.work import ScanWork,work_paths
from scans.extraction import extract_page
from scans.tasks import process_scan
from reporting.services import create_dossier

assert connection.settings_dict['NAME']=='sem_test'
user=project=batch=client=None
try:
    user=get_user_model().objects.create_user('resource-smoke-'+uuid.uuid4().hex)
    project=Project.objects.create(name='DEMO · synthetic pages / actual asset requests',website='https://example.com/',created_by=user)
    Membership.objects.create(project=project,user=user,role='admin')
    target=ScanTarget.objects.create(project=project,name='R4 controlled worker smoke',url='https://example.com/',allowed_hosts=['example.com'],created_by=user)
    HostPolicy.objects.create(project=project,host='www.vandersteenhoven.nl',resources='allowed')
    config={'scan':'crawl','url':target.url,'allowed_hosts':['example.com'],'resource_hosts':['www.vandersteenhoven.nl'],
            'max_urls':10,'max_depth':2,'duration':300,'resource_version':'resources-v1','check_resources':True,'check_external_links':False,
            'resource_max_targets':5,'resource_max_bytes':10*1024**2,'respect_robots':False,'requests_per_second':1}
    batch=ImportBatch.objects.create(project=project,created_by=user,scan_target=target,source='semscan',method='scan',label='DEMO R4 controlled asset measurements',request_metadata=config)
    host='https://www.vandersteenhoven.nl'
    scripts=['/_sdk/'+name+'_sdk.js' for name in ('telemetry','editing','data','resizing')]
    image='/wp-content/uploads/2025/05/17285788191311-4.jpg'
    now=timezone.now()
    with ScanWork(batch.pk) as work:
        for index,path in enumerate(('first','second')):
            url='https://example.com/'+path
            html='<html><head><title>Synthetic fixture '+path+'</title></head><body><h1>Fixture</h1>'+''.join('<script src="'+host+src+'"></script>' for src in scripts)+'<img src="'+host+image+'" alt="Synthetic usage"></body></html>'
            fields,_=extract_page(SimpleNamespace(url=url,status=200,headers={'content-type':'text/html'},body=html.encode()))
            work.records.append({'locator':'page:'+str(index),'data':{'url':url,'final_url':url,'status':200,'final_status':200,'content_type':'text/html','measured_at':now.isoformat(),'measurement_kind':'synthetic page fixture; asset requests are real',**fields}})
            work.documents.append({'url':url,'status':200,'content_type':'text/html','fetched_at':now,'body':html.encode()})
        work.set('page_phase_done',True);work.set('page_phase_warnings',[])
    process_scan.delay(str(batch.pk))
    for iteration in range(110):
        batch.refresh_from_db()
        if batch.status in ('complete','incomplete','failed'):break
        time.sleep(.5)
    assert batch.status=='complete',(batch.status,batch.error)
    assert batch.record_count==2 and batch.resource_measurements.count()==5
    assert batch.scan_documents.count()==2,'Asset bodies must not become page documents'
    assert batch.resource_measurements.filter(http_status=404).count()==4
    assert batch.resource_measurements.filter(http_status=200).count()==1
    run=batch.analysis_runs.latest('started_at')
    assert run.definition.version=='7' and run.results.count()==2
    assert run.summary['resource_http_error']==4 and run.summary['resource_image_large']==1
    assert run.coverage['resource_checks']['resource_http_error']['issue']==4
    resource=batch.resource_measurements.filter(http_status=404).first()
    assert resource.references.count()==2
    client=Client(HTTP_HOST='localhost');client.force_login(user)
    for name,args in [('projects:scans:resources',[project.pk,batch.pk]),('projects:scans:resource',[project.pk,batch.pk,resource.pk]),('ui:assessments:run',[project.pk,run.pk])]:
        assert client.get(reverse(name,args=args)).status_code==200,name
    export=client.get(reverse('projects:scans:resources',args=[project.pk,batch.pk])+'?download=1')
    output=b''.join(export.streaming_content).decode()
    assert 'wire_bytes' in output and 'reference' in output and 'set-cookie' not in output.lower()
    dossier=create_dossier(project,user,batch.pk,title='DEMO R4 report')
    for iteration in range(100):
        dossier.refresh_from_db()
        if dossier.status in ('complete','failed'):break
        time.sleep(.5)
    assert dossier.status=='complete',(dossier.status,dossier.error)
    assert dossier.pages.count()==2,'Resource targets must not inflate the page denominator'
    assert dossier.groups.filter(rule='resource_http_error').count()==4
    assert all(group.affected_urls==2 for group in dossier.groups.filter(rule='resource_http_error'))
    assert client.get(reverse('ui:reports:prospect',args=[project.pk,dossier.pk])).status_code==200
    print('PASS: real asset queue, four SDK-404s and one measured image, two synthetic pages, shared-source evidence, separate budgets/page counts, private UI, report and export.',flush=True)
finally:
    if client:client.logout()
    if batch:
        ImportBatch.objects.filter(pk=batch.pk).delete()
        for path in work_paths(batch.pk):(Path(settings.MEDIA_ROOT)/path).unlink(missing_ok=True)
    if project:
        ScanTarget.objects.filter(project=project).delete()
        Project.objects.filter(pk=project.pk).delete()
    if user:get_user_model().objects.filter(pk=user.pk).delete()
    print('Temporary runtime data removed.',flush=True)
