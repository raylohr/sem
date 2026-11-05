"""Networkless renderer helpers. All browser evaluations remain bounded."""
import base64
import hashlib
from pathlib import Path

IMAGE_SNAPSHOT=r'''() => {
 const path=n=>{let p=[];for(let d=0;n&&n.nodeType===1&&d<20;d++,n=n.parentElement){let i=1;for(let s=n.previousElementSibling;s;s=s.previousElementSibling)if(s.tagName===n.tagName)i++;p.unshift(n.tagName.toLowerCase()+'['+i+']');}return '/'+p.join('/');};
 return Array.from(document.images).slice(0,500).map(n=>{const r=n.getBoundingClientRect(),s=getComputedStyle(n),visible=n.getClientRects().length>0&&s.display!=='none';
 return {url:(n.currentSrc||n.src).slice(0,4096),src:n.src.slice(0,4096),current_src:n.currentSrc.slice(0,4096),element_path:path(n),
 alt:n.getAttribute('alt')?.slice(0,500)??null,width:n.getAttribute('width'),height:n.getAttribute('height'),
 natural_width:n.naturalWidth>0?n.naturalWidth:null,natural_height:n.naturalHeight>0?n.naturalHeight:null,
 rendered_width:visible?r.width:null,rendered_height:visible?r.height:null,complete:n.complete,loaded:n.complete&&n.naturalWidth>0,
 loading:n.loading,srcset:n.getAttribute('srcset')?.slice(0,4096)??null,sizes:n.getAttribute('sizes')?.slice(0,1024)??null,
 device_pixel_ratio:window.devicePixelRatio,viewport:{width:innerWidth,height:innerHeight},scroll_y:scrollY,
 css:{display:s.display,visibility:s.visibility,object_fit:s.objectFit,object_position:s.objectPosition,aspect_ratio:s.aspectRatio}};});}'''

LINKS=r'''() => Array.from(document.querySelectorAll('a[href]')).slice(0,2000).map(a=>({url:a.href.slice(0,4096),text:(a.textContent||'').trim().slice(0,200)}))'''


def explore(page,config):
    """Scroll and expand only native details; never arbitrary buttons or forms."""
    import time
    start=time.monotonic();budget=min(8000,max(500,int(config.get('exploration_ms',4000))))
    phases=[];candidates=[];seen=set();limit=False
    before=page.evaluate(IMAGE_SNAPSHOT)
    def collect(phase):
        nonlocal limit
        total=page.locator('a[href]').count()
        if total>2000:limit=True
        for link in page.evaluate(LINKS):
            if link['url'] in seen:continue
            seen.add(link['url']);candidates.append({**link,'phase':phase})
            if len(candidates)>=2000:limit=True;break
        phases.append(phase)
    collect('before_scroll')
    positions=[];opened=0
    for step in range(min(10,max(0,int(config.get('scroll_steps',6))))):
        if (time.monotonic()-start)*1000>=budget:limit=True;break
        position=page.evaluate('''() => {const before=scrollY;window.scrollBy(0,Math.max(1,innerHeight*.9));return {before,after:scrollY,height:document.documentElement.scrollHeight};}''')
        positions.append(position);page.wait_for_timeout(250)
        collect('after_scroll')
        if position['before']==position['after']:break
    if config.get('expand_details',True):
        # Native details toggles are the sole supported non-consent interaction.
        for index in range(min(5,page.locator('details:not([open])').count())):
            if (time.monotonic()-start)*1000>=budget:limit=True;break
            page.locator('details:not([open])').first.evaluate("n=>{n.open=true;}")
            opened+=1;page.wait_for_timeout(100);collect('after_details')
    bottom=page.evaluate('scrollY+innerHeight>=document.documentElement.scrollHeight-2')
    return {'details_opened':opened,'scroll_steps_executed':len(positions),'scroll_complete':bool(bottom),'phases':phases,'links':candidates[:2000],'links_truncated':limit,'scroll_positions':positions,
            'duration_ms':round((time.monotonic()-start)*1000),'before_images':before,
            'scope':'bounded scrolling and native details only; no arbitrary buttons, form submission or navigation clicks'}


def title_width(browser,text):
    font=Path('vendor/title-font/DejaVuSans.ttf').read_bytes()
    result={'model':'sem-title-width-v1','font_sha256':hashlib.sha256(font).hexdigest(),'font':'DejaVu Sans (vendored)',
            'font_size_px':20,'reference_width_px':600,'text':text[:4096],
            'basis':'fixed-font canvas estimate; no Google display guarantee','status':'unknown','width_px':None}
    if not text.strip():
        result['status']='not_applicable';return result
    if len(text)>4096:return result
    context=browser.new_context(service_workers='block')
    try:
        page=context.new_page();page.route('**/*',lambda route:route.abort())
        width=page.evaluate('''async ({font,text})=>{const bytes=Uint8Array.from(atob(font),c=>c.charCodeAt(0));const face=new FontFace('SEMTitleV1',bytes);await face.load();document.fonts.add(face);await document.fonts.ready;const ctx=document.createElement('canvas').getContext('2d');ctx.font='20px SEMTitleV1';ctx.fontKerning='normal';return ctx.measureText(text).width;}''',{'font':base64.b64encode(font).decode(),'text':text})
        result.update(status='measured',width_px=round(width,2))
    finally:context.close()
    return result
