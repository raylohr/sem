"""Observed rendered links and bounded browser follow-up admission."""
from urllib.parse import urlsplit
from catalog.adapters import normalize_url as identity
from scans.network import normalize_url,ScanError
from scans.resource_discovery import known_file
from .models import DiscoveredLink,BrowserVisit


def retain_links(visit,run,response,server,rendered):
    original=set(server.get('outlinks',[]))
    phases={item.get('url'):item.get('phase','rendered_dom') for item in response.get('exploration',{}).get('links',[]) if isinstance(item,dict)}
    seen=set(run.visits.values_list('url_hash',flat=True));available=run.config['max_pages']-len(seen)
    DiscoveredLink.objects.filter(visit=visit).delete()
    rows=[]
    for link in rendered.get('outlinks',[])[:2000]:
        if link in original:continue
        try:
            url=normalize_url(link);url_hash=identity(url)[2]
        except (ScanError,TypeError):continue
        state='budget'
        try:normalize_url(url,run.config['allowed_hosts'])
        except ScanError:state='outside_scope'
        else:
            if known_file(url):state='file'
            elif urlsplit(url).path.startswith('/cdn-cgi/'):state='infrastructure'
            elif visit.scenario!='baseline':state='privacy_only'
            elif url_hash in seen:state='already_selected'
            elif available>0 and visit.depth<run.config['max_depth']:
                _,created=BrowserVisit.objects.get_or_create(run=run,url_hash=url_hash,scenario='baseline',defaults={'url':url,'depth':visit.depth+1,'discovery':'rendered_link'})
                if created:available-=1;seen.add(url_hash)
                state='queued_browser'
        if any(row.url_hash==url_hash for row in rows):continue
        rows.append(DiscoveredLink(visit=visit,url=url,url_hash=url_hash,phase=phases.get(link,'rendered_dom'),state=state,
            details={'source_url':visit.final_url,'source_scenario':visit.scenario,'source_visit_id':visit.pk,'basis':'observed rendered hyperlink; no proof of original HTTP reachability'}))
    DiscoveredLink.objects.bulk_create(rows,ignore_conflicts=True,batch_size=500)
