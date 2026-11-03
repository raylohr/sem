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
