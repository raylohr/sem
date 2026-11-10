"""Bounded metadata and reachability requests through the public-only proxy."""
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import gzip
from io import BytesIO
import re
from urllib.parse import urljoin, urlsplit

import requests
from defusedxml import ElementTree
from integrations.url_validation import normalize_url

MAX_XML = 5 * 1024 * 1024
MAX_ROBOTS = 512 * 1024


class CrawlNetworkError(Exception):
    pass


def safe_http_url(url):
    normalized = normalize_url(url)
    if not normalized:
        raise CrawlNetworkError('Ongeldig webadres.')
    parts = urlsplit(normalized[0])
    if parts.port not in (None,80 if parts.scheme == 'http' else 443):
        raise CrawlNetworkError('Afwijkende poort niet toegestaan.')
    return normalized[0]


def retry_after_seconds(value):
    if not value:
        return 0
    try:
        seconds = int(value)
    except (ValueError,TypeError):
        try:
            seconds = (parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds()
        except (ValueError,TypeError,OverflowError):
            return 0
    return max(0,min(300,int(seconds)))


@dataclass
class HTTPResult:
    url: str
    status: int
    headers: dict
    body: bytes
    redirects: list


class SafeHTTP:
    def __init__(self, proxy, user_agent, guard=lambda: None):
        if not proxy:
            raise CrawlNetworkError('Crawlproxy is niet ingesteld.')
        self.proxy, self.user_agent, self.guard = proxy, user_agent, guard

    def fetch(self, url, *, method='GET', maximum=MAX_XML, allowed=lambda url: True, body=True):
        current = safe_http_url(url)
        redirects = []
        try:
            with requests.Session() as session:
                session.trust_env = False
                session.proxies = {'http':self.proxy,'https':self.proxy}
                for _ in range(6):
                    self.guard()
                    if not allowed(current):
                        raise CrawlNetworkError('Redirect valt buiten de toegestane crawlgrenzen.')
                    with session.request(method,current,headers={'User-Agent':self.user_agent},
                            timeout=(5,15),allow_redirects=False,stream=True) as response:
                        headers = {key.lower():value for key,value in response.headers.items()
                                   if key.lower() in ('content-type','content-length','location','retry-after','x-robots-tag')}
                        if response.status_code in (301,302,303,307,308) and headers.get('location'):
                            redirects.append({'url':current,'status':response.status_code})
                            current = safe_http_url(urljoin(current,headers['location']))
                            continue
                        chunks, total = [], 0
                        if body and method != 'HEAD':
                            for chunk in response.iter_content(65536):
                                total += len(chunk)
                                if total > maximum:
                                    raise CrawlNetworkError('Antwoord overschrijdt de crawl-limiet.')
                                chunks.append(chunk)
                        return HTTPResult(current,response.status_code,headers,b''.join(chunks),redirects)
        except requests.RequestException:
            raise CrawlNetworkError('Bestemming niet bereikbaar of timeout.') from None
        raise CrawlNetworkError('Te veel redirects.')

    def reachable(self, url):
        result = self.fetch(url,method='HEAD',body=False)
        if result.status in (405,501):
            result = self.fetch(url,method='GET',body=False)
        return result


def sitemap_entries(body):
    if body.startswith(b'\x1f\x8b'):
        try:
            body = gzip.GzipFile(fileobj=BytesIO(body)).read(MAX_XML+1)
        except (OSError,EOFError):
            raise CrawlNetworkError('Ongeldige gecomprimeerde sitemap.') from None
    if len(body) > MAX_XML:
        raise CrawlNetworkError('Sitemap is te groot.')
    try:
        root = ElementTree.fromstring(body)
    except Exception:
        raise CrawlNetworkError('Ongeldige sitemap-XML.') from None
    kind = root.tag.rsplit('}',1)[-1]
    if kind not in ('sitemapindex','urlset'):
        raise CrawlNetworkError('Onbekend sitemapformaat.')
    values = []
    for child in root:
        for field in child:
            if field.tag.rsplit('}',1)[-1] == 'loc' and field.text:
                values.append(field.text.strip())
    return kind, values


class RobotsRules:
    """Bounded allow/disallow groups, wildcard paths and sitemap declarations."""
    def __init__(self, text):
        self.groups, self.sitemaps = [], []
        agents, rules = [], []
        for line in text.splitlines()[:10000]:
            line = line.split('#',1)[0].strip()
            if ':' not in line:
                continue
            name,value = (part.strip() for part in line.split(':',1))
            name = name.lower()
            if name == 'sitemap':
                self.sitemaps.append(value)
            elif name == 'user-agent':
                if rules:
                    self.groups.append((agents,rules))
                    agents,rules = [],[]
                agents.append(value.lower())
            elif name in ('allow','disallow') and agents and value and len(value)<=2048:
                rules.append((name=='allow',value))
        if agents:
            self.groups.append((agents,rules))

    def allowed(self, user_agent, url):
        agent = user_agent.lower()
        candidates = []
        for agents,rules in self.groups:
            score = max((0 if token=='*' else len(token) for token in agents if token=='*' or token in agent),default=-1)
            if score >= 0:
                candidates.append((score,rules))
        if not candidates:
            return True
        best = max(score for score,_ in candidates)
        parts = urlsplit(url)
        target = (parts.path or '/') + ('?'+parts.query if parts.query else '')
        matched = []
        for score,rules in candidates:
            if score != best:
                continue
            for allow,pattern in rules:
                end = pattern.endswith('$')
                value = pattern[:-1] if end else pattern
                expression = '^' + '.*'.join(re.escape(part) for part in value.split('*')) + ('$' if end else '')
                if re.search(expression,target):
                    matched.append((len(value.replace('*','')),allow))
        return max(matched)[1] if matched else True
