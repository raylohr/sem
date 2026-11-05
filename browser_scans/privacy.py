"""Browser-only consent interactions and redacted observation metadata; no Django."""
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

CONTAINERS = '#onetrust-banner-sdk, #onetrust-pc-sdk, #CybotCookiebotDialog, .cmplz-cookiebanner, #didomi-host, [data-testid="uc-default-wall"], [role="dialog"][aria-label*="cookie" i], [role="dialog"][aria-label*="consent" i]'
CONTROLS = {
    'accept': ['#onetrust-accept-btn-handler', '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll', '.cmplz-accept', '#didomi-notice-agree-button'],
    'reject': ['#onetrust-reject-all-handler', '#reject-recommended-btn-handler', '#CybotCookiebotDialogBodyButtonDecline', '.cmplz-deny', '#didomi-notice-disagree-button'],
    'preferences': ['#onetrust-pc-btn-handler', '#ot-sdk-btn', '.cmplz-manage-consent'],
    'withdraw': ['#reject-recommended-btn-handler', '#onetrust-reject-all-handler', '#CybotCookiebotDialogBodyButtonDecline', '.cmplz-deny'],
}
LABELS = {'accept': r'^(alles accepteren|accept all|alle akzeptieren|accepteren)$',
          'reject': r'^(alles weigeren|reject all|alle ablehnen|weigeren|decline all)$',
          'withdraw': r'^(alles weigeren|reject all|alle ablehnen|intrekken|withdraw consent)$'}


def redacted_url(value):
    try:
        p = urlsplit(value)
        return urlunsplit((p.scheme, p.netloc.split('@')[-1], p.path, urlencode([(key[:100], '[redacted]') for key, _ in parse_qsl(p.query, keep_blank_values=True)[:40]]), ''))[:4096]
    except ValueError:
        return '[invalid URL]'


def safe_name(value):
    text = str(value)[:256]
    return '[redacted key]' if '@' in text or len(text) > 200 else text


def snapshot(context, page, phase, coverage=None):
    coverage = coverage if coverage is not None else {}
    items = []
    cookies = context.cookies()
    if len(cookies)>300 or len(page.frames)>20: coverage['storage_truncated'] = True
    for cookie in cookies[:300]:
        items.append({'phase': phase, 'kind': 'cookie', 'name': safe_name(cookie['name']),
            'origin': cookie['domain'], 'metadata': {key: cookie.get(key) for key in ('path', 'expires', 'httpOnly', 'secure', 'sameSite')},
            'value_omitted': True})
    for frame in page.frames[:20]:
        try:
            entries = frame.evaluate('''() => {
                const out=[]; for(const [kind, storage] of [['localStorage',localStorage],['sessionStorage',sessionStorage]]) {
                    for(let i=0;i<Math.min(storage.length,200);i++) {const name=storage.key(i);out.push({kind,name,size:(storage.getItem(name)||'').length});}
                } return {origin:location.origin,entries:out,truncated:localStorage.length>200 || sessionStorage.length>200};
            }''')
            if entries.get('truncated'): coverage['storage_truncated'] = True
            for item in entries['entries']:
                items.append({'phase': phase, 'kind': item['kind'], 'name': safe_name(item['name']), 'origin': redacted_url(entries['origin']), 'metadata': {'length': item['size']}, 'value_omitted': True})
        except Exception:
            coverage['storage_unreadable'] = True
    if len(items)>1000: coverage['storage_truncated'] = True
    return items[:1000]


def click_control(context, page, kind, selectors):
    explicit = selectors.get(kind, '')
    candidates = [explicit] if explicit else CONTROLS.get(kind, [])
    target = None
    for selector in candidates:
        try:
            found = page.locator(selector)
            visible = found.count() == 1 and found.first.is_visible()
        except Exception:
            continue
        if visible:
            target = found.first
            matched = selector
            break
    if target is None and not explicit and kind in LABELS:
        found = page.locator(CONTAINERS).get_by_role('button', name=re.compile(LABELS[kind], re.I))
        if found.count() == 1 and found.first.is_visible():
            target = found.first
            matched = 'consent container + button label'
    if target is None:
        return {'action': kind, 'status': 'not_found', 'explicit_selector': bool(explicit)}
    before = context.cookies()
    try:
        before_storage = page.evaluate('''() => JSON.stringify([Object.entries(localStorage),Object.entries(sessionStorage)])''')
        tag = target.evaluate('(e) => e.tagName.toLowerCase()')
        if tag not in ('button', 'input', 'a', 'div', 'span'):
            return {'action': kind, 'status': 'unsupported_control'}
        target.click(timeout=2000, no_wait_after=True)
        page.wait_for_timeout(400)
        after_storage = page.evaluate('''() => JSON.stringify([Object.entries(localStorage),Object.entries(sessionStorage)])''')
        changed = before != context.cookies() or before_storage != after_storage or not target.is_visible()
        return {'action': kind, 'status': 'completed' if changed or kind == 'preferences' else 'unverified', 'selector': matched, 'explicit_selector': bool(explicit)}
    except Exception:
        return {'action': kind, 'status': 'failed', 'explicit_selector': bool(explicit)}


