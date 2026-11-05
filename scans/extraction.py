import hashlib
import json
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
        self.hreflang = []
        self.headings = []
        self.images = []
        self.image_count = 0
        self.jsonld = []
        self.jsonld_current = None
        self.jsonld_count = 0

    def handle_starttag(self, tag, attrs):
        attrs = {key:value or '' for key,value in attrs}
        if tag in ('svg', 'math'):
            self.foreign_depth += 1
        if tag == 'script' and attrs.get('type', '').lower().split(';')[0].strip() == 'application/ld+json':
            self.jsonld_count += 1
            if self.jsonld_count <= 20:
                self.jsonld_current = []
        if tag in ('script', 'style', 'noscript'):
            self.ignore += 1
        if tag in ('title', 'h1', 'h2') and not self.foreign_depth:
            self.current = (tag, [])
        resource = attrs.get('src') if tag in ('script','img','iframe','source') else attrs.get('href') if tag == 'link' and set((attrs.get('rel') or '').lower().split()) & {'stylesheet','preload','modulepreload'} else None
        if resource and len(self.resource_urls) < 200:
            self.resource_urls.append(resource[:4096])
        if tag in ('h1','h2','h3','h4','h5','h6') and not self.foreign_depth and len(self.headings) < 200:
            self.headings.append({'level':int(tag[1]),'locator':f'html:{self.getpos()[0]}:{self.getpos()[1]}'})
        if tag == 'img':
            self.image_count += 1
            if len(self.images) < 200:
                self.images.append({'src':(attrs.get('src') or '')[:4096], 'alt_present':'alt' in attrs, 'alt':attrs.get('alt','')[:500], 'locator':f'html:{self.getpos()[0]}:{self.getpos()[1]}'})
        if tag == 'link' and 'alternate' in (attrs.get('rel') or '').lower().split() and 'hreflang' in attrs and len(self.hreflang) < 100:
            self.hreflang.append({'code':attrs['hreflang'][:100], 'url':(attrs.get('href') or '')[:4096], 'source':'html'})
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
        if tag == 'script' and self.jsonld_current is not None:
            raw = ''.join(self.jsonld_current)
            item = {'status':'unknown' if len(raw)>65536 else 'valid', 'types':[]}
            if len(raw)<=65536:
                try:
                    from .structured_data import strict_json
                    value = strict_json(raw)
                    if not isinstance(value,(dict,list)):
                        raise ValueError('Expected object or array')
                    nodes = value if isinstance(value,list) else [value]
                    for node in nodes[:100]:
                        if isinstance(node,dict):
                            types=node.get('@type',[])
                            item['types'].extend([str(t)[:100] for t in (types if isinstance(types,list) else [types])][:20])
                except RecursionError:
                    item={'status':'unknown','reason':'decoder_recursion_limit'}
                except ValueError as exc:
                    item={'status':'invalid','error':type(exc).__name__,'line':getattr(exc,'lineno',None),'column':getattr(exc,'colno',None)}
            self.jsonld.append(item)
            self.jsonld_current = None
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
        if self.jsonld_current is not None and sum(map(len,self.jsonld_current)) <= 65536:
            self.jsonld_current.append(data[:65537])
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
    if parser.jsonld_current is not None:
        parser.jsonld.append({'status':'unknown','reason':'unclosed_script'})
    text = ' '.join(' '.join(parser.text).split())
    base = urljoin(result.url, parser.base) if parser.base else result.url
    canonicals = [urljoin(base, value) for value in parser.canonicals if value]
    from requests.utils import parse_header_links
    header_links = parse_header_links(result.headers.get('link',''))[:100]
    canonical_sources = [{'url':url,'source':'html'} for url in canonicals]
    for link in header_links:
        rel = link.get('rel','').lower().split()
        if 'canonical' in rel and link.get('url'):
            url = urljoin(result.url,link['url']); canonicals.append(url)
            canonical_sources.append({'url':url,'source':'header'})
        if 'alternate' in rel and 'hreflang' in link:
            parser.hreflang.append({'code':link['hreflang'][:100],'url':urljoin(result.url,link.get('url','')[:4096]),'source':'header'})
    retained_headers = {k:v[:8192] for k,v in result.headers.items() if k.lower() in ('link','x-robots-tag','strict-transport-security','content-security-policy','x-content-type-options','x-frame-options','referrer-policy')}
    robots = parser.robots + [result.headers.get('x-robots-tag', '')]
    from .seo import google_tokens
    directives = parser.robot_directives + ([{'source':'header','agent':'unspecified','value':result.headers['x-robots-tag']}] if result.headers.get('x-robots-tag') else [])
    noindex = 'noindex' in google_tokens({'robot_directives':directives})
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
    fields = {'extractor_version': 'html-v8', 'transfer_measurements':getattr(result,'measurements',{}),'headers_observed':True,'response_headers':retained_headers,
        'canonical_sources':canonical_sources, 'hreflang':[dict(item,url=urljoin(base,item['url']) if item['url'] else '') for item in parser.hreflang[:100]], 'hreflang_truncated':len(parser.hreflang)>=100,
        'heading_sequence':parser.headings, 'headings_truncated':len(parser.headings)>=200,
        'images':[dict(item,src=urljoin(base,item['src']) if item['src'] else '') for item in parser.images], 'image_count':parser.image_count, 'images_truncated':parser.image_count>len(parser.images),
        'jsonld':parser.jsonld, 'jsonld_count':parser.jsonld_count, 'jsonld_truncated':parser.jsonld_count>20, 'protected_email_locators': parser.protected_email_locators, 'resource_urls': [urljoin(base, value) for value in parser.resource_urls], 'indexability_reasons': reasons,
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
    from .html_evidence import enrich
    enrich(html,fields,base)
    from .header_evidence import extract as header_evidence
    fields.update(header_evidence(result))
    return fields, fields['outlinks']
