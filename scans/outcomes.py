"""Assess transport completeness without treating genuine 404s as crawl failures."""
from collections import Counter


def summarize(records):
    total = responses = failures = blocked = server_errors = robots = http_errors = 0
    codes = Counter()
    redirects = 0
    for record in records:
        data = record.get('data', {})
        total += 1
        if data.get('blocked_redirect'):
            redirects += 1
        if data.get('robots_blocked'):
            robots += 1
        elif data.get('error'):
            failures += 1
            code = data.get('error_code') or ('legacy_http_error' if str(data['error']).startswith('HTTP-opvraag mislukt') else 'scan_error')
            codes[code] += 1
        status = data.get('final_status',data.get('status'))
        if isinstance(status,int) and 100 <= status <= 599:
            responses += 1
            http_errors += status >= 400
            if status in (403,429):
                blocked += 1; codes[f'http_{status}'] += 1
            elif status >= 500:
                server_errors += 1; codes[f'http_{status}'] += 1
    return {'total':total,'http_responses':responses,'request_failures':failures,
            'blocked_responses':blocked,'server_errors':server_errors,'http_errors':http_errors,
            'robots_blocked':robots,'blocked_redirects':redirects,'errors_by_code':dict(codes),
            'incomplete':bool(failures or blocked or server_errors or redirects)}


def outcome_warning(summary):
    n = lambda value: format(value,',').replace(',','.')
    return (f"Onvolledige scan: {n(summary['request_failures'])} mislukte downloads, "
            f"{n(summary['blocked_responses'])} HTTP 403/429-antwoorden en "
            f"{n(summary['server_errors'])} serverfouten en {n(summary.get('blocked_redirects',0))} geblokkeerde redirects. Opgehaalde resultaten zijn bewaard. "
            "Dit resultaat bewijst geen volledige dekking van de website. Start na onderzoek zo nodig een nieuwe scan.")
