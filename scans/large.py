"""Checkpointed runners: only one response and one result row live in memory."""
import hashlib
import json
from django.utils import timezone
from .extraction import extract_page
from .network import normalize_url, ScanError, ScanBudgetExceeded, RobotsDenied
from .limits import MAX_SITEMAPS
from .work import ScanPause


def bounded_metadata(fields):
    if len(json.dumps(fields,ensure_ascii=False).encode('utf-8')) <= 1024**2:
        return fields
    fields = dict(fields)
    for key,count,width in [('titles',5,2048),('meta_descriptions',5,4096),('h1',50,1024),
                            ('h2',50,1024),('canonicals',5,4096),('robots',20,2048),('outlinks',100,4096)]:
        fields[key] = [value[:width] for value in fields.get(key,[])[:count]]
    fields['language'] = fields.get('language','')[:256]
    fields['analysis_truncated'] = True
    return fields


def add_warning(work, message):
    warnings = work.get('warnings', [])
    if message not in warnings:
        warnings.append(message)
        work.set('warnings', warnings)


def crawl_checkpointed(client, *, url, max_urls, max_depth, work):
    work.add('crawl', normalize_url(url, client.allowed_hosts), 0)
    discovered = work.count('crawl')
    while len(work.records) < max_urls and (item := work.next('crawl')):
        item_id, current, depth = item
        try:
            work.check_disk()
            result = client.fetch(current)
            fields, links = extract_page(result)
            fields = bounded_metadata(fields)
            data = {'url': current, 'final_url': result.url,
                'status': result.redirects[0]['status'] if result.redirects else result.status,
                'final_status': result.status, 'redirects': result.redirects,
                'blocked_redirect': result.blocked_redirect, 'depth': depth,
                'measured_at': timezone.now().isoformat(), 'content_type': result.headers.get('content-type',''),
                'response_time': result.elapsed, 'body_hash': hashlib.sha256(result.body).hexdigest(), **fields}
            if depth < max_depth:
                for link in links:
                    try:
                        link = normalize_url(link, client.allowed_hosts)
                    except ScanError:
                        continue
                    if discovered < max_urls * 5:
                        discovered += work.add('crawl', link, depth+1)
                    else:
                        add_warning(work, 'Ontdeklimiet voor links bereikt.')
            elif links:
                add_warning(work, 'Dieptelimiet bereikt; dieper gelinkte pagina’s zijn niet ingepland.')
            if result.blocked_redirect:
                add_warning(work, result.blocked_redirect)
            stop = result.status in (429,503)
            if stop:
                add_warning(work, 'Website vraagt om terughoudendheid (429/503); crawl gestopt.')
        except ScanBudgetExceeded as exc:
            add_warning(work, str(exc))
            break
        except (RobotsDenied, ScanError) as exc:
            data = {'url':current,'depth':depth,'error':str(exc),'measured_at':timezone.now().isoformat()}
            if isinstance(exc, RobotsDenied):
                data['robots_blocked'] = True
            stop = False
        work.finish(item_id, {'locator':f'crawl:page:{len(work.records)}','data':data})
        if stop:
            break
    if work.next('crawl'):
        add_warning(work,'URL- of tijdslimiet bereikt; niet alle ontdekte pagina’s zijn bezocht.')
    add_warning(work,'Basiscrawl zonder JavaScript-rendering; indexeerbaarheid is een technische beoordeling.')
    return work.records, work.get('warnings',[])


def sitemap_checkpointed(client, *, url, max_urls, max_depth, work):
    from .runners import parse_sitemap
    work.add('sitemap',normalize_url(url,client.allowed_hosts),0)
    discovered = work.count('sitemap')
    while len(work.records) < max_urls and (item := work.next('sitemap')):
        item_id, current, depth = item
        try:
            work.check_disk()
            result = client.fetch(current)
            if result.status != 200 or result.blocked_redirect:
                raise ScanError('Sitemap niet bereikbaar met HTTP 200 binnen toegestane hosts.')
            kind, entries = parse_sitemap(result.body)
        except ScanBudgetExceeded as exc:
            add_warning(work,str(exc))
            break
        except ScanError as exc:
            if work.count('sitemap',done=1)==0:
                raise
            add_warning(work,f'Sub-sitemap overgeslagen: {exc}')
            work.finish(item_id)
            continue
        offset = work.get(f'offset:{item_id}',0)
        for position in range(offset,len(entries)):
            if position % 100 == 0:
                try:
                    client.guard()
                except ScanBudgetExceeded as exc:
                    add_warning(work,str(exc))
                    add_warning(work,'Sitemapimport is onvolledig; pagina’s zijn niet opgehaald.')
                    return work.records,work.get('warnings',[])
                work.set(f'offset:{item_id}',position)
            entry = entries[position]
            try:
                location = normalize_url(entry['loc'],client.allowed_hosts)
            except ScanError:
                add_warning(work,'Sitemapverwijzingen buiten de toegestane hosts zijn overgeslagen.')
                continue
            if kind == 'sitemapindex':
                if depth < max_depth and discovered < MAX_SITEMAPS:
                    discovered += work.add('sitemap',location,depth+1)
                else:
                    add_warning(work,'Diepte- of ontdeklimiet voor sub-sitemaps bereikt.')
            else:
                work.sitemap_record(location,{'locator':f'sitemap:url:{len(work.records)}','data':{
                    'url':location,'sitemap_url':result.url,'sitemap_entry':entry,
                    'lastmod':entry.get('lastmod') or None,'measured_at':timezone.now().isoformat(),
                    'page_fetched':False}})
                if len(work.records)>=max_urls:
                    add_warning(work,'URL-limiet bereikt; sitemapimport kan onvolledig zijn.')
                    break
        work.finish(item_id)
        if work.count('sitemap',done=1)>=MAX_SITEMAPS:
            add_warning(work,'Limiet voor sitemapdocumenten bereikt.')
            break
    if work.next('sitemap'):
        add_warning(work,'Niet alle sub-sitemaps zijn verwerkt.')
    add_warning(work,'Sitemapvermelding is geen bewijs van bereikbaarheid of indexering; pagina’s zijn niet opgehaald.')
    return work.records,work.get('warnings',[])
