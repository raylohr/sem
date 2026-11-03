"""Trusted network broker; the browser itself has no IP networking."""
import base64
import hashlib
import socket
import time
from django.conf import settings
from scans.network import SafeClient, ScanError, ScanBudgetExceeded, normalize_url, USER_AGENT
from .protocol import send, receive
from .privacy import redacted_url

RESPONSE_HEADERS = {'server', 'x-powered-by', 'x-generator', 'via', 'x-cache', 'content-type', 'location', 'cache-control', 'expires', 'etag', 'last-modified',
    'x-robots-tag', 'link', 'content-security-policy', 'content-security-policy-report-only',
    'x-frame-options', 'x-content-type-options', 'access-control-allow-origin',
    'access-control-allow-credentials', 'cross-origin-resource-policy', 'cross-origin-opener-policy'}


def render(url, config, *, check_access, batch_id, transport_factory=SafeClient):
    hosts = set(config['allowed_hosts'])
    resource_hosts = hosts | set(config.get('resource_hosts', []))
    normalize_url(url, hosts)
    client = transport_factory(resource_hosts, check_access=check_access,
        duration=config['timeout_ms']/1000+5, delay=1/config['requests_per_second'],
        max_transfer=config['max_bytes'], document_sink=[], respect_robots=config['respect_robots'],
        shared_rate=True, batch_id=batch_id)
    connection = socket.socket(socket.AF_UNIX)
    connection.settimeout(95)
    try:
        connection.connect(settings.BROWSER_SOCKET)
        with connection.makefile('rwb', buffering=0) as stream:
            send(stream, {**{key: config[key] for key in ('timeout_ms', 'settle_ms', 'scroll_steps', 'profile', 'max_requests')}, 'url': url, 'user_agent': USER_AGENT, 'privacy_enabled': bool(config.get('privacy_enabled')), 'scenario': config.get('scenario', 'baseline'), 'consent_selectors': config.get('consent_selectors', {})})
            count = 0
            main_result = None
            main_chain = []
            exhausted = False
            while True:
                message = receive(stream)
                check_access()
                if message.get('type') == 'result':
                    if normalize_url(message['final_url'], hosts) != normalize_url(message['final_url']):
                        raise ScanError('Ongeldige finale browser-URL.')
                    if config.get('privacy_enabled'):
                        for resource in message.get('resources', []):
                            resource['url'] = redacted_url(resource.get('url', ''))
                            resource['url_redacted'] = True
                        for blocked in message.get('blocked', []):
                            blocked['url'] = redacted_url(blocked.get('url', ''))
                        message['errors'] = [{'kind': item.get('kind'), 'message': 'Foutmelding geredigeerd bij privacyonderzoek.'} for item in message.get('errors', [])]
                    message['server_response'] = main_result
                    message['user_agent'] = USER_AGENT
                    message['transport'] = {'bytes': client.transferred, 'decoded_bytes': client.decoded_bytes,
                        'policy': 'public DNS-pinned GET; authorization never forwarded; browser network=none',
                        'ephemeral_cookies_forwarded': bool(config.get('privacy_enabled'))}
                    return message
                if message.get('type') != 'request':
                    raise ScanError('Browseronderzoek mislukt: ' + str(message.get('code', 'protocol')))
                count += 1
                response = {'allowed': False, 'reason': 'blocked'}
                try:
                    if exhausted:
                        raise ScanBudgetExceeded('transfer_budget')
                    if count > config['max_requests']:
                        raise ScanBudgetExceeded('request_budget')
                    if message.get('method') != 'GET':
                        raise ScanError('method_not_allowed')
                    requested = normalize_url(message.get('url'), hosts if message.get('main_navigation') else resource_hosts)
                    cookie_header = message.get('cookie_header', '') if config.get('privacy_enabled') else ''
                    result = client.fetch_once(requested, cookie_header=cookie_header) if cookie_header else client.fetch_once(requested)
                    # Only retain the main document body; resource responses remain bounded ephemeral input.
                    if message.get('main_navigation'):
                        main_chain.append({'url': result.url, 'status': result.status, 'destination': result.headers.get('location'), 'type': 'http' if result.status in (301,302,303,307,308) else 'browser_navigation'})
                        main_result = {'url': result.url, 'status': result.status, 'headers': {k: v for k, v in result.headers.items() if k in RESPONSE_HEADERS},
                            'body': base64.b64encode(result.body).decode(), 'googlebot_allowed': result.googlebot_allowed,
                            'robots_allowed': result.robots_allowed, 'initial_status': main_chain[0]['status'], 'redirects': list(main_chain[:-1])}
                    response = {'allowed': True, 'status': result.status, 'headers': {k: v for k, v in result.headers.items() if k in RESPONSE_HEADERS},
                        'body': base64.b64encode(result.body).decode(), 'bytes': len(result.body),
                        'fetch_ms': round(result.elapsed*1000, 2), 'sha256': hashlib.sha256(result.body).hexdigest()}
                    if config.get('privacy_enabled') and result.set_cookies:
                        response['headers']['set-cookie'] = '\n'.join(result.set_cookies[:100])
                    # Discard transport's raw-resource sink promptly, retaining only main_result.
                    client.documents.clear()
                except ScanBudgetExceeded as exc:
                    exhausted = True
                    response = {'allowed': False, 'reason': str(exc)[:160]}
                except ScanError as exc:
                    response = {'allowed': False, 'reason': str(exc)[:160]}
                send(stream, response)
    finally:
        client.close()
        connection.close()
