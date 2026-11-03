"""Native sandbox with synthetic consent UI; no external traffic or persisted values."""
import base64
import json
from browser_scans.client import render
from scans.network import FetchResult

PAGE = b'''<!doctype html><html lang="en"><head><title>Consent fixture</title></head><body><main><h1>Fixture</h1>
<div id="onetrust-banner-sdk"><button id="onetrust-accept-btn-handler" onclick="choose('accepted')">Accept all</button>
<button id="onetrust-reject-all-handler" onclick="choose('rejected')">Reject all</button></div>
<button id="ot-sdk-btn" onclick="document.querySelector('#onetrust-banner-sdk').hidden=false">Preferences</button>
<script>
window.initialCookie=document.cookie;
function choose(value){localStorage.setItem('consent',value);document.cookie='consent='+value+'; Secure; SameSite=Lax; path=/';
document.querySelector('#onetrust-banner-sdk').hidden=true;
if(value==='accepted'){fetch('/collect?client_id=private-identifier');fetch('/cookie-check');}}
</script></main></body></html>'''

class Transport:
    calls=[]
    def __init__(self,*args,**kwargs):
        self.documents=[];self.transferred=self.decoded_bytes=0
    def close(self):pass
    def fetch_once(self,url, cookie_header=''):
        self.calls.append((url,cookie_header))
        body=PAGE if url=='https://example.com/' else b'ok'
        self.transferred+=len(body);self.decoded_bytes+=len(body)
        return FetchResult(url=url,status=200,headers={'content-type':'text/html' if body==PAGE else 'text/plain'},body=body,elapsed=.01,
            set_cookies=['http_only=secret-http-cookie; Path=/; Secure; HttpOnly; SameSite=Lax'] if body==PAGE else [])

config={'allowed_hosts':['example.com'],'resource_hosts':[],'timeout_ms':15000,'max_bytes':20*1024**2,
    'requests_per_second':10,'respect_robots':False,'settle_ms':150,'scroll_steps':0,'profile':'desktop','max_requests':30,'privacy_enabled':True}
for scenario in ['no_choice','reject','accept','withdraw']:
    Transport.calls=[]
    result=render('https://example.com/',{**config,'scenario':scenario},check_access=lambda:None,batch_id='privacy-fixture',transport_factory=Transport)
    assert result['privacy']['complete'],result['privacy']
    assert Transport.calls[0][1]=='',Transport.calls
    assert any(item['name']=='http_only' and item['metadata']['httpOnly'] for item in result['privacy']['snapshots']), result['privacy']
    if scenario in ('accept','withdraw'):
        assert any('consent=accepted' in cookies and 'http_only=secret-http-cookie' in cookies for url,cookies in Transport.calls if '/cookie-check' in url),Transport.calls
    stored=json.dumps({key:result[key] for key in ('privacy','resources','policy') if key in result})
    assert 'secret-http-cookie' not in stored and 'private-identifier' not in stored,stored
    assert all('value' not in item for item in result['privacy']['snapshots'])
    assert not any('initialCookie="consent' in base64.b64decode(result['dom']).decode() for _ in [0])
    print('PASS:',scenario,'fresh context, verified controls, HttpOnly cookie transport and redacted observations',flush=True)
