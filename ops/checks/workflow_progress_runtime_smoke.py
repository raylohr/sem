"""sem_test only: saved settings -> actual public example.com crawl -> browser workers.
Uses a one-page budget, one external link set and temporary project; never touches production.
"""
import json,time,uuid
from django.db import connection
from django.test import Client
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.urls import reverse
from projects.models import Project,Membership
from sources.models import ImportBatch
from scans.models import ScanTarget
from scans.activity import paths as activity_paths
from scans.work import work_paths

assert connection.settings_dict['NAME']=='sem_test'
user=project=client=None;ids=[]
try:
    user=get_user_model().objects.create_user('workflow-runtime-'+uuid.uuid4().hex)
    project=Project.objects.create(name='DEMO live workflow',website='https://example.com/',created_by=user)
    Membership.objects.create(project=project,user=user,role='admin')
    target=ScanTarget.objects.create(project=project,created_by=user,name='Runtime',url=project.website,allowed_hosts=['example.com'])
    client=Client(HTTP_HOST='localhost');client.force_login(user)
    config={'kind':'crawl','max_urls':1,'max_depth':0,'duration':180,'respect_robots':False,'robots_mode':'ignore','connect_timeout':10,'read_timeout':15,'requests_per_second':2,'concurrency':1,'check_resources':True,'browser_phase':True,'browser_max_pages':1,'browser_timeout_seconds':30}
    config.pop('respect_robots')
    saved=client.post(reverse('projects:scans:settings',args=[project.pk,target.pk]),json.dumps(config),content_type='application/json')
    assert saved.status_code==200,saved.content
    started=client.post(reverse('projects:scans:start',args=[project.pk,target.pk]),json.dumps({'kind':'crawl'}),content_type='application/json')
    assert started.status_code==202,started.content
    batch=ImportBatch.objects.get(pk=started.json()['id']);ids.append(batch.pk)
    assert batch.request_metadata['max_urls']==1 and batch.request_metadata['browser_phase']
    url=reverse('projects:scans:progress',args=[project.pk,batch.pk]);observed=set();browser_followed=False
    from browser_scans.models import BrowserRun
    run=None
    for _ in range(150):
        response=client.get(url);assert response.status_code==200
        state=response.json();workflow=state['workflow']
        if workflow['current_phase']:observed.add(workflow['current_phase'])
        batch.refresh_from_db()
        run=BrowserRun.objects.filter(source_batch=batch).select_related('batch').first()
        if batch.status in ('failed','cancelled'):raise AssertionError((batch.status,batch.error))
        if batch.status in ('complete','incomplete') and workflow['active']:browser_followed=True
        if run and run.batch.status in ('complete','incomplete','failed'):break
        time.sleep(.5)
    assert run and run.batch.status in ('complete','incomplete'),(run.batch.status if run else None)
    ids.append(run.batch_id)
    state=client.get(url).json()['workflow']
    phases={p['name']:p for p in state['phases']}
    assert phases['pages']['status']=='complete' and phases['pages']['done']==1,phases
    assert phases['files']['status']=='complete' and phases['external']['status']=='complete',phases
    assert phases['analysis']['status']=='complete' and phases['browser']['done']==1,phases
    measured_phases={row['phase'] for row in state['totals']}
    assert {'pages','browser'}<=measured_phases,state['totals']
    for name in ('files','external'):
        if phases[name]['done']:assert name in measured_phases,state['totals']
    assert browser_followed,observed
    target.refresh_from_db();assert target.scan_settings['crawl']['max_urls']==1
    assert client.get(reverse('ui:scans',args=[project.pk])).status_code==200
    assert client.get(reverse('ui:batch',args=[project.pk,batch.pk])).status_code==200
    print('PASS: real workers; saved configuration reused, pages/files/external/analysis phases completed, measured HTTP events retained after publication, root follows browser and UI renders.',flush=True)
finally:
    if client:client.logout()
    if project:
        ids=list(set(ids)|set(project.import_batches.values_list('id',flat=True)))
        ImportBatch.objects.filter(project=project).delete()
        ScanTarget.objects.filter(project=project).delete()
        Project.objects.filter(pk=project.pk).delete()
        for identifier in ids:
            for path in activity_paths(identifier)+work_paths(identifier):default_storage.delete(path)
    if user:get_user_model().objects.filter(pk=user.pk).delete()
    print('Temporary workflow runtime fixtures removed.',flush=True)
