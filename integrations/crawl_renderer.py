"""Browser rendering in the credential-free browser service.

Capture objects contain transient HTML/text. Step 2 returns execution metadata;
permanent HTML artifacts and inventory publication belong to step 3.
"""
from dataclasses import dataclass, field
from hashlib import sha256
import re
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

from .playwright_config import DESKTOP_PROFILES, in_scope, is_excluded
from .crawl_http import RobotsRules, safe_http_url, retry_after_seconds

MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_LINKS = 10000


def desktop_user_agent(profile, version):
    match = re.match(r'\d+(?:\.\d+)*', version)
    if not match:
        raise ValueError('Unknown browser version')
    version = match.group()
    platform = {'windows':'Windows NT 10.0; Win64; x64', 'macos':'Macintosh; Intel Mac OS X 10_15_7',
                'linux':'X11; Linux x86_64', 'chromeos':'X11; CrOS x86_64'}[profile.platform]
    if profile.family == 'firefox':
        major = version.split('.')[0] + '.0'
        return f'Mozilla/5.0 ({platform}; rv:{major}) Gecko/20100101 Firefox/{version}'
    if profile.family == 'safari':
        return f'Mozilla/5.0 ({platform}) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/{version} Safari/605.1.15'
    value = f'Mozilla/5.0 ({platform}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36'
    return value + (f' Edg/{version}' if profile.family == 'edge' else '')


@dataclass
class PageCapture:
    url: str
    final_url: str = ''
    http_status: int | None = None
    server_html: bytes = b''
    rendered_html: str = ''
    text: str = ''
    links: list = field(default_factory=list)
    redirects: list = field(default_factory=list)
    blocked_resources: int = 0
    failed_resources: int = 0
    render_complete: bool = False
    elapsed_seconds: float = 0
    ttfb_seconds: float = 0
    retry_after: int = 0
    error: str = ''
    user_agent: str = ''
    browser_version: str = ''
    headers: dict = field(default_factory=dict)
    links_truncated: bool = False
    title: str = ''
    blocked_navigation: str = ''

    def metadata(self):
        return {name:getattr(self,name) for name in ('final_url','http_status','links','redirects',
            'blocked_resources','failed_resources','render_complete','elapsed_seconds','ttfb_seconds',
            'retry_after','error','user_agent','browser_version','headers','links_truncated','title','blocked_navigation')} | {
                'server_html_bytes':len(self.server_html),
                'rendered_html_bytes':len(self.rendered_html.encode()), 'text_length':len(self.text),
                'server_html_sha256':sha256(self.server_html).hexdigest(),
                'rendered_html_sha256':sha256(self.rendered_html.encode()).hexdigest()}


def manifest_browser_version(engine):
    import json
    from pathlib import Path
    import playwright
    manifest = Path(playwright.__file__).parent/'driver'/'package'/'browsers.json'
    browsers = json.loads(manifest.read_text())['browsers']
    return next(item['browserVersion'] for item in browsers if item['name']==engine)


