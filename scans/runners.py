import hashlib
from collections import deque
from urllib.parse import urljoin
from defusedxml.ElementTree import fromstring
from django.utils import timezone
from .network import ScanError, ScanBudgetExceeded, RobotsDenied, normalize_url
from .extraction import extract_page


def crawl(client, *, url, max_urls, max_depth, work=None):
    if work is not None:
        from .large import crawl_checkpointed
        return crawl_checkpointed(client, url=url, max_urls=max_urls, max_depth=max_depth, work=work)
    queue = deque([(normalize_url(url, client.allowed_hosts), 0)])
    queued = {queue[0][0]}
    records, warnings = [], []
    while queue and len(records) < max_urls:
        current, depth = queue.popleft()
        try:
            result = client.fetch(current)
            fields, links = extract_page(result)
            discovery=getattr(client,'host_discovery',None)
            if discovery:
                discovery.observe(links,current)
                discovery.observe(fields.get('resource_urls',[]),current,'resources')
                if result.blocked_redirect and result.blocked_destination:discovery.observe([result.blocked_destination],current)
            data = {'url': current, 'final_url': result.url, 'status': result.redirects[0]['status'] if result.redirects else result.status,
                'final_status': result.status, 'redirects': result.redirects,
                'blocked_redirect': result.blocked_redirect, 'depth': depth,
                'blocked_destination': result.blocked_destination,
                'measured_at': timezone.now().isoformat(), 'content_type': result.headers.get('content-type', ''),
                'response_time': result.elapsed, 'body_hash': hashlib.sha256(result.body).hexdigest(), **fields}
            if depth < max_depth:
                for link in links:
                    try:
                        link = normalize_url(link, client.allowed_hosts)
                    except ScanError:
                        continue
                    if link not in queued and len(queued) < max_urls * 5:
                        queued.add(link)
                        queue.append((link, depth+1))
            if result.blocked_redirect:
                warnings.append(result.blocked_redirect)
            if result.status in (429, 503):
                warnings.append('Website vraagt om terughoudendheid (429/503); crawl gestopt.')
                queue.clear()
        except ScanBudgetExceeded as exc:
            warnings.append(str(exc))
            break
        except RobotsDenied as exc:
            data = {'url': current, 'depth': depth, 'robots_blocked': True, 'error': str(exc), 'measured_at': timezone.now().isoformat()}
        except ScanError as exc:
            # Cancellation/access errors are not ScanError and propagate out of the runner.
            data = {'url': current, 'depth': depth, 'error': str(exc), 'measured_at': timezone.now().isoformat()}
            if hasattr(exc, 'code'):
                data.update(error_code=exc.code, error_details=exc.details)
        records.append({'locator': f'crawl:page:{len(records)}', 'data': data})
    if queue:
        warnings.append('URL- of tijdslimiet bereikt; niet alle ontdekte pagina’s zijn bezocht.')
    if len(queued) >= max_urls * 5:
        warnings.append('Ontdeklimiet voor links bereikt.')
    warnings.append('Basiscrawl zonder JavaScript-rendering; indexeerbaarheid is een technische beoordeling.')
    return records, list(dict.fromkeys(warnings))


def local_name(tag):
    return tag.rsplit('}', 1)[-1]


def parse_sitemap(body):
    try:
        root = fromstring(body, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except Exception as exc:
        raise ScanError('Ongeldige of onveilige sitemap-XML.') from exc
    kind = local_name(root.tag)
    if kind not in ('urlset', 'sitemapindex'):
        raise ScanError('Verwacht een XML-urlset of sitemapindex.')
    entries = []
    for child in root:
        if local_name(child.tag) != ('url' if kind == 'urlset' else 'sitemap'):
            continue
        entry = {local_name(part.tag): (part.text or '').strip() for part in child if local_name(part.tag) in ('loc', 'lastmod', 'changefreq', 'priority')}
        if entry.get('loc'):
            entries.append(entry)
    return kind, entries


def sitemap(client, *, url, max_urls, max_depth, work=None):
    if work is not None:
        from .large import sitemap_checkpointed
        return sitemap_checkpointed(client, url=url, max_urls=max_urls, max_depth=max_depth, work=work)
    queue = deque([(normalize_url(url, client.allowed_hosts), 0)])
    seen, urls = set(), set()
    records, warnings = [], []
    while queue and len(records) < max_urls and len(seen) < 50:
        current, depth = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        try:
            result = client.fetch(current)
            if result.status != 200 or result.blocked_redirect:
                raise ScanError('Sitemap niet bereikbaar met HTTP 200 binnen toegestane hosts.')
            kind, entries = parse_sitemap(result.body)
        except ScanBudgetExceeded as exc:
            warnings.append(str(exc))
            break
        except ScanError as exc:
            if len(seen) == 1:
                raise
            warnings.append(f'Sub-sitemap overgeslagen: {str(exc)}')
            continue
        for entry in entries:
            try:
                location = normalize_url(entry['loc'], client.allowed_hosts)
            except ScanError:
                warnings.append('Sitemapverwijzingen buiten de toegestane hosts zijn overgeslagen.')
                continue
            if kind == 'sitemapindex':
                if depth < max_depth and len(queue) < 250:
                    queue.append((location, depth+1))
                else:
                    warnings.append('Diepte- of ontdeklimiet voor sub-sitemaps bereikt.')
                continue
            if location in urls:
                continue
            urls.add(location)
            records.append({'locator': f'sitemap:url:{len(records)}', 'data': {
                'url': location, 'sitemap_url': result.url, 'sitemap_entry': entry,
                'lastmod': entry.get('lastmod') or None, 'measured_at': timezone.now().isoformat(),
                'page_fetched': False}})
            if len(records) >= max_urls:
                warnings.append('URL-limiet bereikt; sitemapimport kan onvolledig zijn.')
                break
    if queue:
        warnings.append('Niet alle sub-sitemaps zijn verwerkt.')
    warnings.append('Sitemapvermelding is geen bewijs van bereikbaarheid of indexering; pagina’s zijn niet opgehaald.')
    return records, list(dict.fromkeys(warnings))