def technology_globals(page):
    return page.evaluate('''() => {
        const out={}; for(const [key,read] of Object.entries({React:()=>window.React?.version,Vue:()=>window.Vue?.version,
            jQuery:()=>window.jQuery?.fn?.jquery,NextJS:()=>window.__NEXT_DATA__ ? 'present':null,
            Shopify:()=>window.Shopify?.shop ? 'present':null,HubSpot:()=>window._hsq ? 'present':null,
            Matomo:()=>window.Matomo ? 'present':null})) {
            try {const value=read();if(typeof value==='string' && value.length<60)out[key]=value;} catch(e){}
        } return out;
    }''')


# Async renderer variants keep the same redaction and interaction policy as the
# synchronous helpers used by offline callers. Chromium never uses a sync API.
async def async_snapshot(context, page, phase, coverage=None):
    coverage = coverage if coverage is not None else {}
    items = []
    cookies = await context.cookies()
    if len(cookies) > 300 or len(page.frames) > 20:
        coverage['storage_truncated'] = True
    for cookie in cookies[:300]:
        items.append({'phase': phase, 'kind': 'cookie', 'name': safe_name(cookie['name']), 'origin': cookie['domain'], 'metadata': {key: cookie.get(key) for key in ('path', 'expires', 'httpOnly', 'secure', 'sameSite')}, 'value_omitted': True})
    for frame in page.frames[:20]:
        try:
            entries = await frame.evaluate("() => {\n                const out=[]; for(const [kind, storage] of [['localStorage',localStorage],['sessionStorage',sessionStorage]]) {\n                    for(let i=0;i<Math.min(storage.length,200);i++) {const name=storage.key(i);out.push({kind,name,size:(storage.getItem(name)||'').length});}\n                } return {origin:location.origin,entries:out,truncated:localStorage.length>200 || sessionStorage.length>200};\n            }")
            if entries.get('truncated'):
                coverage['storage_truncated'] = True
            for item in entries['entries']:
                items.append({'phase': phase, 'kind': item['kind'], 'name': safe_name(item['name']), 'origin': redacted_url(entries['origin']), 'metadata': {'length': item['size']}, 'value_omitted': True})
        except Exception:
            coverage['storage_unreadable'] = True
    if len(items) > 1000:
        coverage['storage_truncated'] = True
    return items[:1000]


async def async_click_control(context, page, kind, selectors):
    explicit = selectors.get(kind, '')
    candidates = [explicit] if explicit else CONTROLS.get(kind, [])
    target = None
    for selector in candidates:
        try:
            found = page.locator(selector)
            visible = await found.count() == 1 and await found.first.is_visible()
        except Exception:
            continue
        if visible:
            target = found.first
            matched = selector
            break
    if target is None and (not explicit) and (kind in LABELS):
        found = page.locator(CONTAINERS).get_by_role('button', name=re.compile(LABELS[kind], re.I))
        if await found.count() == 1 and await found.first.is_visible():
            target = found.first
            matched = 'consent container + button label'
    if target is None:
        return {'action': kind, 'status': 'not_found', 'explicit_selector': bool(explicit)}
    before = await context.cookies()
    try:
        before_storage = await page.evaluate('() => JSON.stringify([Object.entries(localStorage),Object.entries(sessionStorage)])')
        tag = await target.evaluate('(e) => e.tagName.toLowerCase()')
        if tag not in ('button', 'input', 'a', 'div', 'span'):
            return {'action': kind, 'status': 'unsupported_control'}
        await target.click(timeout=2000, no_wait_after=True)
        await page.wait_for_timeout(400)
        after_storage = await page.evaluate('() => JSON.stringify([Object.entries(localStorage),Object.entries(sessionStorage)])')
        changed = before != await context.cookies() or before_storage != after_storage or (not await target.is_visible())
        return {'action': kind, 'status': 'completed' if changed or kind == 'preferences' else 'unverified', 'selector': matched, 'explicit_selector': bool(explicit)}
    except Exception:
        return {'action': kind, 'status': 'failed', 'explicit_selector': bool(explicit)}


async def async_technology_globals(page):
    return await page.evaluate("() => {\n        const out={}; for(const [key,read] of Object.entries({React:()=>window.React?.version,Vue:()=>window.Vue?.version,\n            jQuery:()=>window.jQuery?.fn?.jquery,NextJS:()=>window.__NEXT_DATA__ ? 'present':null,\n            Shopify:()=>window.Shopify?.shop ? 'present':null,HubSpot:()=>window._hsq ? 'present':null,\n            Matomo:()=>window.Matomo ? 'present':null})) {\n            try {const value=read();if(typeof value==='string' && value.length<60)out[key]=value;} catch(e){}\n        } return out;\n    }")
