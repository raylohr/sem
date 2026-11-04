"""Bounded reporting benchmark on sem_test; rolls all synthetic records back."""
import os
import json
import time
import resource
from unittest.mock import patch
from django.db import connection,transaction
from django.utils import timezone
from accounts.models import User
from projects.models import Project,Membership
from sources.models import ImportBatch,SourceRecord
from assessments.models import AnalysisDefinition,AnalysisRun,AnalysisResult,Finding
from reporting.catalog import EXPECTED
from reporting.services import create_dossier,assemble

assert connection.settings_dict['NAME']=='sem_test'
assert connection.settings_dict['USER']=='sem_test_migrate'
total=int(os.environ.get('REPORT_SCALE_URLS','5000'));assert 100<=total<=10000
started=time.monotonic();queries=0

def count_query(execute,sql,params,many,context):
    global queries
    queries+=1
    return execute(sql,params,many,context)

class Rollback(Exception):pass
try:
    with transaction.atomic(),connection.execute_wrapper(count_query):
        user=User.objects.create_user('report-scale')
        project=Project.objects.create(name='Report scale',website='https://example.com/',created_by=user)
        Membership.objects.create(project=project,user=user,role='admin')
        batch=ImportBatch.objects.create(project=project,created_by=user,label='Report scale',source='semscan',method='scan',status='complete',request_metadata={'scan':'crawl'})
        definition,_=AnalysisDefinition.objects.get_or_create(key='crawl-basics',version='3')
        run=AnalysisRun.objects.create(batch=batch,definition=definition,status='complete')
        measured=timezone.now().isoformat()
        sources=SourceRecord.objects.bulk_create([SourceRecord(batch=batch,locator='page:'+str(i),data={'url':'https://example.com/'+str(i),'measured_at':measured}) for i in range(total)],batch_size=500)
        checks={rule:'pass' for kind in ('technical','content') for rule in EXPECTED[kind]};checks['link_to_404']='issue'
        results=AnalysisResult.objects.bulk_create([AnalysisResult(run=run,source_record=source,outcome='issue',answer={'input':{'url':source.data['url']},'checks':checks,'measured_at':measured}) for source in sources],batch_size=500)
        for offset in range(0,total,500):
            Finding.objects.bulk_create([Finding(result=result,rule='link_to_404',title='Shared broken link',severity='high',recommendation='Fix shared navigation',details={'source_url':result.answer['input']['url'],'destination':'https://example.com/missing'}) for result in results[offset:offset+500] for _ in range(10)],batch_size=500)
        with patch('reporting.services.enqueue'):dossier=create_dossier(project,user,batch.pk)
        build_started=time.monotonic();dossier=assemble(dossier.pk)
        assert dossier.status=='complete' and dossier.groups.count()==1
        group=dossier.groups.get();assert group.affected_urls==total and group.occurrences==total*10
        assert dossier.categories.get(key='content').score==100
        print(json.dumps({'urls':total,'findings':total*10,'groups':1,'build_seconds':round(time.monotonic()-build_started,2),
            'total_seconds':round(time.monotonic()-started,2),'queries':queries,'peak_rss_mib':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,1)},indent=2),flush=True)
        raise Rollback
except Rollback:print('PASS: source records, report and evidence rolled back; no network calls.',flush=True)
