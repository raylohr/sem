"""Safe error evidence: no raw exception messages, URL paths, queries or bodies."""
import errno
import hashlib
import logging
import socket
import ssl
from urllib.parse import urlsplit
import urllib3

logger = logging.getLogger('scans.network')
MESSAGES = {
    'interrupted':'Poging onderbroken; geen bewezen eindantwoord.',
    'retry_exhausted':'Maximaal aantal pogingen bereikt na een onderbreking.',
    'budget_exhausted':'Netwerkbudget verlopen.',
    'dns_error': 'DNS-opzoeking mislukt.',
    'unsafe_dns': 'DNS verwijst naar een niet-toegestaan netwerkadres.',
    'connect_timeout': 'Verbindingstime-out bij ophalen van de pagina.',
    'read_timeout': 'Leestime-out bij ophalen van de pagina.',
    'tls_error': 'TLS- of certificaatfout bij ophalen van de pagina.',
    'connection_reset': 'De verbinding is verbroken door de andere kant.',
    'connection_refused': 'De verbinding is geweigerd.',
    'connection_error': 'Verbinding met de website mislukt.',
    'response_decode_error': 'Het HTTP-antwoord kon niet worden uitgepakt.',
    'unexpected_error': 'Interne fout tijdens verwerking van het HTTP-antwoord.',
}


def describe_exception(exc, stage):
    chain, pending, seen = [], [exc], set()
    while pending and len(chain) < 12:
        current = pending.pop(0)
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current)); chain.append(current)
        pending.extend([current.__cause__, current.__context__, getattr(current, 'reason', None)])
        pending.extend(value for value in current.args if isinstance(value, BaseException))
    def has(types): return any(isinstance(value, types) for value in chain)
    if has(socket.gaierror): code = 'dns_error'
    elif has((ssl.SSLError, urllib3.exceptions.SSLError)): code = 'tls_error'
    elif has(ConnectionResetError) or any(getattr(e,'errno',None) == errno.ECONNRESET for e in chain): code = 'connection_reset'
    elif has(ConnectionRefusedError): code = 'connection_refused'
    elif has(urllib3.exceptions.ReadTimeoutError): code = 'read_timeout'
    elif has(urllib3.exceptions.NewConnectionError): code = 'connection_error'
    elif has(urllib3.exceptions.ConnectTimeoutError): code = 'connect_timeout'
    elif has((TimeoutError, socket.timeout)): code = 'read_timeout' if stage == 'read_body' else 'connect_timeout'
    elif stage == 'decode_body': code = 'response_decode_error'
    elif has((OSError, urllib3.exceptions.HTTPError)): code = 'connection_error'
    else: code = 'unexpected_error'
    details = {'stage': stage, 'exception_chain': [type(value).__name__ for value in chain]}
    for attr in ('errno', 'verify_code'):
        values = [getattr(value,attr,None) for value in chain]
        numbers = [value for value in values if isinstance(value,int)]
        if numbers: details[attr] = numbers[-1]
    return code, details


def log_failure(url, batch_id, code, details, elapsed):
    logger.warning('scan_http_error batch=%s host=%s url_sha256=%s code=%s stage=%s exceptions=%s errno=%s verify_code=%s elapsed_ms=%s',
        batch_id or '-', urlsplit(url).hostname, hashlib.sha256(url.encode()).hexdigest(), code,
        details['stage'], ','.join(details['exception_chain']), details.get('errno'),
        details.get('verify_code'), round(elapsed*1000))
