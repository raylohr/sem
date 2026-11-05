"""Real networkless Chromium, synthetic external dependency chain; no site traffic."""
import base64
from pathlib import Path
from urllib.parse import urlsplit
from browser_scans.client import render
from scans.network import FetchResult

class Transport:
    calls=[]
    def __init__(self,hosts,**kw):self.documents=[];self.transferred=0;self.decoded_bytes=0
    def close(self):pass
    def fetch_once(self,url):
        self.calls.append(url)
        host=urlsplit(url).hostname
        if host=='fonts.googleapis.com':
            body=b'@font-face{font-family:Probe;src:url(https://fonts.gstatic.com/probe.ttf)}body{font-family:Probe}';mime='text/css'
        elif host=='fonts.gstatic.com':
            body=(Path('/app/browser_scans/vendor/title-font/DejaVuSans.ttf')).read_bytes();mime='font/ttf'
        elif host=='cdn.jsdelivr.net':
            body=b"let s=document.createElement('script');s.src='https://nested.vendor.com/dependency.js';document.head.append(s);";mime='application/javascript'
        elif host=='nested.vendor.com':body=b"document.getElementById('result').textContent='External script loaded';";mime='application/javascript'
        elif host=='images.vendor.com':
            body=b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"><rect width="40" height="20" fill="blue"/></svg>';mime='image/svg+xml'
        else:
            body=b'''<!doctype html><html lang="nl"><head><title>Host probe</title><link rel="stylesheet" href="https://fonts.googleapis.com/probe.css"></head><body><main><h1>Host policy</h1><p id="result">Initial</p><img src="https://images.vendor.com/a.svg" width="40" height="20" alt="Probe"></main><script src="https://cdn.jsdelivr.net/probe.js"></script><script>fetch('http://127.0.0.1/private').catch(()=>{});fetch('/write',{method:'POST'}).catch(()=>{});</script></body></html>''';mime='text/html'
        self.transferred+=len(body);self.decoded_bytes+=len(body)
        return FetchResult(url,200,{'content-type':mime,'access-control-allow-origin':'*'},body,elapsed=.001)

config={'host_policy_version':'discovered-hosts-v2','domain':'example.com','include_subdomains':True,'allow_external_resources':True,
    'allowed_hosts':['example.com'],'blocked_hosts':[],'blocked_resource_hosts':[],'resource_hosts':[],
    'timeout_ms':20000,'max_bytes':20*1024**2,'requests_per_second':5,'respect_robots':False,
    'settle_ms':250,'scroll_steps':0,'profile':'desktop','max_requests':30,'expand_details':False,'exploration_ms':1000}
response=render('https://blog.example.com/',config,check_access=lambda:None,batch_id='host-policy-probe',transport_factory=Transport)
assert {'https://fonts.googleapis.com/probe.css','https://fonts.gstatic.com/probe.ttf','https://cdn.jsdelivr.net/probe.js','https://nested.vendor.com/dependency.js','https://images.vendor.com/a.svg'}<=set(Transport.calls),Transport.calls
assert b'External script loaded' in base64.b64decode(response['dom'])
assert response['images'][0]['natural_width']==40
assert not any('127.0.0.1' in url or url.endswith('/write') for url in Transport.calls)
assert any('127.0.0.1' in item['url'] for item in response['blocked'])
print('PASS: actual Chromium on discovered subdomain; external CSS -> font dependency, script -> new supplier dependency, image loaded; private address and POST blocked; no external website traffic.',flush=True)
