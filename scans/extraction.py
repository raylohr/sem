import hashlib
import re
from html.parser import HTMLParser
from urllib.parse import urljoin


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = []
        self.h1, self.h2 = [], []
        self.current = None
        self.ignore = 0
        self.foreign_depth = 0
        self.protected_email_locators = []
        self.text = []
        self.links = []
        self.canonicals = []
        self.descriptions = []
        self.robots = []
        self.language = ''
        self.base = ''
        self.link_details = []
        self.link_count = 0
        self.anchor = None
        self.robot_directives = []
        self.resource_urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('svg', 'math'):
            self.foreign_depth += 1
        if tag in ('script', 'style', 'noscript'):
            self.ignore += 1
        if tag in ('title', 'h1', 'h2') and not self.foreign_depth:
            self.current = (tag, [])
        resource = attrs.get('src') if tag in ('script','img','iframe','source') else attrs.get('href') if tag == 'link' and set((attrs.get('rel') or '').lower().split()) & {'stylesheet','preload','modulepreload'} else None
        if resource and len(self.resource_urls) < 200:
            self.resource_urls.append(resource[:4096])
        if tag == 'html':
            self.language = attrs.get('lang', '')
        if tag == 'base' and not self.base:
            self.base = attrs.get('href', '')
        if tag == 'meta':
            name = (attrs.get('name') or '').lower()
            if name == 'description':
                self.descriptions.append(attrs.get('content', ''))
            if name in ('robots', 'googlebot'):
                self.robots.append(attrs.get('content', ''))
                self.robot_directives.append({'source': 'meta', 'agent': name, 'value': attrs.get('content', '')})
        if tag == 'link' and 'canonical' in (attrs.get('rel') or '').lower().split():
            self.canonicals.append(attrs.get('href', ''))
        if tag == 'a':
            self.anchor = None
            if attrs.get('href'):
                self.link_count += 1
                if len(self.links) < 2000:
                    self.links.append(attrs['href'])
                    self.anchor = {'href': attrs['href'], 'text': [],
                        'rel': (attrs.get('rel') or '').lower().split(), 'locator': f'html:{self.getpos()[0]}:{self.getpos()[1]}'}
                    self.link_details.append(self.anchor)
                    if attrs.get('data-cfemail') or '__cf_email__' in attrs.get('class', '').split():
                        self.protected_email_locators.append(self.anchor['locator'])
        if tag == 'span' and self.anchor and '__cf_email__' in attrs.get('class', '').split():
            self.protected_email_locators.append(self.anchor['locator'])
        if tag == 'img' and self.anchor and attrs.get('alt'):
            self.anchor['text'].append(attrs['alt'])

    def handle_endtag(self, tag):
        if tag in ('svg', 'math'):
            self.foreign_depth = max(0, self.foreign_depth-1)
        if tag == 'a':
            self.anchor = None
        if tag in ('script', 'style', 'noscript'):
            self.ignore = max(0, self.ignore-1)
        if self.current and tag == self.current[0]:
            value = ' '.join(' '.join(self.current[1]).split())
            if tag == 'title':
                self.title.append(value)
            elif tag == 'h1' and len(self.h1) < 100:
                self.h1.append(value)
            elif tag == 'h2' and len(self.h2) < 100:
                self.h2.append(value)
            self.current = None

    def handle_data(self, data):
        if not self.ignore:
            if self.anchor:
                self.anchor['text'].append(data)
            self.text.append(data)
            if self.current:
                self.current[1].append(data)


def extract_page(result):
    content_type = result.headers.get('content-type', '').lower()
    if 'text/html' not in content_type and 'application/xhtml+xml' not in content_type:
        return {}, []
    charset = re.search(r'charset=["\']?([\w-]+)', content_type)
    try:
        html = result.body.decode(charset.group(1) if charset else 'utf-8', errors='replace')
    except LookupError:
        html = result.body.decode('utf-8', errors='replace')
    parser = PageParser()
    parser.feed(html)
    text = ' '.join(' '.join(parser.text).split())
    base = urljoin(result.url, parser.base) if parser.base else result.url
    canonicals = [urljoin(base, value) for value in parser.canonicals if value]
    robots = parser.robots + [result.headers.get('x-robots-tag', '')]
    noindex = bool(set(re.split(r'[\s,:;]+', ' '.join(robots).lower())) & {'noindex', 'none'})
    from .network import normalize_url, ScanError
    canonical_elsewhere = False
    if canonicals:
        try:
            canonical_elsewhere = normalize_url(canonicals[0]) != normalize_url(result.url)
        except ScanError:
            canonical_elsewhere = True
    reasons = []
    if result.status != 200:
        reasons.append('http_status')
    if noindex:
        reasons.append('noindex')
    if canonical_elsewhere:
        reasons.append('canonical_elsewhere')
    fields = {'extractor_version': 'html-v3', 'protected_email_locators': parser.protected_email_locators, 'resource_urls': [urljoin(base, value) for value in parser.resource_urls], 'indexability_reasons': reasons,
        'robot_directives': parser.robot_directives + ([{'source': 'header', 'agent': 'unspecified', 'value': result.headers['x-robots-tag']}] if result.headers.get('x-robots-tag') else []),
        'link_count': parser.link_count, 'links_truncated': parser.link_count > len(parser.links),
        'link_details': [{'url': urljoin(base, link['href']), 'anchor_text': ' '.join(' '.join(link['text']).split()), 'rel': link['rel'], 'locator': link['locator']} for link in parser.link_details],
        'text_extraction_basis': 'server HTML, excluding script/style/noscript; includes navigation and CSS-hidden text',
        'titles': parser.title, 'meta_descriptions': parser.descriptions,
        'h1': parser.h1, 'h2': parser.h2, 'canonicals': canonicals, 'robots': robots,
        'language': parser.language, 'word_count': len(text.split()),
        'text': text[:100_000], 'text_truncated': len(text) > 100_000,
        'text_hash': hashlib.sha256(text.encode()).hexdigest(),
        'is_indexable': result.status == 200 and not noindex and not canonical_elsewhere,
        'indexability_basis': 'HTTP, robots-instructies en canonical; geen Google-indexeringsmeting',
        'outlinks': [urljoin(base, value) for value in parser.links]}
    return fields, fields['outlinks']
