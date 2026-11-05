"""Indexed, frozen inventory including sitemap URLs not visited due to limits."""
import hashlib
from itertools import islice
from .models import CrawlURLCoverage


def publish_coverage(batch,work):
    connection=work.connection
    connection.execute("CREATE INDEX IF NOT EXISTS record_url ON records(json_extract(payload,'$.data.url'))")
    has_origins=connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='crawl_origins'").fetchone()
    if has_origins:
        query='''SELECT o.url,o.sitemap,o.link,o.start,o.sitemap_url,r.payload,f.id FROM crawl_origins o
            LEFT JOIN frontier f ON f.url=o.url AND f.kind='crawl'
            LEFT JOIN records r ON json_extract(r.payload,'$.data.url')=o.url
            '''
    else:
        query="SELECT json_extract(payload,'$.data.url'),NULL,NULL,0,NULL,payload,id FROM records"
    import json
    from .models import ResourceMeasurement
    file_targets={row.url_hash:row for row in ResourceMeasurement.objects.filter(batch=batch,purpose='resource').only('url_hash','http_status')}
    rows=iter(connection.execute(query))
    while chunk:=list(islice(rows,25)):
        values=[]
        for url,sitemap,link,start,map_url,payload,frontier_id in chunk:
            data=json.loads(payload)['data'] if payload else {}
            status=data.get('final_status',data.get('status'))
            outcome='not_visited' if not payload else 'blocked' if data.get('blocked_redirect') or data.get('robots_blocked') or status in (403,429) else 'download_error' if data.get('error') else 'success' if status is not None and 200<=status<300 else 'http_error' if status is not None and status>=400 else 'response'
            file_target=file_targets.get(hashlib.sha256(url.encode()).hexdigest())
            if not payload and file_target:outcome='resource_only';status=file_target.http_status
            values.append(CrawlURLCoverage(batch=batch,url=url,url_hash=hashlib.sha256(url.encode()).hexdigest(),
                in_sitemap=bool(sitemap) if sitemap is not None else None,in_links=bool(link) if link is not None else None,
                is_start=bool(start),sitemap_url=map_url or '',outcome=outcome,http_status=status,
                queued=frontier_id is not None,attempted=bool(payload),
                error_code=data.get('error_code',''),depth=data.get('depth')))
        CrawlURLCoverage.objects.bulk_create(values,batch_size=25)