def render_page(url, configuration, profile_key, proxy, navigation_gate=None):
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    url = safe_http_url(url)
    profile = DESKTOP_PROFILES[profile_key]
    scope = SimpleNamespace(domain=configuration['domain'], website=configuration['website'],
                            include_subdomains=configuration['include_subdomains'])
    allowed = lambda value: in_scope(scope,value) and not is_excluded(value,
        configuration.get('excluded_hosts',[]),configuration.get('excluded_paths',[]))
    if not allowed(url):
        raise ValueError('URL outside configured scope')
    timeout = max(5,min(120,int(configuration.get('render_timeout_seconds',30))))
    settle = max(1,min(10,int(configuration.get('settle_seconds',2))))
    capture = PageCapture(url=url)
    started = time.monotonic()
    main_responses = []
    requests = [0]
    with sync_playwright() as playwright:
        browser_type = getattr(playwright,profile.engine)
        launch = {'headless':True,'proxy':{'server':proxy},'timeout':30000}
        if profile.engine == 'chromium':
            launch['args'] = ['--proxy-bypass-list=<-loopback>','--disable-background-networking',
                              '--disable-component-update','--disable-sync','--force-webrtc-ip-handling-policy=disable_non_proxied_udp']
        if profile.engine == 'firefox':
            launch['firefox_user_prefs'] = {'network.proxy.allow_hijacking_localhost':True,
                                           'media.peerconnection.enabled':False}
        browser = browser_type.launch(**launch)
        try:
            capture.browser_version = browser.version
            capture.user_agent = desktop_user_agent(profile,browser.version)
            context = browser.new_context(user_agent=capture.user_agent,viewport={'width':1440,'height':900},
                locale='nl-NL',timezone_id='Europe/Amsterdam',is_mobile=False,has_touch=False,
                service_workers='block',accept_downloads=False)
            platform = {'windows':'Win32','macos':'MacIntel','linux':'Linux x86_64','chromeos':'Linux x86_64'}[profile.platform]
            context.add_init_script('Object.defineProperty(navigator,"platform",{get:()=>'+repr(platform)+'});')
            page = context.new_page()
            page.set_default_timeout(timeout*1000)
            if profile.engine == 'chromium':
                major = browser.version.split('.')[0]
                brand = 'Microsoft Edge' if profile.family == 'edge' else 'Google Chrome'
                cdp = context.new_cdp_session(page)
                cdp.send('Emulation.setUserAgentOverride',{'userAgent':capture.user_agent,'acceptLanguage':'nl-NL,nl;q=0.9,en;q=0.8',
                    'platform':platform,'userAgentMetadata':{'brands':[{'brand':'Chromium','version':major},{'brand':brand,'version':major}],
                    'fullVersionList':[{'brand':'Chromium','version':browser.version},{'brand':brand,'version':browser.version}],
                    'platform':{'windows':'Windows','macos':'macOS','linux':'Linux','chromeos':'Chrome OS'}[profile.platform],
                    'platformVersion':'','architecture':'x86','bitness':'64','model':'','mobile':False}})
            context.on('page',lambda popup: popup.close() if popup != page else None)
            page.on('download',lambda download:download.cancel())
            page.on('requestfailed',lambda request:setattr(capture,'failed_resources',capture.failed_resources+1))
            context.route_web_socket('**/*',lambda socket:socket.close())

            def route_request(route):
                request = route.request
                requests[0] += 1
                try:
                    safe_http_url(request.url)
                except Exception:
                    capture.blocked_resources += 1
                    route.abort()
                    return
                document = request.is_navigation_request() and request.resource_type == 'document'
                permitted = True
                if requests[0] > 200 or request.resource_type == 'media' or request.method not in ('GET','HEAD','POST','OPTIONS'):
                    permitted = False
                if document:
                    if not allowed(request.url):
                        permitted = False
                        capture.blocked_navigation = request.url
                    elif configuration.get('respect_robots'):
                        robot = configuration.get('robots',{}).get(urlsplit(request.url).netloc)
                        if not robot or robot.get('unreachable') or not RobotsRules(robot.get('text','')).allowed(capture.user_agent,request.url):
                            permitted = False
                            if request.frame == page.main_frame:
                                capture.blocked_navigation = request.url
                                capture.error = 'robots_unknown' if not robot else 'robots_blocked'
                    if request.frame == page.main_frame:
                        capture.redirects.append(request.url)
                if permitted:
                    route.continue_()
                else:
                    capture.blocked_resources += 1
                    route.abort()
            context.route('**/*',route_request)

            def response_received(response):
                if response.request.is_navigation_request() and response.frame == page.main_frame:
                    main_responses.append(response)
            page.on('response',response_received)
            try:
                if navigation_gate:
                    navigation_gate(configuration['domain'], float(configuration.get('effective_rate',configuration.get('pages_per_second',1))))
                started = time.monotonic()
                page.goto(url,wait_until='domcontentloaded',timeout=timeout*1000)
                remaining = max(1,int((timeout-(time.monotonic()-started))*1000))
                try:
                    page.wait_for_load_state('load',timeout=min(remaining,10000))
                    loaded = True
                except BrowserTimeout:
                    loaded = False
                if configuration.get('scroll_for_lazy_content'):
                    for _ in range(10):
                        if time.monotonic()-started >= timeout-settle:
                            break
                        end = page.evaluate('()=>{window.scrollBy(0,window.innerHeight);return window.scrollY+window.innerHeight>=document.documentElement.scrollHeight}')
                        page.wait_for_timeout(150)
                        if end:
                            break
                remaining = max(0,timeout-(time.monotonic()-started))
                page.wait_for_timeout(min(settle,remaining)*1000)
                capture.render_complete = loaded and time.monotonic()-started < timeout
            except BrowserTimeout:
                capture.error = 'timeout'
            except Exception:
                if not capture.error:
                    capture.error = 'external_redirect' if capture.blocked_navigation else 'navigation_failed'
            capture.final_url = page.url if allowed(page.url) else url
            if main_responses:
                response = main_responses[-1]
                capture.http_status = response.status
                headers = response.all_headers()
                capture.headers = {key:value for key,value in headers.items() if key in
                    ('content-type','content-length','x-robots-tag','location','cache-control')}
                capture.retry_after = retry_after_seconds(headers.get('retry-after'))
                timing = response.request.timing
                capture.ttfb_seconds = max(0,(timing.get('responseStart',0)-timing.get('requestStart',0))/1000)
                if 'html' in headers.get('content-type','').lower() and int(headers.get('content-length','0') or 0) <= MAX_HTML_BYTES:
                    try:
                        body = response.body()
                        if len(body) <= MAX_HTML_BYTES:
                            capture.server_html = body
                        else:
                            capture.error = 'page_too_large'
                    except Exception:
                        pass
                elif 'html' not in headers.get('content-type','').lower():
                    capture.error = 'non_html'
            if allowed(page.url) and capture.error not in ('page_too_large','non_html'):
                try:
                    html = page.content()
                    if len(html.encode()) > MAX_HTML_BYTES:
                        capture.error = 'page_too_large'
                    else:
                        capture.rendered_html = html
                        capture.text = page.locator('body').inner_text(timeout=2000)
                        capture.title = page.title()[:1000]
                        links = page.locator('a[href]').evaluate_all('(items)=>items.slice(0,10001).map(a=>a.href)')
                        capture.links_truncated = len(links)>MAX_LINKS
                        link_bytes = 0
                        for value in dict.fromkeys(links[:MAX_LINKS]):
                            if not isinstance(value,str) or len(value)>8192 or not value.startswith(('http://','https://')):
                                continue
                            link_bytes += len(value.encode())
                            if link_bytes > 1024*1024:
                                capture.links_truncated = True
                                break
                            capture.links.append(value)
                except Exception:
                    if not capture.error:
                        capture.error = 'render_incomplete'
            capture.elapsed_seconds = round(time.monotonic()-started,3)
            if capture.error or not capture.rendered_html:
                capture.render_complete = False
            context.close()
        finally:
            browser.close()
    return capture
