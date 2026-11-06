"""Project host boundaries: exact apex/www, optionally its subdomains."""
import ipaddress
import re
import socket
from urllib.parse import urlsplit
from django.core.exceptions import ValidationError


def normalize_domain(value):
    try:
        domain = value.strip().rstrip('.').lower().encode('idna').decode('ascii')
        if len(domain) > 253 or len(domain.split('.')) < 2:
            raise ValueError
        if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in domain.split('.')):
            raise ValueError
        try:
            ipaddress.ip_address(domain)
        except ValueError:
            return domain
    except (ValueError, UnicodeError, AttributeError):
        pass
    raise ValidationError('Geef een geldig hoofddomein op, bijvoorbeeld voorbeeld.nl, zonder protocol of pad.')


def website_domain(website):
    return normalize_domain((urlsplit(website).hostname or '').removeprefix('www.'))


def check_dns(domain):
    try:
        if socket.getaddrinfo(domain + '.', None, type=socket.SOCK_STREAM):
            return
    except socket.gaierror:
        pass
    raise ValidationError('Het hoofddomein heeft geen vindbaar DNS-adres (A/AAAA), of DNS is tijdelijk niet bereikbaar. Controleer het domein en probeer opnieuw.')


def allows_url(project, url):
    from integrations.url_validation import normalize_url
    identity = normalize_url(url)
    if not identity:
        return False
    try:
        domain = normalize_domain(project.domain) if project.domain else website_domain(project.website)
    except ValidationError:
        return False
    host = identity[1]
    return host in (domain, 'www.' + domain) or (project.include_subdomains and host.endswith('.' + domain))


def host_rules(project):
    domain = project.domain or website_domain(project.website)
    return {'domain': domain, 'allowed_hosts': [domain, 'www.' + domain],
            'include_subdomains': project.include_subdomains}


def filter_records(project, records):
    accepted, excluded = [], 0
    for item in records:
        data = item['data']
        url = data.get('url') or data.get('Address') or data.get('URL')
        if allows_url(project, url):
            accepted.append(item)
        else:
            excluded += 1
    return accepted, excluded
