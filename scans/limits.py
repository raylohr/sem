MAX_URLS = 100_000
MAX_DEPTH = 100
MAX_DURATION = 7 * 24 * 60 * 60
SLICE_SECONDS = 120
MAX_SITEMAPS = 10_000


def scan_budget(kind, max_urls):
    # Keep small scans modest, but never stop a large crawl after three minutes.
    duration = min(MAX_DURATION, max(120, max_urls * (6 if kind == 'crawl' else 1) + 60))
    transfer = max(25 * 1024**2, max_urls * 1024 * 1024)
    return duration, transfer
