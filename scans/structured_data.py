"""Pinned vocabulary-profile checks, separate from JSON syntax and Google profile checks."""
import json
import math
import re
from datetime import date,datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin,urlsplit

PROFILE_IDS=('google-breadcrumb-v1','google-product-snippet-v1')
PROFILES={
 'google-breadcrumb-v1':{'type':'BreadcrumbList','source':'https://developers.google.com/search/docs/appearance/structured-data/breadcrumb','reviewed':'2026-11-05'},
 'google-product-snippet-v1':{'type':'Product','source':'https://developers.google.com/search/docs/appearance/structured-data/product-snippet','reviewed':'2026-11-05'},
}


@lru_cache(maxsize=1)
def vocabulary():return json.loads((Path(__file__).parent/'data/schema/schemaorg-30.1.json').read_text())


def term(value):
    if not isinstance(value,str):return None
    for prefix in ('https://schema.org/','http://schema.org/','schema:'):
        if value.startswith(prefix):return value[len(prefix):]
    if ':' not in value and '/' not in value:return value
    return None


def values(value):return value if isinstance(value,list) else [] if value is None else [value]


def ancestors(kind):
    seen=set();pending=[kind]
    while pending and len(seen)<200:
        item=pending.pop()
        if item in seen:continue
        seen.add(item);pending.extend(vocabulary()['types'].get(item,{}).get('parents',[]))
    return seen


def known_context(context):
    if isinstance(context,str):return context.rstrip('/') in ('https://schema.org','http://schema.org')
    if isinstance(context,dict):
        return bool(context) and set(context)<= {'@vocab','schema','@language','@version'} and any(known_context(context.get(key)) for key in ('@vocab','schema'))
    return False


def evaluate(entities,syntax,complete,base):
    errors=[];unknown=[];nodes=[];profiles=[]
    vocab=vocabulary()
    def error(entity,path,reason,value=None):
        if len(errors)<30:errors.append({'format':entity['format'],'locator':entity['locator'],'path':path,'reason':reason,'value':str(value)[:200] if value is not None else None})
    for entity in entities:
        if entity.get('unsupported'):
            unknown.append({'locator':entity['locator'],'reason':entity['unsupported']});continue
        root=entity['data'];context=root.get('@context') if isinstance(root,dict) else None
        if entity['format']=='jsonld' and not known_context(context):
            unknown.append({'locator':entity['locator'],'reason':'context ontbreekt of valt buiten ondersteund schema.org-profiel; geen externe context opgehaald'});continue
        pending=[(root,'$',0)];steps=0
        while pending and len(nodes)<200 and steps<2000:
            steps+=1
            data,path,depth=pending.pop()
            if depth>12:unknown.append({'locator':entity['locator'],'reason':'nestingsgrens'});continue
            if isinstance(data,list):
                pending.extend((v,path+'['+str(i)+']',depth+1) for i,v in enumerate(data[:100]));continue
            if not isinstance(data,dict):continue
            if '@context' in data and data is not root and not known_context(data['@context']):unknown.append({'locator':entity['locator'],'reason':'afwijkende geneste context'});continue
            kinds=[term(t) for t in values(data.get('@type'))]
            if any(k is None for k in kinds):unknown.append({'locator':entity['locator'],'reason':'type uit andere woordenlijst'});continue
            if kinds:
                nodes.append((data,path,entity))
                for kind in kinds:
                    if kind not in vocab['types']:error(entity,path+'.@type','onbekend type in schema.org 30.1',kind)
                lineage=set().union(*(ancestors(k) for k in kinds))
                for key,value in data.items():
                    if key.startswith('@'):
                        if key not in ('@id','@type','@context','@graph'):unknown.append({'locator':entity['locator'],'reason':'niet-ondersteunde JSON-LD-constructie '+key})
                        continue
                    name=term(key)
                    if name is None:unknown.append({'locator':entity['locator'],'reason':'veld uit andere woordenlijst'});continue
                    prop=vocab['properties'].get(name)
                    if not prop:error(entity,path+'.'+key,'onbekend veld in vastgezette woordenlijst',name);continue
                    if prop['domains'] and not lineage.intersection(prop['domains']):error(entity,path+'.'+key,'veld past niet bij het gekozen typeprofiel',name)
                    for item in values(value):
                        literal=item.get('@value') if isinstance(item,dict) and '@value' in item else item
                        ranges=prop['ranges']
                        if isinstance(literal,(list,dict)):
                            if isinstance(literal,dict) and '@type' in literal:
                                child=[term(t) for t in values(literal['@type'])]
                                if child and all(t in vocab['types'] for t in child) and ranges and not any(ancestors(t).intersection(ranges) for t in child):error(entity,path+'.'+key,'genest type past niet bij waardeprofiel',child)
                            elif isinstance(literal,dict) and '@id' not in literal:unknown.append({'locator':entity['locator'],'reason':'ongetypeerde geneste waarde'})
                        elif ranges and not literal_matches(literal,ranges,base):error(entity,path+'.'+key,'waarde past niet bij letterlijk waardeprofiel',literal)
            for key,value in data.items():
                if key!='@context' and isinstance(value,(dict,list)):pending.append((value,path+'.'+key,depth+1))
        if pending:unknown.append({'locator':entity['locator'],'reason':'begrensde entiteits-/verwerkingsinventaris'})
    if len(nodes)>=200 or any(s.get('state')=='unknown' for s in syntax):unknown.append({'reason':'entiteitsgrens bereikt'})
    for data,path,entity in nodes:
        kinds={term(t) for t in values(data.get('@type'))}
        for key,profile in PROFILES.items():
            if profile['type'] not in kinds:continue
            problems=[];uncertain=[]
            if profile['type']=='BreadcrumbList':
                items=values(data.get('itemListElement'))
                if len(items)<2:problems.append('minimaal twee ListItems ontbreken')
                for i,item in enumerate(items):
                    if not isinstance(item,dict):uncertain.append('ListItem niet als object ondersteund');continue
                    if '@id' in item and len(item)==1:uncertain.append('verwezen ListItem niet opgelost');continue
                    if 'ListItem' not in {term(t) for t in values(item.get('@type'))}:problems.append('breadcrumb is geen benoemd ListItem')
                    position=item.get('position')
                    try:valid_position=not isinstance(position,bool) and int(position)==i+1 and str(position).isdigit()
                    except (ValueError,TypeError):valid_position=False
                    if not valid_position:problems.append('positie ontbreekt of is niet opeenvolgend vanaf 1')
                    target=item.get('item');target_name=target.get('name') if isinstance(target,dict) else None
                    if not item.get('name') and not target_name:problems.append('breadcrumbnaam ontbreekt')
                    if i<len(items)-1 and not target:problems.append('item ontbreekt vóór laatste breadcrumb')
                    if target:
                        target_url=target.get('@id') if isinstance(target,dict) else target
                        try:valid_url=isinstance(target_url,str) and not re.search(r'\s',target_url) and urlsplit(urljoin(base,target_url)).scheme in ('http','https') and bool(urlsplit(urljoin(base,target_url)).hostname)
                        except ValueError:valid_url=False
                        if not valid_url:problems.append('breadcrumb-item mist een bruikbare web-URL of @id')
            elif profile['type']=='Product':
                if not data.get('name'):problems.append('productnaam ontbreekt')
                present=[name for name in ('offers','review','aggregateRating') if data.get(name)]
                if not present:problems.append('offers, review of aggregateRating ontbreekt')
                for offer in values(data.get('offers')):
                    if not isinstance(offer,dict):uncertain.append('offer niet als object ondersteund');continue
                    if '@id' in offer and len(offer)==1:uncertain.append('verwezen offer niet opgelost');continue
                    types={term(t) for t in values(offer.get('@type'))}
                    if 'AggregateOffer' in types:
                        if offer.get('lowPrice') is None or not offer.get('priceCurrency'):problems.append('AggregateOffer mist lowPrice of priceCurrency')
                    elif 'Offer' in types:
                        spec=offer.get('priceSpecification',{})
                        if offer.get('price') is None and (not isinstance(spec,dict) or spec.get('price') is None):problems.append('Offer mist price of priceSpecification.price')
                    else:uncertain.append('offer-type onbekend voor dit profiel')
                if data.get('review') or data.get('aggregateRating'):uncertain.append('review/rating-profiel buiten deze beperkte productcontrole')
            profiles.append({'profile':key,'locator':entity['locator'],'path':path,'state':'issue' if problems else 'unknown' if uncertain or not complete or any(e.get('locator')==entity['locator'] for e in errors) else 'pass',
                'errors':problems[:20],'unknown':uncertain[:10],'source':profile['source'],'reviewed':profile['reviewed'],
                'scope':'beperkte verplichte-veldcontrole; zichtbare inhoud, beleid, indexering en Google-weergave niet bewezen'})
    has_markup=bool(entities or syntax)
    syntax_invalid=any(row['state']=='issue' for row in syntax)
    return {'version':'structured-profile-v1','vocabulary':'schema.org-30.1','vocabulary_sha256':vocab['source_sha256'],
        'complete':complete,'detected':has_markup,'entity_count':len(nodes),'formats':sorted({e['format'] for e in entities}|{s['format'] for s in syntax}),
        'syntax':syntax[:30],'schema_errors':errors,'unknown':unknown[:20],
        'schema_state':'issue' if errors else 'not_applicable' if complete and not has_markup else 'unknown' if unknown or syntax_invalid or not complete or not nodes else 'pass',
        'profiles':profiles[:50],'scope':'bounded vocabulary/domain/range profile; not a full JSON-LD/RDF/SHACL processor or rich-result eligibility guarantee'}


