"""Run with the test migration role: manage.py shell < ops/checks/scan_scale.py.
Synthetic HTTP responses only; all test database rows are rolled back.
"""
import json
import os
import resource
import tempfile
import time
from unittest.mock import patch
from django.db import connection, transaction
from django.test import override_settings
from django.utils import timezone
from accounts.models import User
from projects.models import Project, Membership
from sources.models import ImportBatch
from catalog.models import ProjectURL,URLObservation
from scans.models import ScanTarget,ScanDocument
from scans.network import FetchResult
from scans.tasks import process_scan
from scans.work import ScanPause

assert connection.settings_dict['NAME']=='sem_test', 'Never run a scale check in production'
TOTAL=100_000
KIND=os.environ.get("SCAN_SCALE_KIND","crawl")
assert KIND in {"crawl","sitemap"}
started=time.monotonic()


class SyntheticClient:
    def __init__(self,hosts,**kwargs):
        self.allowed_hosts=set(hosts)
        self.documents=kwargs['document_sink']
        self.robot_cache=kwargs.get('robot_cache',{})
        self.transferred=self.decoded_bytes=0
        self.calls=0

    def close(self):
        pass

    def guard(self):
        if self.calls>=5000:raise ScanPause

    def fetch(self,url):
        self.guard();self.calls+=1
        if KIND=='sitemap':
            if url.endswith('sitemap.xml'):
                body=b'<sitemapindex><sitemap><loc>https://example.com/part-0.xml</loc></sitemap><sitemap><loc>https://example.com/part-1.xml</loc></sitemap></sitemapindex>'
            else:
                part=int(url.rsplit('part-',1)[-1].split('.')[0])
                body=('<urlset>'+''.join(f'<url><loc>https://example.com/{i}</loc></url>' for i in range(part*50000,(part+1)*50000))+'</urlset>').encode()
            self.documents.append({'url':url,'status':200,'content_type':'application/xml','body':body,'fetched_at':timezone.now()})
            self.transferred+=len(body);self.decoded_bytes+=len(body)
            return FetchResult(url,200,{'content-type':'application/xml'},body)
        number=int(url.rsplit('/',1)[-1])
        links=''.join(f'<a href="/{child}">Next</a>' for child in (number*2+1,number*2+2) if child<TOTAL)
        body=f'<html><title>Page {number}</title><h1>Page {number}</h1>{links}</html>'.encode()
        self.documents.append({'url':url,'status':200,'content_type':'text/html','body':body,'fetched_at':timezone.now()})
        self.transferred+=len(body);self.decoded_bytes+=len(body)
        return FetchResult(url,200,{'content-type':'text/html'},body)


class RollbackScaleCheck(Exception):pass

try:
    with tempfile.TemporaryDirectory(prefix='sem-scale-') as directory, override_settings(MEDIA_ROOT=directory), transaction.atomic():
        user=User.objects.create_user('scale-check-user')
        project=Project.objects.create(name='Synthetic scale check',website='https://example.com',created_by=user)
        Membership.objects.create(project=project,user=user,role='admin')
        target=ScanTarget.objects.create(project=project,created_by=user,name='Synthetic',url='https://example.com/0',allowed_hosts=['example.com'])
        batch=ImportBatch.objects.create(project=project,created_by=user,scan_target=target,source='semscan',method='scan',label='Synthetic 100k',request_metadata={'scan':KIND,'url':'https://example.com/0' if KIND=='crawl' else 'https://example.com/sitemap.xml','max_urls':TOTAL,'max_depth':100,'duration':604800,'allowed_hosts':['example.com']})
        with patch('scans.tasks.SafeClient',SyntheticClient), patch('scans.tasks.process_scan.apply_async'):
            for iteration in range(30):
                process_scan(str(batch.pk));batch.refresh_from_db()
                count=batch.request_metadata.get('scan_progress',{}).get('records',0)
                print('Synthetic scan round',iteration+1,'checkpoint records',count,'status',batch.status,flush=True)
                if batch.status=='complete':break
                assert batch.status=='processing',batch.error
            else:raise AssertionError('Scale check did not finish')
        counts={'records':batch.records.count(),'urls':ProjectURL.objects.filter(project=project).count(),
                'observations':URLObservation.objects.filter(batch=batch).count(),'documents':ScanDocument.objects.filter(batch=batch).count()}
        assert all(counts[key]==TOTAL for key in ('records','urls','observations')),counts
        assert counts['documents']==(TOTAL if KIND=='crawl' else 3),counts
        print('PASS',json.dumps({'kind':KIND,**counts,'elapsed_seconds':round(time.monotonic()-started,2),'peak_rss_mib':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,1)}),flush=True)
        raise RollbackScaleCheck
except RollbackScaleCheck:
    print('Scale-check DB rows rolled back and private checkpoints removed.',flush=True)
