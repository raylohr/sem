"""Checkpointed runners with bounded HTTP concurrency and a single checkpoint writer."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
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
    fields['link_details'] = [{**link, 'anchor_text': link.get('anchor_text', '')[:1024], 'rel': [value[:128] for value in link.get('rel', [])[:20]]} for link in fields.get('link_details', [])[:100] if len(link.get('url', '')) <= 8192]
    fields['robot_directives'] = [{**item, 'value': item.get('value', '')[:2048]} for item in fields.get('robot_directives', [])[:20]]
    fields['links_truncated'] = True
    fields['analysis_truncated'] = True
    # New structured fields must not silently bypass the existing metadata budget.
    while len(json.dumps(fields, ensure_ascii=False).encode('utf-8')) > 1024**2:
        arrays = [key for key, value in fields.items() if isinstance(value, list) and value]
        if not arrays:
            break
        for key in arrays:
            fields[key] = fields[key][:len(fields[key]) // 2]
    return fields


def add_warning(work, message):
    warnings = work.get('warnings', [])
    if message not in warnings:
        warnings.append(message)
        work.set('warnings', warnings)


def crawl_checkpointed(client, *, url, max_urls, max_depth, work):
    work.add('crawl', normalize_url(url, client.allowed_hosts), 0)
    if getattr(client,'discover_sitemaps',False) is True:
        from .seeding import initialize, origin
        initialize(work)
        origin(work,normalize_url(url,client.allowed_hosts),'start')
    discovered = work.count('crawl')
    parallel = getattr(client, 'concurrency', 1)
    if not isinstance(parallel, int):
        parallel = 1
    executor = ThreadPoolExecutor(max_workers=parallel) if parallel > 1 else None
    ready = {}
    def fetch(current):
        return ready.pop(current).result() if current in ready else client.fetch(current)
    try:
        return _crawl_loop(client, url=url, max_urls=max_urls, max_depth=max_depth,
                           work=work, discovered=discovered, executor=executor, ready=ready, fetch=fetch)
    finally:
        if executor:
            client.stopped.set()
            executor.shutdown(wait=True, cancel_futures=True)
            client.drain_documents()


def _crawl_loop(client, *, url, max_urls, max_depth, work, discovered, executor, ready, fetch):
    auto=getattr(client,'discover_sitemaps',False) is True
    while len(work.records) < max_urls:
        if auto and len(work.records) and not work.get('seed_maps_done'):
            from .seeding import discover
            try:discover(client,work,url,max_urls)
            except ScanBudgetExceeded as exc:
                add_warning(work,str(exc));break
            discovered=work.count('crawl')
        item=work.next('crawl')
        if not item:break
        item_id, current, depth = item
        try:
            work.check_disk()
            if executor:
                client.guard()
                capacity = min(client.concurrency-len(ready), max_urls-len(work.records)-len(ready))
                if capacity > 0:
                    exclusions = ','.join('?' for _ in ready) or "''"
                    items = work.connection.execute(
                        f'SELECT url FROM frontier WHERE kind=? AND done=0 AND depth=? AND url NOT IN ({exclusions}) ORDER BY id LIMIT ?',
                        ('crawl', depth, *ready, capacity)).fetchall()
                    ready.update((value, executor.submit(client.fetch, value)) for (value,) in items)
                while ready and not any(f.done() for f in ready.values()):
                    client.guard()
                    wait(ready.values(), timeout=0.1, return_when=FIRST_COMPLETED)
                client.guard()
                if ready:
                    current = next(value for value, future in ready.items() if future.done())
                    item_id, current, depth = work.connection.execute(
                        'SELECT id,url,depth FROM frontier WHERE kind=? AND url=?', ('crawl',current)).fetchone()
            result = fetch(current)
            if result.status in (429, 503):
                retry_key = 'retry:' + str(item_id)
                retries = work.get(retry_key, 0)
                if retries < 3:
                    work.set(retry_key, retries+1)
                    add_warning(work, 'Website vertraagt verzoeken (429/503); tempo verlaagd en URL opnieuw ingepland.')
                    continue
            fields, links = extract_page(result)
            fields = bounded_metadata(fields)
            discovery = getattr(client,'host_discovery',None)
            if discovery:
                discovery.observe(links,current)
                discovery.observe(fields.get('resource_urls',[]),current,'resources')
                if result.blocked_redirect and result.blocked_destination:
                    discovery.observe([result.blocked_destination],current)
            if getattr(result,"googlebot_allowed",None) is False:
                fields["is_indexable"] = False
            elif getattr(result,"robots_checked",False) and result.googlebot_allowed is None and fields.get("is_indexable"):
                fields["is_indexable"] = None
            data = {'url': current, 'final_url': result.url,
                'status': result.redirects[0]['status'] if result.redirects else result.status,
                'final_status': result.status, 'redirects': result.redirects,
                'blocked_redirect': result.blocked_redirect, 'depth': depth,
                'blocked_destination': result.blocked_destination,
                'measured_at': timezone.now().isoformat(), 'content_type': result.headers.get('content-type',''),
                'robots_allowed': getattr(result,'robots_allowed',None),
                'googlebot_allowed': getattr(result,'googlebot_allowed',None),
                'robots_respected': getattr(client,'respect_robots',True) is not False,
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
                if depth==0:
                    add_warning(work,'De startpagina verwijst buiten de toegestane hosts; de eigenlijke website is niet onderzocht. Beoordeel de hostkeuzes bij Websitescans.')
            if auto:
                from .seeding import links as remember_links
                remember_links(work,client,current,result.url,links,max_urls)
            stop = False
        except ScanBudgetExceeded as exc:
            add_warning(work, str(exc))
            break
        except (RobotsDenied, ScanError) as exc:
            data = {'url':current,'depth':depth,'error':str(exc),'measured_at':timezone.now().isoformat()}
            if hasattr(exc, 'code'):
                data.update(error_code=exc.code, error_details=exc.details)
            if isinstance(exc, RobotsDenied):
                data['robots_blocked'] = True
                data['robots_allowed'] = False
                data['googlebot_allowed'] = getattr(exc,'googlebot_allowed',None)
                data['robots_respected'] = True
            stop = False
        work.finish(item_id, {'locator':f'crawl:page:{len(work.records)}','data':data})
        if stop:
            break
    if work.next('crawl'):
        add_warning(work,'URL- of tijdslimiet bereikt; niet alle ontdekte pagina’s zijn bezocht.')
    if auto:
        from .seeding import finalize
        finalize(client,work,normalize_url(url,client.allowed_hosts),max_depth)
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
            discovery=getattr(client,'host_discovery',None)
            if discovery and result.blocked_redirect and result.blocked_destination:
                discovery.observe([result.blocked_destination],current)
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
        discovery=getattr(client,'host_discovery',None)
        if discovery:discovery.observe([entry['loc'] for entry in entries],current)
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
