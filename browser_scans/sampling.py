"""Bounded, deterministic samples: spread across paths before query variants."""
from collections import defaultdict, Counter
from urllib.parse import urlsplit
from scans.network import normalize_url, ScanError


def select_urls(urls, allowed_hosts, limit):
    groups = defaultdict(dict)
    for value in urls:
        try:
            url = normalize_url(value, allowed_hosts)
        except ScanError:
            continue
        parts = urlsplit(url)
        if parts.path.startswith('/cdn-cgi/'):
            continue
        segments = parts.path.strip('/').split('/')
        group = (parts.hostname, segments[0])
        if group not in groups and len(groups) >= limit:
            continue
        path = (parts.scheme, parts.netloc, parts.path)
        bucket = groups[group]
        if path in bucket:
            # Prefer the canonical path URL to a parameterized entry point.
            if urlsplit(bucket[path]).query and not parts.query:
                bucket[path] = url
        elif len(bucket) < limit:
            bucket[path] = url
        else:
            family = tuple(segments[:2])
            families = [tuple(urlsplit(item).path.strip('/').split('/')[:2]) for item in bucket.values()]
            if family not in families:
                family_counts = Counter(families)
                depths = Counter(len(urlsplit(item).path.strip('/').split('/')) for item in bucket.values())
                depth = len(segments)
                for existing, existing_family in reversed(list(zip(bucket, families))):
                    existing_depth = len(urlsplit(bucket[existing]).path.strip('/').split('/'))
                    if family_counts[existing_family] > 1 or (depth not in depths and depths[existing_depth] > 1):
                        del bucket[existing]
                        bucket[path] = url
                        break
    queues = []
    for bucket in groups.values():
        ordered = sorted(bucket.values(), key=lambda u: (bool(urlsplit(u).query), len(urlsplit(u).path.strip('/').split('/')), u))
        first, rest, seen = [], [], set()
        for url in ordered:
            path = urlsplit(url).path.strip('/').split('/')
            family = tuple(path[:2])
            (rest if family in seen else first).append(url)
            seen.add(family)
        queues.append(first + rest)
    selected = []
    for position in range(limit):
        for queue in queues:
            if position < len(queue):
                selected.append(queue[position])
                if len(selected) == limit:
                    return selected
    return selected


def select_pages(rows,allowed_hosts,limit):
    """Path diversity plus a bounded quota for uncertain/dynamic page candidates."""
    risk={};reasons={}
    def urls():
        for row in rows:
            url=row['final_url'];facts=row.get('facts',{})
            hints=[]
            if facts.get('word_count') is not None and facts['word_count']<100:hints.append('weinig tekst in server-HTML')
            if facts.get('h1')==[]:hints.append('geen H1 in server-HTML')
            path=urlsplit(url).path.lower()
            if any(part in path for part in ('contact','bereken','calculator','zoeken','search')):hints.append('interactief paginatype op basis van URL-pad')
            if any(any(token in source.lower() for token in ('_next/','react','vue','nuxt')) for source in (facts.get('resource_urls') or [])):hints.append('aanwijzing voor client-rendering')
            if hints:
                risk[url]=hints
                if len(risk)>limit*2:
                    remove=min(risk,key=lambda key:(len(risk[key]),key));del risk[remove]
            yield url
    selected=select_urls(urls(),allowed_hosts,limit)
    if not selected:return [],[]
    quota=min(max(1,limit//3),len(risk))
    chosen=[];chosen_paths=set()
    existing_paths={(urlsplit(url).scheme,urlsplit(url).netloc,urlsplit(url).path):url for url in selected}
    for url in sorted(risk,key=lambda key:(-len(risk[key]),key)):
        try:normalize_url(url,allowed_hosts)
        except ScanError:continue
        parts=urlsplit(url);path=(parts.scheme,parts.netloc,parts.path)
        if path in chosen_paths:continue
        chosen_paths.add(path);chosen.append(existing_paths.get(path,url))
        if len(chosen)>=quota:break
    protected=set(chosen)
    for url in chosen:
        if url in selected:continue
        if len(selected)>=limit:
            removable=next((index for index in range(len(selected)-1,0,-1) if selected[index] not in protected),None)
            if removable is None:continue
            selected.pop(removable)
        selected.append(url)
    return selected,[{'url':url,'reasons':['spreiding over URL-paden']+risk.get(url,[]),'basis':'heuristische steekproef; geen bewezen paginatemplate'} for url in selected]
