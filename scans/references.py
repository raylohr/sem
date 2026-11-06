"""Versioned reference classification, independent of permission and transport."""
from urllib.parse import urljoin, urlsplit, unquote
from .host_rules import navigation_hosts, excluded, resource_hosts
from .network import normalize_host, ScanError
from .scope import registered_domain
from .resource_discovery import known_file

VERSION='references-v1'
DESTINATIONS={'own':'Eigen website','external':'Externe website','same_page':'Binnen dezelfde pagina','action':'Andere actie'}
FUNCTIONS={'navigation':'Navigatielink','download':'Download','loaded_resource':'Geladen bestand',
           'instruction':'Technische instructie','fragment':'Paginaonderdeel','contact':'Contactactie'}


def classify(raw,source,config,*,function=None,download=False,mime='',raw_reference=None):
    raw=str(raw or '')[:4096];url=urljoin(source,raw)
    try:
        p=urlsplit(url);origin=urlsplit(source)
        same=(bool(p.fragment) or raw=='#' or raw_reference=='#') and (p.scheme,p.netloc,p.path,p.query)==(origin.scheme,origin.netloc,origin.path,origin.query)
        if p.scheme not in ('http','https'):
            return {'version':VERSION,'url':url,'destination':'action','function':function or ('contact' if p.scheme in ('mailto','tel') else 'action'),
                    'scope':'local' if p.scheme in ('mailto','tel') else 'unsupported','reason':'' if p.scheme in ('mailto','tel') else 'unsupported_scheme','http':False}
        # Classification describes an address; network permission (including
        # public DNS/IP validation) remains the transport's responsibility.
        host=(p.hostname or '').encode('idna').decode('ascii').lower().rstrip('.')
        if not host or any(c.isspace() for c in host):raise ValueError('Invalid host')
        roots=config.get('allowed_hosts') or [origin.hostname]
        root=config.get('domain') or registered_domain(roots[0])
        own=host==root or host.endswith('.'+root) or host in roots
        destination='same_page' if same else 'own' if own else 'external'
        role=function or ('fragment' if same else 'download' if download or known_file(url) else 'navigation')
        observed=mime.split(';')[0].lower().strip()
        if role=='navigation' and observed and observed not in ('text/html','application/xhtml+xml'):
            role='download'
        if same:scope='local'
        elif role=='loaded_resource':scope='excluded' if host not in resource_hosts(config) else 'allowed'
        elif own:scope='allowed' if host in navigation_hosts({**config,'allowed_hosts':roots}) else 'excluded'
        else:scope='excluded' if excluded(host,config.get('blocked_external_hosts',[])) else 'allowed' if config.get('check_external_links',True) or role in ('download','loaded_resource') else 'excluded'
        return {'version':VERSION,'url':url,'destination':destination,'function':role,'scope':scope,
                'reason':'host_excluded' if scope=='excluded' else '', 'http':not same,'observed_mime':observed,
                'type_basis':'response' if observed else 'hint'}
    except (ValueError,ScanError,TypeError,IndexError):
        return {'version':VERSION,'url':url,'destination':'action','function':function or 'navigation','scope':'invalid','reason':'invalid_url','http':False}


def candidates(data):
    for link in data.get('link_details',[]):
        if isinstance(link,dict):yield {**link,'function':None}
    if not isinstance(data.get('link_details'),list):
        for url in data.get('outlinks',[]):
            if isinstance(url,str):yield {'url':url,'function':None}
    for item in data.get('resource_candidates',[]):
        if isinstance(item,dict) and item.get('kind')!='download':yield {**item,'function':'loaded_resource'}
    for item in data.get('canonical_sources',[]) or [{'url':u,'source':'unknown'} for u in data.get('canonicals',[])]:
        yield {**item,'function':'instruction','instruction':'canonical'}
    for item in data.get('hreflang',[]):yield {**item,'function':'instruction','instruction':'hreflang'}


def local_check(reference,data):
    p=urlsplit(reference['url'])
    if reference['scope']=='invalid':return 'issue','invalid_url'
    if reference['scope'] in ('excluded','unsupported'):return 'not_tested',reference['reason']
    if reference['function']=='fragment':
        target=unquote(p.fragment)
        if target=='' or ':~:text=' in target:return 'not_applicable','browser_fragment_directive'
        if not data.get('fragment_targets_complete'):return 'unknown','fragment_targets_unavailable'
        return ('pass','fragment_exists') if target in data.get('fragment_targets',[]) else ('issue','fragment_missing')
    if reference['function']=='contact':
        if p.scheme=='mailto':
            from django.core.validators import validate_email
            from django.core.exceptions import ValidationError
            try:
                addresses=unquote(p.path).split(',')
                for address in addresses:validate_email(address)
                return 'pass','syntax_only'
            except ValidationError:return 'issue','invalid_email_syntax'
        value=unquote(p.path)
        import re
        return ('pass','syntax_only') if re.fullmatch(r'\+?[0-9(). -]+(?:;ext=[0-9]+)?',value or '') and any(c.isdigit() for c in value) else ('issue','invalid_phone_syntax')
    return None,None