def literal_matches(value,ranges,base):
    if value is None:return True
    if isinstance(value,bool):return 'Boolean' in ranges
    if isinstance(value,(int,float)) and not math.isfinite(value):return False
    if isinstance(value,(int,float)):return bool(set(ranges)&{'Number','Float'}) or 'Integer' in ranges and float(value).is_integer()
    if not isinstance(value,str):return False
    enumerations=[r for r in ranges if 'Enumeration' in ancestors(r)]
    if enumerations and 'Text' not in ranges:
        member=term(value)
        return bool(member in vocabulary().get('members',{}) and any(ancestors(t).intersection(ranges) for t in vocabulary()['members'][member]))
    if 'Text' in ranges:return True
    if 'URL' in ranges:
        try:return not re.search(r'\s',value) and bool(urlsplit(urljoin(base,value)).scheme)
        except ValueError:return False
    if 'Date' in ranges:
        try:date.fromisoformat(value);return True
        except ValueError:pass
    if 'DateTime' in ranges:
        try:datetime.fromisoformat(value.replace('Z','+00:00'));return True
        except ValueError:pass
    if set(ranges)&{'Number','Integer','Float'}:
        try:
            number=float(value)
            return math.isfinite(number) and (bool(set(ranges)&{'Number','Float'}) or 'Integer' in ranges and number.is_integer())
        except ValueError:pass
    # Schema.org permits text/URL fallbacks for object-valued properties. Do not invent strict RDF errors.
    return not set(ranges)<= {'Date','DateTime','Number','Integer','Float','Boolean'}


def reject_constant(value):
    raise ValueError('Niet-standaard JSON-constante')


def strict_json(value):return json.loads(value,parse_constant=reject_constant)
