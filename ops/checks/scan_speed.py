"""Controlled latency benchmark: real pacing, threading, extraction and publication.
No requests reach external websites; all database rows roll back.
"""
import json,os,time,tempfile,resource,threading,uuid
from unittest.mock import patch
from django.db import transaction,connection
from django.test import override_settings
from django.utils import timezone
from accounts.models import User
from projects.models import Project,Membership
from scans.models import ScanTarget,ScanDocument
from scans.network import SafeClient
from scans.tasks import process_scan
from catalog.models import URLObservation,ProjectURL
from sources.models import ImportBatch
assert connection.settings_dict['NAME']=='sem_test'
TOTAL=int(os.environ.get('SCAN_SPEED_TOTAL','4366'))
REDIRECTS=min(TOTAL,1146)
LATENCY=float(os.environ.get('SCAN_SPEED_LATENCY','.33'))
NO_PACING=os.environ.get('SCAN_SPEED_NO_PACING')=='1'
RUN_KEY=uuid.uuid4().hex[:12]
assert 1 <= TOTAL <= 100000 and 0 <= LATENCY <= 5
HOST='speed-'+RUN_KEY+'.example.com'
urlbase='https://'+HOST
calls=0
lock=threading.Lock()
class Response:
    def __init__(self,path):
        self.done=False
        if path=='/robots.txt':
            self.status=200;self.headers={'content-type':'text/plain'};self.body=b'User-agent: *\nAllow: /\n'
        else:
            number=int(path.rsplit('/',1)[-1])
            if number<REDIRECTS and not path.startswith('/final/'):
                self.status=301;self.headers={'location':f'/final/{number}'};self.body=b''
            else:
                self.status=200;self.headers={'content-type':'text/html'}
                links=''.join(f'<a href="/{child}">Next</a>' for child in (number*2+1,number*2+2) if child<TOTAL)
                self.body=f'<html><title>Page {number}</title><h1>Page {number}</h1>{links}<p>{"Words "*100}</p></html>'.encode()
    def read(self,*args,**kwargs):
        if self.done:return b''
        self.done=True
        return self.body
    def close(self): pass
    def release_conn(self): pass
class Pool:
    def __init__(self,*args,**kwargs):pass
    def urlopen(self,method,path,**kwargs):
        global calls
        time.sleep(LATENCY)
        with lock:calls+=1
        return Response(path)
    def close(self):pass
class Rollback(Exception):pass
started=time.monotonic()
try:
 with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory),transaction.atomic():
  user=User.objects.create_user('speed-check-'+RUN_KEY)
  project=Project.objects.create(name='Controlled speed check',website=urlbase,created_by=user)
  Membership.objects.create(project=project,user=user,role='admin')
  target=ScanTarget.objects.create(project=project,created_by=user,name='Synthetic',url=urlbase+'/0',allowed_hosts=[HOST])
  batch=ImportBatch.objects.create(project=project,created_by=user,scan_target=target,source='semscan',method='scan',label='Controlled speed',request_metadata={'scan':'crawl','url':urlbase+'/0','max_urls':TOTAL,'max_depth':99,'duration':3600,'allowed_hosts':[HOST],'concurrency':8,'requests_per_second':12,'respect_robots':False})
  with patch('scans.network.resolve_public',return_value='93.184.216.34'),patch('scans.network.urllib3.HTTPSConnectionPool',Pool),patch('scans.tasks.process_scan.apply_async'):
   from contextlib import nullcontext
   pacing = patch.object(SafeClient,'_pace',return_value=None) if NO_PACING else nullcontext()
   with pacing:
    for round_number in range(15):
     process_scan(str(batch.pk));batch.refresh_from_db()
     print(json.dumps({'round':round_number+1,'status':batch.status,'requests':calls,'progress':batch.request_metadata.get('scan_progress',{}),'elapsed':round(time.monotonic()-started,2)}),flush=True)
     assert batch.status in ('processing','complete'),batch.error
     if batch.status=='complete':break
    else:raise AssertionError('did not complete')
  counts={'records':batch.records.count(),'urls':ProjectURL.objects.filter(project=project).count(),'observations':URLObservation.objects.filter(batch=batch).count(),'documents':ScanDocument.objects.filter(batch=batch).count()}
  assert all(counts[k]==TOTAL for k in ('records','urls','observations')),counts
  print('PASS',json.dumps({**counts,'requests':calls,'elapsed_seconds':round(time.monotonic()-started,2),'peak_rss_mib':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,1)}),flush=True)
  raise Rollback
except Rollback:print('Benchmark rows rolled back.',flush=True)
