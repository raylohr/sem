"""sem_test only: stored HTML -> real reanalysis queue -> dossier, UI and export."""
import time
import uuid
from types import SimpleNamespace
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from projects.models import Project, Membership
from sources.models import ImportBatch
from sources.services import publish_records
from scans.extraction import extract_page
from scans.models import ScanDocument
from assessments.models import ReprocessingJob, PageMeasurement, Finding
from reporting.services import create_dossier

assert connection.settings_dict['NAME']=='sem_test'
user=project=client=None
try:
    user=get_user_model().objects.create_user('content-smoke-'+uuid.uuid4().hex)
    project=Project.objects.create(name='DEMO · R3 content smoke',website='https://example.com/',created_by=user)
    Membership.objects.create(project=project,user=user,role='admin')
    batch=ImportBatch.objects.create(project=project,created_by=user,source='semscan',method='scan',label='DEMO R3',request_metadata={'scan':'crawl','url':'https://example.com/first'})
    now=timezone.now();records=[]
    for index,path in enumerate(('first','second')):
        url='https://example.com/'+path
        html=('<html lang="nl"><head><title>Demonstratie van een beschrijvende paginatitel '+path+'</title><link rel="canonical" href="/'+path+'"></head>'
              '<body><header><h2>Taalmenu</h2></header><main><h1>Pagina '+path+'</h1>'
              '<a href="/named" aria-label="Contact"><img src="/icon" alt="" role="none"></a>'
              '<a href="/unnamed"><img src="/photo" alt=""></a><h3>Detail</h3></main></body></html>')
        fields,_=extract_page(SimpleNamespace(url=url,status=200,headers={'content-type':'text/html'},body=html.encode()))
        # Simulate older retained records; the worker must recover new facts from the document.
        for key in ('content_audit','heading_evidence','link_name_evidence','social_metadata'):fields.pop(key,None)
        fields['extractor_version']='html-v5'
        records.append({'locator':'page:'+str(index),'data':{'url':url,'final_url':url,'status':200,'final_status':200,'content_type':'text/html','measured_at':now.isoformat(),**fields}})
        ScanDocument.objects.create(batch=batch,index=index,url=url,status=200,content_type='text/html',fetched_at=now,body=html.encode())
    publish_records(batch_id=batch.pk,records=records,derive_assessment=False)
    original=list(batch.records.order_by('id').values_list('data',flat=True))
    client=Client(HTTP_HOST='localhost');client.force_login(user)
    response=client.post(reverse('ui:assessments:reprocess',args=[project.pk,batch.pk]))
    assert response.status_code==302
    job=ReprocessingJob.objects.get(batch=batch)
    for iteration in range(100):
        job.refresh_from_db()
        if job.status in ('complete','failed'):break
        time.sleep(.5)
    assert job.status=='complete',(job.status,job.error)
    run=job.run
    assert run.definition.version=='7'
    assert list(batch.records.order_by('id').values_list('data',flat=True))==original
    assert Finding.objects.filter(result__run=run,rule='image_link_name_missing').count()==2
    assert Finding.objects.filter(result__run=run,rule='social_og_missing').count()==2
    assert not Finding.objects.filter(result__run=run,rule='image_empty_alt_review').exists()
    page=PageMeasurement.objects.filter(batch=batch).first()
    body=client.get(reverse('ui:assessments:page',args=[project.pk,page.pk])).content.decode()
    assert 'Naam uit HTML: Contact' in body and 'Open Graph en X Card-velden' in body
    finding=Finding.objects.filter(result__run=run,rule='image_link_name_missing').first()
    response=client.get(reverse('ui:assessments:finding',args=[project.pk,finding.pk]))
    assert response.status_code==200 and b'Gemeten waarden en locaties' in response.content
    export=client.get(reverse('ui:assessments:export',args=[project.pk,run.pk]))
    assert 'social_og_missing' in b''.join(export.streaming_content).decode()
    dossier=create_dossier(project,user,batch.pk,title='DEMO R3-dossier')
    for iteration in range(100):
        dossier.refresh_from_db()
        if dossier.status in ('complete','failed'):break
        time.sleep(.5)
    assert dossier.status=='complete',(dossier.status,dossier.error)
    group=dossier.groups.get(rule='image_link_name_missing')
    assert group.affected_urls==2 and group.occurrences==2
    response=client.get(reverse('ui:reports:group',args=[project.pk,group.pk]))
    assert response.status_code==200
    assert client.get(reverse('ui:reports:prospect',args=[project.pk,dossier.pk])).status_code==200
    export=client.get(reverse('ui:reports:export',args=[project.pk,dossier.pk]))
    assert 'image_link_name_missing' in b''.join(export.streaming_content).decode()
    print('PASS: R3 real reanalysis queue, document recovery, original records preserved, names and source evidence, private UI, report grouping and exports.',flush=True)
finally:
    if client:client.logout()
    if project:
        ImportBatch.objects.filter(project=project).delete()
        Project.objects.filter(pk=project.pk).delete()
    if user:get_user_model().objects.filter(pk=user.pk).delete()
    print('Temporary runtime data removed.',flush=True)
