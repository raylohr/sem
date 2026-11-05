"""Actual networkless Chromium + production broker/slots; synthetic HTTP only."""
import base64
import threading
import time
from urllib.parse import urlsplit
from browser_scans.client import render
from scans.network import SafeClient, FetchResult

class Transport(SafeClient):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.active=self.peak=0
        self.starts=[]
        Transport.last=self

    def _request_impl(self,url,delay,cookie_header=''):
        self._pace(urlsplit(url).hostname,delay)
        if url.endswith('/robots.txt'):
            return FetchResult(url,404,{},b'')
        if '/style-' in url:
            with self.lock:
                self.active+=1;self.peak=max(self.peak,self.active)
                self.starts.append(time.monotonic())
            # Force responses to finish in a different order than their requests.
            time.sleep(.4 if url.endswith('0.css') else .2)
            with self.lock:self.active-=1
            body=b'body{color:rgb(20,30,40)}';mime='text/css'
        else:
            body=('''<!doctype html><html lang="nl"><head><title>Parallel probe</title>'''+
                ''.join('<link rel="stylesheet" href="/style-%s.css">'%i for i in range(6))+
                '''</head><body><main><h1>Parallel broker</h1><script>document.title='All styles received';fetch('http://127.0.0.1/private').catch(()=>{});fetch('/write',{method:'POST'}).catch(()=>{});</script></main></body></html>''').encode();mime='text/html'
        with self.lock:self.transferred+=len(body);self.decoded_bytes+=len(body)
        return FetchResult(url,200,{'content-type':mime},body,elapsed=.01)

config={'allowed_hosts':['example.com'],'resource_hosts':[],'timeout_ms':20000,
    'max_bytes':20*1024**2,'requests_per_second':15,'respect_robots':False,
    'settle_ms':500,'scroll_steps':0,'profile':'desktop','max_requests':30,'expand_details':False}
for concurrency in (1,3):
    result=render('https://example.com/',{**config,'concurrency':concurrency},
        check_access=lambda:None,batch_id='network-limits-probe',transport_factory=Transport)
    client=Transport.last
    assert client.peak==concurrency,(client.peak,concurrency,result.get('errors'))
    styles=[r for r in result['resources'] if '/style-' in r['url']]
    assert len(styles)==6 and all(r.get('status')==200 for r in styles),styles
    assert all(b-a>=.060 for a,b in zip(client.starts,client.starts[1:])),client.starts
    assert b'All styles received' in base64.b64decode(result['dom'])
    assert any(r['method']=='POST' and r.get('reason') for r in result['resources'])
    assert any('127.0.0.1' in r['url'] for r in result['blocked'])
    assert result['title_width']['status']=='measured',result['title_width']
    print('PASS native Chromium: concurrency=%s peak=%s; 6 correct out-of-order CSS responses; host pacing >=1/15s; title font; private/POST blocked.'%(concurrency,client.peak),flush=True)

class PrivacyTransport:
    cookies=[]
    def __init__(self,*args,**kwargs):
        self.documents=[];self.transferred=self.decoded_bytes=0
    def close(self):pass
    def fetch_once(self,url,cookie_header=''):
        if url.endswith('/after'):
            self.cookies.append(cookie_header);body=b'ok';mime='text/plain'
        else:
            body=b'''<!doctype html><html lang="nl"><head><title>Consent probe</title></head><body><main><h1>Consent</h1><button id="accept" onclick="document.cookie='consent=yes; Path=/; Secure';fetch('/after')">Accepteren</button></main></body></html>''';mime='text/html'
        self.transferred+=len(body);self.decoded_bytes+=len(body)
        return FetchResult(url,200,{'content-type':mime},body,elapsed=.01)
privacy=render('https://example.com/',{**config,'concurrency':3,'privacy_enabled':True,
    'scenario':'accept','consent_selectors':{'accept':'#accept'}},
    check_access=lambda:None,batch_id='network-privacy-probe',transport_factory=PrivacyTransport)
assert privacy['privacy']['complete'],privacy['privacy']
assert any(s['kind']=='cookie' and s['name']=='consent' and s['value_omitted'] for s in privacy['privacy']['snapshots'])
assert any('consent=yes' in value for value in PrivacyTransport.cookies),PrivacyTransport.cookies
print('PASS async consent interaction, redacted storage evidence and explicitly enabled ephemeral cookie transport.',flush=True)
