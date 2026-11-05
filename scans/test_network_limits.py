"""Concurrency, pacing and byte-budget invariants across network phases."""
import io
import json
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from .network import SafeClient, FetchResult, ScanError
from .resource_transfer import read_body
from .resources import ResourceClient, initialize, add, run
from .work import ScanWork


class NetworkLimitTests(SimpleTestCase):
    def test_one_request_does_not_impose_two_per_second_floor(self):
        client=SafeClient(['example.com'],check_access=lambda:None,concurrency=1,delay=1/15)
        try:self.assertAlmostEqual(client.delay,1/15)
        finally:client.close()

    def test_shared_root_slots_cap_multiple_clients_and_release_on_error(self):
        root=str(uuid.uuid4())
        clients=[SafeClient(['example.com'],check_access=lambda:None,concurrency=2,
            shared_rate=True,batch_id=str(uuid.uuid4()),network_scope=root) for _ in range(2)]
        lock=threading.Lock();active=peak=0
        def request(url,delay,cookie=''):
            nonlocal active,peak
            with lock:active+=1;peak=max(peak,active)
            try:
                time.sleep(.04)
                if url.endswith('/error'):raise ScanError('probe')
                return FetchResult(url,200,{},b'')
            finally:
                with lock:active-=1
        try:
            for client in clients:client._request_impl=request
            with ThreadPoolExecutor(max_workers=8) as executor:
                jobs=[executor.submit(clients[i%2]._request,'https://example.com/'+('error' if i==0 else str(i)),0) for i in range(8)]
                outcomes=[]
                for job in jobs:
                    try:outcomes.append(job.result())
                    except ScanError:pass
            self.assertEqual(len(outcomes),7)
            self.assertEqual(peak,2)
            self.assertEqual(clients[0].shared_slots.redis.zcard(clients[0].shared_slots.key),0)
        finally:
            for client in clients:client.close()

    def test_shared_host_pacing_applies_across_clients(self):
        from .throttle import HostThrottle
        host=uuid.uuid4().hex+'.example.com'
        first=HostThrottle();second=HostThrottle()
        self.assertEqual(first.wait(host,.5),0)
        self.assertGreater(second.wait(host,.5),.3)
        self.assertEqual(second.wait(uuid.uuid4().hex+'.example.com',.5),0)

    def test_parallel_readers_cannot_spend_the_same_byte_budget(self):
        client=SimpleNamespace(resource_body_limit=100,max_transfer=100,transferred=0,decoded_bytes=0,
            guard=lambda:None,lock=threading.RLock())
        class Slow(io.BytesIO):
            def read(self,n,**kwargs):
                time.sleep(.02)
                return super().read(n)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:read_body(client,Slow(b'x'*100),{'content-length':'100'}),range(4)))
        self.assertEqual(client.transferred,100)
        self.assertEqual(client.decoded_bytes,100)
        self.assertEqual(sum(len(body) for body,_ in results),100)
        self.assertEqual(sum(data['body_complete'] for _,data in results),1)

    def test_files_and_external_targets_overlap_and_keep_sqlite_on_owner(self):
        batch=SimpleNamespace(pk=uuid.uuid4())
        config={'duration':600,'allowed_hosts':['example.com'],'check_resources':True,
            'check_external_links':True,'requests_per_second':15,'concurrency':3,'respect_robots':False}
        owner=threading.get_ident();lock=threading.Lock();active={};peaks={}
        def permission():self.assertEqual(threading.get_ident(),owner)
        def fetch(client,url):
            with lock:
                active[client.purpose]=active.get(client.purpose,0)+1
                peaks[client.purpose]=max(peaks.get(client.purpose,0),active[client.purpose])
            time.sleep(.03)
            with lock:active[client.purpose]-=1
            return FetchResult(url,200,{'content-type':'text/javascript'},b'',measurements={'body_complete':True})
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory),ScanWork(batch.pk) as work:
            initialize(work);work.set('resource_seed_done',True)
            for purpose in ('resource','external'):
                for i in range(8):
                    add(work,'https://example.com/'+purpose+str(i),purpose,{'source_url':'https://example.com/'})
            with patch.object(ResourceClient,'fetch',autospec=True,side_effect=fetch):
                run(batch,work,config,permission)
            self.assertEqual(peaks,{'resource':3,'external':3})
            rows=work.connection.execute('SELECT state,result FROM resource_targets').fetchall()
            self.assertEqual(len(rows),16)
            self.assertTrue(all(state=='done' and json.loads(result)['outcome']=='success' for state,result in rows))

    def test_robots_is_once_per_origin_and_parallel_between_hosts(self):
        client=SafeClient([],check_access=lambda:None,concurrency=4)
        active=peak=0;calls=[];lock=threading.Lock()
        def load(url):
            nonlocal active,peak
            origin=url.split('/')[2]
            if origin in client.robots:return client.robots[origin]
            with lock:active+=1;peak=max(peak,active);calls.append(origin)
            time.sleep(.04)
            client.robots[origin]=('policy',0)
            with lock:active-=1
            return client.robots[origin]
        try:
            with patch.object(client,'_load_robot_policy',side_effect=load),ThreadPoolExecutor(max_workers=4) as pool:
                results=list(pool.map(client._robot_policy,['https://a.example/x','https://a.example/y','https://b.example/x','https://c.example/x']))
            self.assertEqual(len(results),4)
            self.assertEqual(sorted(calls),['a.example','b.example','c.example'])
            self.assertGreater(peak,1)
        finally:client.close()

    def test_expired_slots_are_reclaimed_and_renewed_slots_are_retained(self):
        from .throttle import RequestSlots,RENEW
        slots=RequestSlots(str(uuid.uuid4()),1,threading.Event())
        try:
            slots.redis.zadd(slots.key,{'dead-worker':1})
            token=slots.acquire(lambda:None)
            self.assertIsNone(slots.redis.zscore(slots.key,'dead-worker'))
            slots.redis.zadd(slots.key,{token:1})
            slots.redis.eval(RENEW,1,slots.key,token)
            seconds,microseconds=slots.redis.time()
            self.assertGreater(slots.redis.zscore(slots.key,token),seconds*1000+microseconds/1000+170000)
            slots.release(token)
        finally:slots.close()
