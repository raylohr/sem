"""sem_test only: realistic synthetic crawl -> classifications -> frozen report.
No production export or network requests. Removes all temporary records.
"""
import json,hashlib,uuid,time
from types import SimpleNamespace
from unittest.mock import patch
from django.db import connection
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from projects.models import Project,Membership
from sources.models import ImportBatch
from sources.services import publish_records
from scans.models import ResourceMeasurement,ResourceReference
from scans.extraction import extract_page
from assessments.models import ReferenceObservation
from reporting.services import create_dossier,assemble
from reporting.models import Dossier
assert connection.settings_dict['NAME']=='sem_test','Test database only'
user=project=None
started=time.monotonic()
try:
    user=get_user_model().objects.create_user('reference-runtime-'+uuid.uuid4().hex)
    project=Project.objects.create(name='Temporary reference benchmark',website='https://example.com/',created_by=user)
    Membership.objects.create(project=project,user=user,role='admin')
    batch=ImportBatch.objects.create(project=project,created_by=user,source='semscan',method='scan',request_metadata={
        'scan':'crawl','domain':'example.com','allowed_hosts':['example.com'],'include_subdomains':True,
        'blocked_hosts':['private.example.com'],'allow_external_resources':True,'resource_version':'resources-v1',
        'check_resources':True,'check_external_links':True,'respect_robots':False})
    targets={}
    for purpose,url,status,mime,outcome in [('resource','https://example.com/app.js',200,'text/html','success'),
        ('resource','https://example.com/manual.pdf',200,'text/html','success'),
        ('external','https://www.google.com/',200,'text/html','success'),
        ('external','https://www.linkedin.com/in/example/',999,'text/html','unknown_response')]:
        targets[url]=ResourceMeasurement.objects.create(batch=batch,purpose=purpose,url=url,url_hash=hashlib.sha256(url.encode()).hexdigest(),
            http_status=status,outcome=outcome,headers={'content-type':mime},measurements={'wire_bytes':123,'wire_complete':True,'representation_complete':True})
    records=[]
    for i in range(364):
        url='https://example.com/'+('' if not i else 'page/'+str(i))
        next_url='/' if i==363 else '/page/'+str(i+1)
        html='''<!doctype html><html lang="nl"><head><title>Page %s</title><link rel="canonical" href="%s"><script src="/app.js"></script></head><body><main><h1 id="section">Page %s</h1><a href="%s">Next</a><a href="https://www.google.com/">Search</a><a href="https://www.linkedin.com/in/example/">Profile</a><a href="/manual.pdf">Document</a><a href="#section">Section</a><a href="mailto:info@example.com">Mail</a></main></body></html>'''%(i,url,i,next_url)
        if i%17==0:html=html.replace('</main>','<a href="https://private.example.com/">Excluded</a></main>')
        fields,_=extract_page(SimpleNamespace(url=url,status=200,headers={'content-type':'text/html','strict-transport-security':'max-age=31536000','x-content-type-options':'nosniff','content-security-policy':"default-src 'self'"},body=html.encode()))
        records.append({'locator':'page:'+str(i),'data':{**fields,'url':url,'status':200,'final_status':200,'content_type':'text/html','googlebot_allowed':True,'measured_at':'2026-11-05T12:00:00+00:00'}})
        for target_url,kind in [('https://example.com/app.js','script'),('https://example.com/manual.pdf','download'),('https://www.google.com/','hyperlink'),('https://www.linkedin.com/in/example/','hyperlink')]:
            ResourceReference.objects.create(target=targets[target_url],reference_hash=hashlib.sha256((url+kind).encode()).hexdigest(),source_url=url,kind=kind,details={'source_url':url,'kind':kind})
    with patch('scans.network.SafeClient.fetch',side_effect=AssertionError('No network requests in reference analysis')):
        publish_records(batch_id=batch.pk,records=records)
        with patch('reporting.services.enqueue'):dossier=create_dossier(project,user,batch.pk)
        dossier=assemble(dossier.pk)
    technical=dossier.categories.get(key='technical');files=dossier.categories.get(key='files')
    for rule in ('link_to_404','link_to_5xx','link_to_redirect','link_to_nonindexable'):
        assert technical.calculation['per_rule'][rule]['unknown']==0,technical.calculation
        assert technical.calculation['per_rule'][rule]['not_tested']==22
    assert technical.coverage['percent']>=80,technical.coverage
    assert files.coverage['counts']['unknown']==1,files.coverage
    assert files.coverage['counts']['issue']>=2,files.coverage
    assert dossier.references.filter(destination='external',outcome='unknown').count()==364
    assert dossier.references.filter(destination='own',scope='excluded',outcome='not_tested').count()==22
    assert dossier.groups.filter(rule='resource_download_mime',category='files').exists()
    client=Client(HTTP_HOST='localhost');client.force_login(user)
    for name,args in [('dossier',[project.pk,dossier.pk]),('category',[project.pk,dossier.pk,'files']),('references',[project.pk,dossier.pk])]:
        response=client.get(reverse('ui:reports:'+name,args=args))
        assert response.status_code==200,(name,response.status_code)
    response=client.get(reverse('ui:reports:references',args=[project.pk,dossier.pk])+'?destination=external&outcome=unknown&download=1')
    assert len(b''.join(response.streaming_content).splitlines())==365
    print(json.dumps({'result':'PASS','pages':364,'references':dossier.references.count(),'technical_coverage':technical.coverage['percent'],
        'technical_link_unknown':0,'technical_excluded_pages':22,'files_coverage':files.coverage['percent'],'file_issues':files.coverage['counts']['issue'],
        'external_unknown_separate':364,'version':dossier.version,'elapsed_seconds':round(time.monotonic()-started,1)}),flush=True)
finally:
    if project:
        ImportBatch.objects.filter(project=project).delete()
        Project.objects.filter(pk=project.pk).delete()
    if user:get_user_model().objects.filter(pk=user.pk).delete()
    print('Temporary reference benchmark records removed.',flush=True)
