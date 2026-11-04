"""sem_test only: synthetic observations -> real queue, dossier, actions and UI."""
import json
import os
import time
import uuid
from pathlib import Path
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from projects.models import Project,Membership
from sources.models import ImportBatch,SourceRecord
from assessments.models import AnalysisDefinition,AnalysisRun,AnalysisResult,Finding
from reporting.catalog import EXPECTED
from reporting.models import Dossier
from reporting.services import create_dossier
from reporting.actions import create_action

assert connection.settings_dict['NAME']=='sem_test'
preview=os.environ.get('REPORT_PREVIEW_DIR','')
user=project=client=None
retain=False
try:
    user=get_user_model().objects.create_user('report-smoke-'+uuid.uuid4().hex)
    project=Project.objects.create(name='DEMO · synthetische onderzoeksgegevens',website='https://example.com/',created_by=user)
    Membership.objects.create(project=project,user=user,role='admin')
    batch=ImportBatch.objects.create(project=project,created_by=user,source='semscan',method='scan',status='complete',label='DEMO webcrawl',record_count=3,request_metadata={'scan':'crawl'})
    definition,_=AnalysisDefinition.objects.get_or_create(key='crawl-basics',version='3')
    run=AnalysisRun.objects.create(batch=batch,definition=definition,status='complete',extractor_version='assessment-v3')
    measured=timezone.now().isoformat()
    for index in range(3):
        source=SourceRecord.objects.create(batch=batch,locator='page:'+str(index),data={'url':'https://example.com/'+str(index),'measured_at':measured})
        checks={rule:'pass' for kind in ('technical','content') for rule in EXPECTED[kind]}
        checks['link_to_404']='issue'
        if index==0:checks['missing_meta_descriptions']='issue'
        result=AnalysisResult.objects.create(run=run,source_record=source,outcome='issue',answer={'input':{'url':source.data['url']},'checks':checks,'measured_at':measured})
        for number in range(3):Finding.objects.create(result=result,rule='link_to_404',severity='high',title='Gedeelde verwijzing leidt naar een 404',recommendation='Pas de verwijzing in de gedeelde navigatie aan.',details={'source_url':source.data['url'],'destination':'https://example.com/verouderde-pagina/'})
        if index==0:Finding.objects.create(result=result,rule='missing_meta_descriptions',severity='medium',title='Meta description ontbreekt',recommendation='Schrijf een passende omschrijving voor de landingspagina.',details={'url':source.data['url']})
    dossier=create_dossier(project,user,batch.pk,title='Websiteonderzoek · DEMO')
    for iteration in range(120):
        dossier.refresh_from_db()
        if dossier.status in ('complete','failed'):break
        time.sleep(.5)
    assert dossier.status=='complete',(dossier.status,dossier.error)
    assert dossier.categories.count()==5 and dossier.groups.count()==2
    group=dossier.groups.get(rule='link_to_404');assert group.occurrences==9 and group.affected_urls==3
    action=create_action(project,user,[group],{'title':'Herstel de gedeelde navigatielink','description':'Pas de bestemming in het gedeelde menu aan.','acceptance':'De verwijzing leidt bij een nieuwe meting rechtstreeks naar de juiste pagina.','owner':user,'priority':'medium','effort':'small'})
    client=Client(HTTP_HOST='localhost');client.force_login(user)
    pages={
        'dossier':reverse('ui:reports:dossier',args=[project.pk,dossier.pk]),
        'category':reverse('ui:reports:category',args=[project.pk,dossier.pk,'technical']),
        'group':reverse('ui:reports:group',args=[project.pk,group.pk]),
        'actions':reverse('ui:reports:actions',args=[project.pk]),
        'action':reverse('ui:reports:action',args=[project.pk,action.pk]),
        'prospect':reverse('ui:reports:prospect',args=[project.pk,dossier.pk]),
    }
    for name,url in pages.items():assert client.get(url).status_code==200,name
    assert client.get(pages['prospect']+'?download=1')['Content-Disposition'].startswith('attachment')
    export=client.get(reverse('ui:reports:export',args=[project.pk,dossier.pk]))
    data=b''.join(export.streaming_content).decode();assert 'action_event' in data and 'calculation' in data
    print('PASS: real report queue, frozen inputs, five categories, grouped evidence, actions, private UI and standalone report/export.',flush=True)
    if preview:
        directory=Path(preview);directory.mkdir(parents=True,exist_ok=True)
        path=directory/'session.json'
        path.write_text(json.dumps({'project_id':str(project.pk),'user_id':user.pk,'pages':pages,'session_cookie_name':settings.SESSION_COOKIE_NAME,'session_cookie':client.cookies[settings.SESSION_COOKIE_NAME].value}))
        path.chmod(0o600)
        retain=True
        print('Temporary preview fixture retained for local browser inspection; cleanup required.',flush=True)
finally:
    if not retain:
        if client:client.logout()
        if project:
            ImportBatch.objects.filter(project=project).delete()
            Project.objects.filter(pk=project.pk).delete()
        if user:get_user_model().objects.filter(pk=user.pk).delete()
        print('Temporary runtime data removed.',flush=True)
