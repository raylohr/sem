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
        self.text = []
        self.links = []
        self.canonicals = []
        self.descriptions = []
        self.robots = []
        self.language = ''
        self.base = ''

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style', 'noscript'):
            self.ignore += 1
        if tag in ('title', 'h1', 'h2'):
            self.current = (tag, [])
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
        if tag == 'link' and 'canonical' in (attrs.get('rel') or '').lower().split():
            self.canonicals.append(attrs.get('href', ''))
        if tag == 'a' and attrs.get('href') and len(self.links) < 2000:
            self.links.append(attrs['href'])

    def handle_endtag(self, tag):
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
    fields = {'titles': parser.title, 'meta_descriptions': parser.descriptions,
        'h1': parser.h1, 'h2': parser.h2, 'canonicals': canonicals, 'robots': robots,
        'language': parser.language, 'word_count': len(text.split()),
        'text': text[:100_000], 'text_truncated': len(text) > 100_000,
        'text_hash': hashlib.sha256(text.encode()).hexdigest(),
        'is_indexable': result.status == 200 and not noindex and not canonical_elsewhere,
        'indexability_basis': 'HTTP, robots-instructies en canonical; geen Google-indexeringsmeting',
        'outlinks': [urljoin(base, value) for value in parser.links]}
    return fields, fields['outlinks']
