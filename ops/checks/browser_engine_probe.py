"""Real isolated Chromium, synthetic public responses; no external requests."""
import base64
from urllib.parse import urlsplit
from browser_scans.client import render
from scans.network import FetchResult

class SyntheticTransport:
    calls = []
    def __init__(self, hosts, **kwargs):
        self.documents = []
        self.transferred = self.decoded_bytes = 0
    def close(self):
        pass
    def fetch_once(self, url):
        self.calls.append(url)
        if url.endswith('/missing.png'):
            body, status, mime = b'missing', 404, 'image/png'
        else:
            body = b'''<!doctype html><html lang="nl"><head><title>Server title</title></head><body><main><h1>Server</h1>
              <img src="/missing.png"><script>document.title='Rendered title';document.querySelector('h1').textContent='JavaScript content';
              const a=document.createElement('a');a.href='/js-only';a.textContent='JS link';document.querySelector('main').append(a);
              fetch('http://127.0.0.1:8000/private').catch(()=>{});fetch('/write',{method:'POST'}).catch(()=>{});
              </script></main></body></html>'''
            status, mime = 200, 'text/html; charset=utf-8'
        self.transferred += len(body); self.decoded_bytes += len(body)
        return FetchResult(url=url,status=status,headers={'content-type':mime},body=body,elapsed=0.01)

config={'allowed_hosts':['example.com'],'resource_hosts':[],'timeout_ms':15000,'max_bytes':20*1024**2,
    'requests_per_second':1,'respect_robots':False,'settle_ms':300,'scroll_steps':0,'profile':'desktop','max_requests':30}
result=render('https://example.com/',config,check_access=lambda:None,batch_id='synthetic',transport_factory=SyntheticTransport)
dom=base64.b64decode(result['dom'])
assert b'JavaScript content' in dom and b'Rendered title' in dom and b'/js-only' in dom
assert result['accessibility']['version']=='4.14.0'
assert result['metrics']['cls'] is not None
assert result['metrics']['fcp_ms'] is not None
assert any(issue['id']=='image-alt' for issue in result['accessibility']['violations'])
assert any(row.get('status')==404 for row in result['resources'])
assert any(row['method']=='POST' and row.get('reason') for row in result['resources'])
assert not any('127.0.0.1' in url for url in SyntheticTransport.calls)
assert not any(url.endswith('/write') for url in SyntheticTransport.calls)
assert base64.b64decode(result['screenshot']).startswith(b'\x89PNG')
print('PASS: native Chromium sandbox, JavaScript content/links, axe image-alt, 404 resource, screenshot and private/POST request blocking.')
print('Engine:',result['browser_version'],'axe:',result['accessibility']['version'],'resources:',len(result['resources']))
