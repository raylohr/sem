"""No Django, credentials, media, or network. HTTP is fulfilled through Unix IPC."""
import base64
import hashlib
import multiprocessing
import os
from pathlib import Path
import socket
import signal
import time
try:
    from .enhancements import explore, IMAGE_SNAPSHOT, title_width
    from .protocol import send, receive
    from .privacy import snapshot, click_control, technology_globals, redacted_url
except ImportError:
    from enhancements import explore, IMAGE_SNAPSHOT, title_width
    from protocol import send, receive
    from privacy import snapshot, click_control, technology_globals, redacted_url

SOCKET = os.environ.get('BROWSER_SOCKET', '/run/sem-browser/renderer.sock')
MAX_ARTIFACT = 5 * 1024 * 1024

INIT = '''() => {
  window.__semPerf = {lcp: null, cls: 0};
  try { new PerformanceObserver(list => { for(const e of list.getEntries()) window.__semPerf.lcp=e.startTime; }).observe({type:'largest-contentful-paint',buffered:true}); } catch(e) {}
  try { new PerformanceObserver(list => { for(const e of list.getEntries()) if(!e.hadRecentInput) window.__semPerf.cls += e.value; }).observe({type:'layout-shift',buffered:true}); } catch(e) {}
}'''


def render(connection):
    os.setsid()
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    stream = connection.makefile('rwb', buffering=0)
    try:
        config = receive(stream)
        timeout = min(60000, max(5000, int(config.get('timeout_ms', 30000))))
        profile = config.get('profile', 'desktop')
        viewport = {'width': 390, 'height': 844} if profile == 'mobile' else {'width': 1440, 'height': 900}
        errors, blocked, resources = [], [], []
        privacy_enabled = bool(config.get('privacy_enabled'))
        scenario = config.get('scenario', 'baseline')
        privacy = {'scenario': scenario, 'actions': [], 'snapshots': [], 'complete': True}
        phase = 'before_choice' if privacy_enabled else 'baseline'
        started = time.monotonic()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, chromium_sandbox=True,
                args=['--disable-background-networking', '--disable-component-update', '--disable-sync', '--disable-quic', '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
            context = browser.new_context(viewport=viewport, device_scale_factor=1,
                is_mobile=profile == 'mobile', has_touch=profile == 'mobile', locale='nl-NL', timezone_id='Europe/Amsterdam',
                user_agent=config['user_agent'], service_workers='block', accept_downloads=False)
            page = context.new_page()
            page.set_default_timeout(5000)
            page.add_init_script('(' + INIT + ')()')
            def block_websocket(ws):
                if len(blocked) < 200:
                    blocked.append({'url': ws.url[:4096], 'reason': 'websocket_not_supported'})
                ws.close()
            context.route_web_socket('**/*', block_websocket)
            context.on('page', lambda popup: popup.close() if popup != page else None)
            page.on('pageerror', lambda error: errors.append({'kind': 'javascript', 'message': str(error)[:500]}) if len(errors) < 30 else None)
            page.on('console', lambda message: errors.append({'kind': 'console', 'message': message.text[:500]}) if message.type == 'error' and len(errors) < 30 else None)
            def route(request_route):
                request = request_route.request
                if len(resources) >= config.get('max_requests', 100) or time.monotonic()-started > timeout/1000:
                    if len(blocked) < 200:
                        blocked.append({'url': request.url[:4096], 'reason': 'browser_budget'})
                    request_route.abort()
                    return
                if len(request.url) > 4096:
                    if len(blocked) < 200:
                        blocked.append({'url': request.url[:4096], 'reason': 'url_limit', 'url_truncated': True})
                    request_route.abort()
                    return
                main = request.is_navigation_request() and request.frame == page.main_frame
                entry = {'url': request.url[:4096], 'method': request.method, 'resource_type': request.resource_type,
                         'main_navigation': main, 'phase': phase, 'start_ms': round((time.monotonic()-started)*1000, 2)}
                resources.append(entry)
                packet = {'type': 'request', **entry}
                if privacy_enabled:
                    packet['cookie_header'] = request.all_headers().get('cookie', '')
                send(stream, packet)
                response = receive(stream)
                entry.update({key: response.get(key) for key in ('status', 'bytes', 'fetch_ms', 'reason', 'sha256')})
                entry['content_type'] = response.get('headers', {}).get('content-type')
                if not response.get('allowed'):
                    blocked.append({'url': entry['url'], 'reason': response.get('reason', 'blocked')})
                    request_route.abort()
                else:
                    request_route.fulfill(status=response['status'], headers=response.get('headers', {}), body=base64.b64decode(response['body']))
                entry['end_ms'] = round((time.monotonic()-started)*1000, 2)
            context.route('**/*', route)
            timed_out = False
            exploration={'links':[],'phases':[],'links_truncated':True}
            try:
                page.goto(config['url'], wait_until='domcontentloaded', timeout=timeout)
                page.wait_for_timeout(min(1500, max(100, config.get('settle_ms', 750))))
                exploration=explore(page,config) if not privacy_enabled else {'links':[],'phases':[],'scope':'consent scenarios: no baseline exploration'}
            except PlaywrightTimeout:
                timed_out = True
            except Exception:
                errors.append({'kind': 'navigation', 'message': 'Navigatie niet volledig uitgevoerd.'})
            if privacy_enabled:
                privacy['snapshots'].extend(snapshot(context, page, 'before_choice', privacy))
                selectors = config.get('consent_selectors', {})
                if scenario in ('accept', 'withdraw'):
                    phase = 'after_accept'
                    action = click_control(context, page, 'accept', selectors)
                    privacy['actions'].append({**action, 'at_ms': round((time.monotonic()-started)*1000)})
                    privacy['snapshots'].extend(snapshot(context, page, phase, privacy))
                if scenario == 'reject':
                    phase = 'after_reject'
                    privacy['actions'].append({**click_control(context, page, 'reject', selectors), 'at_ms': round((time.monotonic()-started)*1000)})
                    privacy['snapshots'].extend(snapshot(context, page, phase, privacy))
                if scenario == 'withdraw' and privacy['actions'][-1]['status'] == 'completed':
                    phase = 'preferences'
                    privacy['actions'].append({**click_control(context, page, 'preferences', selectors), 'at_ms': round((time.monotonic()-started)*1000)})
                    phase = 'after_withdraw'
                    privacy['actions'].append({**click_control(context, page, 'withdraw', selectors), 'at_ms': round((time.monotonic()-started)*1000)})
                    privacy['snapshots'].extend(snapshot(context, page, phase, privacy))
                privacy['complete'] = all(item['status'] == 'completed' for item in privacy['actions'])
            globals_found = technology_globals(page)
            policies = page.locator('a[href]').evaluate_all("nodes => nodes.filter(a => /privacy|cookie|gegevensbescherming/i.test(a.textContent || '')).slice(0,20).map(a=>({url:a.href,label:(a.textContent||'').slice(0,200)}))")
            html = page.content().encode()
            if len(html) > MAX_ARTIFACT:
                raise ValueError('Gerenderde DOM overschrijdt 5 MiB.')
            metrics = page.evaluate('''() => {
                const n=performance.getEntriesByType('navigation')[0]; const paint=performance.getEntriesByName('first-contentful-paint')[0];
                return {dom_content_loaded_ms:n && n.domContentLoadedEventEnd || null, load_ms:n && n.loadEventEnd || null,
                    fcp_ms:paint ? paint.startTime : null, lcp_ms:window.__semPerf?.lcp ?? null,
                    cls:window.__semPerf?.cls ?? null, document_height:document.documentElement.scrollHeight};
            }''')
            image_count = page.locator('img').count()
            images=page.evaluate(IMAGE_SNAPSHOT)
            try:width_estimate=title_width(browser,page.title())
            except Exception:width_estimate={'status':'unknown','model':'sem-title-width-v1','width_px':None}
            axe_source = Path('vendor/axe.min.js').read_text()
            page.evaluate(axe_source)
            a11y = page.evaluate('''async () => {
                const r=await axe.run(document, {resultTypes:['violations','incomplete']});
                return {version:axe.version, violations:r.violations.map(v => ({id:v.id,impact:v.impact,description:v.description,
                    help:v.help,helpUrl:v.helpUrl,tags:v.tags,node_count:v.nodes.length,nodes_truncated:v.nodes.length>10,nodes:v.nodes.slice(0,10).map(n=>({target:n.target,html:n.html.slice(0,2000),failureSummary:n.failureSummary}))})),
                    incomplete:r.incomplete.map(v=>({id:v.id,help:v.help,nodes:v.nodes.length}))};
            }''')
            if privacy_enabled:
                privacy['snapshots'] = [item for item in privacy['snapshots'] if item['phase'] != phase] + snapshot(context, page, phase, privacy)
                if privacy.get('storage_truncated') or privacy.get('storage_unreadable'):
                    privacy['complete'] = False
                privacy['observed_until_ms'] = round((time.monotonic()-started)*1000)
            page.evaluate('window.scrollTo(0,0)')
            screenshot = page.screenshot(type='png', full_page=False, timeout=5000)
            if len(screenshot) > MAX_ARTIFACT:
                raise ValueError('Screenshot overschrijdt 5 MiB.')
            send(stream, {'type': 'result', 'final_url': page.url, 'dom': base64.b64encode(html).decode(),
                'exploration':exploration,'title_width':width_estimate,'screenshot': base64.b64encode(screenshot).decode(), 'metrics': metrics, 'images': images, 'image_count': image_count,
                'accessibility': a11y, 'resources': resources, 'errors': errors, 'blocked': blocked,
                'privacy': privacy, 'technology_globals': globals_found, 'policy_links': policies,
                'timed_out': timed_out, 'browser_version': browser.version, 'viewport': viewport,
                'duration_ms': round((time.monotonic()-started)*1000), 'axe_sha256': hashlib.sha256(axe_source.encode()).hexdigest()})
            browser.close()
    except Exception as exc:
        try:
            send(stream, {'type': 'error', 'code': type(exc).__name__, 'diagnostic': str(exc)[:1500], 'message': 'Browseronderzoek kon niet volledig worden uitgevoerd.'})
        except Exception:
            pass
    finally:
        stream.close()
        connection.close()


def main():
    Path(SOCKET).parent.mkdir(parents=True, exist_ok=True)
    Path(SOCKET).unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX)
    server.bind(SOCKET)
    os.chmod(SOCKET, 0o660)
    server.listen(4)
    while True:
        connection, _ = server.accept()
        process = multiprocessing.Process(target=render, args=(connection,))
        process.start()
        connection.close()
        process.join(100)
        if process.is_alive():
            os.killpg(process.pid, signal.SIGKILL)
            process.join()
            # PID-namespace teardown also kills browser processes in detached groups.
            # Docker restarts only this credential-free, networkless renderer.
            os._exit(1)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


if __name__ == '__main__':
    main()
