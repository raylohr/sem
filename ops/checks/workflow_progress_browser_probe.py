"""Native Chromium UI probe; JSON input contains rendered template and chart JS.
Uses synthetic fetch responses and never starts a crawl or makes website requests.
"""
import json
import sys
from playwright.sync_api import sync_playwright

payload=json.load(sys.stdin)
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,chromium_sandbox=True)
    page=browser.new_page(viewport={'width':1440,'height':1000})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.set_content('<style>'+payload['css']+'</style>'+payload['html'])
    page.evaluate('''()=>{
      window.calls=0;window.mode='external';
      window.fetch=async()=>{calls++;const ended=mode==='finished',browser=mode==='browser';return {ok:true,status:200,json:async()=>({
        status:'complete',status_label:'Volledig',active:false,available:true,records:364,documents:383,pending:0,errors:0,
        interval_seconds:3,window_seconds:300,end:300,totals:{'200':367},series:[],
        workflow:{active:!ended,telemetry_available:true,current_phase:ended?null:browser?'browser':'external',status_label:'Onderzoek afgerond',
          end:300,updated_at:300,stale_seconds:0,
          phases:[{name:'pages',label:'Pagina’s bezoeken',status:'complete',done:364,total:364},{name:'files',label:'Bestanden controleren',status:'complete',done:653,total:653},
          {name:'external',label:'Externe links controleren',status:browser||ended?'complete':'running',done:browser||ended?191:8,total:191,pending:183,eta_seconds:90,elapsed_seconds:12},
          ...(browser||ended?[{name:'browser',label:'Browseronderzoek',status:ended?'complete':'running',done:ended?15:7,total:15}]:[])],
          requests:ended?[]:[{phase:browser?'browser':'external',host:'linkedin.com',state:'request',waiting_seconds:12}],
          totals:[{phase:'files',status:'200',outcome:'success',n:10},{phase:'external',status:'999',outcome:'unknown',n:1},{phase:'external',status:'404',outcome:'http_error',n:1},{phase:'external',status:'retrying',outcome:'retrying',n:1}],
          series:[{bucket:299,phase:'files',status:'200',outcome:'success',n:10},{bucket:300,phase:'external',status:'999',outcome:'unknown',n:1},{bucket:300,phase:'external',status:'404',outcome:'http_error',n:1},{bucket:300,phase:'external',status:'retrying',outcome:'retrying',n:1}]
        }})}};
    }''')
    page.add_script_tag(content=payload['js'])
    page.wait_for_function('calls>=1')
    assert 'Externe links controleren' in page.locator('[data-live-phase]').inner_text()
    assert '653 / 653' in page.locator('[data-live-phases]').inner_text()
    assert '8 / 191' in page.locator('[data-live-phases]').inner_text()
    assert 'linkedin.com' in page.locator('[data-live-activity]').inner_text() and '12 s' in page.locator('[data-live-activity]').inner_text()
    assert 'Wordt afgerond' not in page.locator('[data-live-eta]').inner_text()
    assert 'voor deze fase' in page.locator('[data-live-eta]').inner_text()
    page.locator('[data-live-phase-filter]').select_option('external')
    assert page.locator('path[data-status="success"]').count()==0
    page.locator('[data-live-outcome-filter]').select_option('unknown')
    assert page.locator('path[data-status]').count()==1
    page.locator('[data-live-group-filter]').select_option('status')
    assert page.locator('path[data-status="999"]').count()==1
    if payload.get('screenshot'):
        import base64
        print('SCREENSHOT:'+base64.b64encode(page.screenshot(full_page=True)).decode())
    page.evaluate("mode='browser'")
    page.wait_for_function("document.querySelector('[data-live-phase]').textContent==='Browseronderzoek'")
    assert page.evaluate('calls')>=2 # root crawl was complete on every response
    page.evaluate("mode='finished'")
    page.wait_for_function("document.querySelector('[data-live-phase]').textContent==='Onderzoek afgerond'")
    count=page.evaluate('calls');page.wait_for_timeout(1300)
    assert page.evaluate('calls')==count
    assert not errors,errors
    browser.close()
print('PASS: native browser displays external phase/counters/inflight wait/phase ETA, filters phases and outcomes/HTTP status, follows browser after root completion, stops only when workflow ends.')
