SAFE_HEADERS=('link','x-robots-tag','strict-transport-security','content-security-policy','x-content-type-options','x-frame-options','referrer-policy')
from .resource_transfer import SAFE_HEADERS as TRANSFER_HEADERS
SAFE_HEADERS=tuple(dict.fromkeys(SAFE_HEADERS+TRANSFER_HEADERS))


def extract(result):
    raw={key.lower():str(value) for key,value in result.headers.items() if key.lower() in SAFE_HEADERS}
    truncated={key for key,value in raw.items() if len(value)>8192}
    values={}
    for key,items in getattr(result,'header_values',{}).items():
        if key not in SAFE_HEADERS:continue
        remaining=8192;stored=[]
        for value in items[:20]:
            if len(value)>remaining:truncated.add(key)
            stored.append(value[:remaining]);remaining-=len(stored[-1])
        if len(items)>20:truncated.add(key)
        values[key]=stored
    return {'response_headers':{key:value[:8192] for key,value in raw.items()},'headers_truncated':sorted(truncated),
            'response_header_values':values,'header_values_observed':bool(getattr(result,'header_values_observed',False))}
