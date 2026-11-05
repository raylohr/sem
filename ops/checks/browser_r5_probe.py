"""Actual isolated Chromium; bounded synthetic HTTP inputs, no external website traffic."""
import base64
from browser_scans.client import render
from scans.network import FetchResult

class FixtureTransport:
    large_font=False
    calls=[]
    def __init__(self,hosts,**kw):self.documents=[];self.transferred=0;self.decoded_bytes=0
    def close(self):pass
    def fetch_once(self,url):
        self.calls.append(url)
        if url.endswith('/pixel.svg'):
            body=b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><rect width="100" height="50" fill="blue"/></svg>';mime='image/svg+xml'
        else:
            body=('''<!doctype html><html lang="nl"><head><title>Fixed model title for R5</title></head><body style="min-height:6000px;font-size:FONTSIZEpx"><h1>Content</h1>
            <details ontoggle="if(this.open&&!document.getElementById('detailslink')){let a=document.createElement('a');a.id='detailslink';a.href='/details-only';a.textContent='Details';this.append(a)}"><summary>Details</summary></details>
            <img id="lazy" width="24" height="12" alt="Fixture" style="position:absolute;top:4000px">
            <script>addEventListener('scroll',()=>{if(scrollY>1000&&!document.getElementById('late')){let a=document.createElement('a');a.id='late';a.href='/scroll-only';a.textContent='New after scroll';document.body.append(a);document.getElementById('lazy').src='/pixel.svg'}});fetch('/write',{method:'POST'}).catch(()=>{});fetch('http://127.0.0.1/private').catch(()=>{});</script></body></html>''').replace('FONTSIZE','100' if self.large_font else '12').encode();mime='text/html'
        self.transferred+=len(body);self.decoded_bytes+=len(body)
        return FetchResult(url,200,{'content-type':mime},body,elapsed=.001)

config={'allowed_hosts':['example.com'],'resource_hosts':[],'timeout_ms':20000,'max_bytes':20*1024**2,
    'requests_per_second':1,'respect_robots':False,'settle_ms':100,'scroll_steps':6,'profile':'desktop','max_requests':30,'expand_details':True,'exploration_ms':4000}
result=render('https://example.com/',config,check_access=lambda:None,batch_id='r5-synthetic',transport_factory=FixtureTransport)
links={row['url']:row['phase'] for row in result['exploration']['links']}
assert links['https://example.com/scroll-only']=='after_scroll',links
assert links['https://example.com/details-only']=='after_details',links
image=result['images'][0]
assert image['natural_width']==100 and image['natural_height']==50,image
assert image['rendered_width']==24 and image['rendered_height']==12,image
assert image['current_src']=='https://example.com/pixel.svg' and image['device_pixel_ratio']==1
assert result['exploration']['before_images'][0]['natural_width'] is None
width=result['title_width'];assert width['status']=='measured' and width['width_px']>0,width
FixtureTransport.large_font=True
second=render('https://example.com/',config,check_access=lambda:None,batch_id='r5-synthetic-2',transport_factory=FixtureTransport)
assert second['title_width']['width_px']==width['width_px'],(width,second['title_width'])
assert second['title_width']['font_sha256']==width['font_sha256']
assert not any('127.0.0.1' in url or url.endswith('/write') for url in FixtureTransport.calls)
assert base64.b64decode(result['screenshot']).startswith(b'\x89PNG')
print('PASS: actual Chromium; scroll-only/details-only discovery; lazy image loaded with currentSrc, intrinsic/rendered geometry and DPR; reproducible title estimate independent of body CSS; POST/private network blocked.',flush=True)
print('Title model:',width,'Chromium:',result['browser_version'],flush=True)
