"""Shared Googlebot directive interpretation; no network or Django dependency."""
import re


def google_tokens(facts):
    tokens=set()
    special={'max-snippet','max-image-preview','max-video-preview','unavailable_after'}
    for item in facts.get('robot_directives',[]):
        agent=item.get('agent','').lower()
        for part in item.get('value','').lower().split(','):
            match=re.match(r'^\s*([a-z][\w-]*):\s*(.*)',part)
            if item.get('source')=='header' and match:
                if match[1] in special:continue
                agent,part=match[1],match[2]
            if agent in ('robots','googlebot','unspecified',''):
                tokens.update(set(part.split()) & {'index','noindex','follow','nofollow','none','all'})
    if 'none' in tokens:tokens.update(('noindex','nofollow'))
    if 'all' in tokens:tokens.update(('index','follow'))
    return tokens
