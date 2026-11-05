"""Additional source-HTML advice; never substitute missing evidence with a pass."""
from urllib.parse import urlsplit
import re
from catalog.adapters import normalize_url

DEFAULT_SETTINGS={'title_min_chars':30,'title_max_chars':60,'description_min_chars':70,'description_max_chars':155,'heading_max_chars':70}

RULES={
 'title_too_long':('content','low','Titel boven ingestelde advieslengte','Beoordeel een bondigere titel; de adviesgrens is geen harde zoekmachinelimiet.'),
 'title_too_short':('content','low','Titel onder ingestelde advieslengte','Beoordeel of de titel de pagina voldoende beschrijft.'),
 'description_too_long':('content','low','Meta description boven advieslengte','Beoordeel een bondigere omschrijving; zoekmachines kunnen andere tekst tonen.'),
 'description_too_short':('content','low','Meta description onder advieslengte','Beoordeel of een specifiekere omschrijving nuttig is.'),
 'heading_too_long':('content','low','H1 of H2 boven advieslengte','Beoordeel een beknoptere kop in de getoonde context.'),
 'title_h1_same':('content','low','Titel en H1 zijn gelijk: beoordelen','Dit kan bewust zijn; beoordeel of beide hun eigen functie voldoende vervullen.'),
 'metadata_outside_head':('content','medium','Metadata buiten het hoofddocument-head','Plaats title, description en canonical in de head van het hoofddocument.'),
 'html_nested_document':('content','high','Meerdere HTML-documentstructuren in één pagina','Integreer de ingebedde inhoud zonder een tweede html/head/body-document; controleer ook de dubbele titel.'),
 'image_dimensions_missing':('assets','low','Afbeelding zonder opgegeven breedte of hoogte','Beoordeel afmetingen of een CSS-aspectratio; dit bewijst geen layoutshift.'),
 'image_dimensions_invalid':('assets','low','Ongeldige opgegeven afbeeldingsafmeting','Gebruik geldige positieve dimensies; beoordeel responsive weergave apart.'),
 'canonical_duplicate_identical':('indexability','low','Identieke canonical meerdere keren in HTML','Verwijder de overbodige dubbele HTML-tag; er is geen tegenstrijdige bestemming aangetoond.'),
 'canonical_conflicting':('indexability','high','Canonical-instructies noemen verschillende bestemmingen','Kies één consistente voorkeursbestemming voor HTML en HTTP-header.'),
 'canonical_fragment':('indexability','medium','Canonical bevat een fragment','Gebruik het bedoelde pagina-adres zonder fragment.'),
 'canonical_outside_head':('indexability','medium','Canonical buiten de hoofd-head','Plaats de canonical in de head van het hoofddocument.'),
 'url_underscores':('http','low','Pagina-URL bevat een underscore','Beoordeel leesbaarheid; verander bestaande URL’s niet zonder migratieplan.'),
 'url_parameters':('http','low','Pagina-URL bevat parameters: beoordelen','Bepaal de functie en voorkeurs-URL; verwijder functionele parameters niet automatisch.'),
 'url_nonstandard':('http','low','Bijzondere URL-schrijfwijze: beoordelen','Beoordeel hoofdletters, spaties, herhaalde slashes en URL-lengte in context.'),
 'external_blank_rel':('security','low','Externe link opent nieuw tabblad zonder expliciete noopener','Beoordeel expliciete rel-bescherming en browserondersteuning; moderne browsers beschermen veel _blank-links impliciet.'),
 'header_values_conflicting':('security','medium','Meerdere waarden voor een beveiligingsheader: beoordelen','Controleer de gecombineerde werking; meerdere CSP-beleidsregels kunnen bewust zijn.'),
}
HEADERS={'strict-transport-security':'HSTS','content-security-policy':'Content-Security-Policy',
         'x-content-type-options':'X-Content-Type-Options','x-frame-options':'X-Frame-Options','referrer-policy':'Referrer-Policy'}
for key,title in HEADERS.items():
    RULES['header_missing_'+key.replace('-','_')]=('security','low',title+' ontbreekt: beoordelen','Beoordeel toepassing van '+title+' voor deze pagina; dit is geen exploit- of compliance-oordeel.')


def settings_for(raw=None):
    if raw is not None and not isinstance(raw,dict):raise ValueError('Analyse-instellingen moeten een object zijn.')
    values={**DEFAULT_SETTINGS,**{key:value for key,value in (raw or {}).items() if key in DEFAULT_SETTINGS}}
    if any(isinstance(value,bool) or not isinstance(value,int) or not 0<=value<=4000 for value in values.values()):
        raise ValueError('Ongeldige analyse-adviesgrenzen.')
    if values['title_min_chars']>values['title_max_chars'] or values['description_min_chars']>values['description_max_chars'] or not values['heading_max_chars']:
        raise ValueError('Minimum moet onder maximum liggen; koplengte moet positief zijn.')
    return values


def local(rec):
    page=rec.page;facts=page.facts
    complete=bool(facts.get('html_audit',{}).get('complete')) and not page.coverage.get('analysis_truncated')
    metadata=facts.get('metadata_evidence')
    settings={**DEFAULT_SETTINGS,**getattr(rec,'settings',{})}
    def check(rule,bad,known=True,details=None,applicable=True):
        rec.check(rule,'not_applicable' if not applicable else 'issue' if bad else 'pass' if known else 'unknown',details)
    for field,prefix,minimum,maximum in [('titles','title',settings['title_min_chars'],settings['title_max_chars']),
                                        ('meta_descriptions','description',settings['description_min_chars'],settings['description_max_chars'])]:
        values=[value for value in facts.get(field,[]) if str(value).strip()]
        evidence=[item for item in metadata or [] if item['kind']==('title' if field=='titles' else 'description')]
        details={'values':values[:5],'minimum':minimum,'maximum':maximum,'evidence':evidence[:5],'basis':'configureerbare tekenlengte; geen pixel- of rankinggarantie'}
        check(prefix+'_too_long',any(len(value)>maximum for value in values),field in facts and not page.coverage.get('analysis_truncated'),details,bool(values) or field not in facts)
        check(prefix+'_too_short',any(len(value)<minimum for value in values),field in facts and not page.coverage.get('analysis_truncated'),details,bool(values) or field not in facts)
    headings=[value for key in ('h1','h2') for value in facts.get(key,[]) if value.strip()]
    check('heading_too_long',any(len(v)>settings['heading_max_chars'] for v in headings),'h1' in facts and 'h2' in facts and not page.coverage.get('analysis_truncated'),
          {'maximum':settings['heading_max_chars'],'evidence':[m for m in metadata or [] if m['kind'] in ('h1','h2') and len(m['text'])>settings['heading_max_chars']][:20]})
    title=(facts.get('titles') or [''])[0];h1=(facts.get('h1') or [''])[0]
    check('title_h1_same',bool(title and h1 and title.strip().casefold()==h1.strip().casefold()),'titles' in facts and 'h1' in facts,{'title':title,'h1':h1},bool(title and h1) or 'titles' not in facts or 'h1' not in facts)
    outside=[m for m in metadata or [] if m['kind'] in ('title','description') and m['in_head'] is False]
    check('metadata_outside_head',bool(outside),complete and not any(m['in_head'] is None for m in metadata or [] if m['kind'] in ('title','description')),{'elements':outside[:20]})
    structure=facts.get('document_structure',{})
    check('html_nested_document',any(structure.get(tag,0)>1 for tag in ('html','head','body')),complete,{'counts':structure,'metadata':(metadata or [])[:10],'interpretation':'raw source tags; browser may repair this structure'})
    images=facts.get('images',[])
    known=complete and not facts.get('images_truncated')
    missing=[i for i in images if 'width' in i and (not i['width'] or not i['height'])]
    invalid=[i for i in images if any(i.get(key) and (not re.fullmatch(r'[0-9]+',str(i[key])) or int(i[key])<=0) for key in ('width','height'))]
    check('image_dimensions_missing',bool(missing),known,{'images':missing[:20],'interpretation':'CSS kan afmetingen reserveren; geen bewezen layoutshift'})
    check('image_dimensions_invalid',bool(invalid),known,{'images':invalid[:20]})
    sources=facts.get('canonical_sources');canonical=facts.get('canonicals')
    identities={normalize_url(value)[0] for value in canonical or [] if normalize_url(value)}
    html=[s for s in sources or [] if s.get('source')=='html']
    known=canonical is not None and not page.coverage.get('analysis_truncated')
    details={'sources':sources,'normalized_destinations':sorted(identities)}
    check('canonical_conflicting',len(identities)>1,known and facts.get('headers_observed',False),details)
    check('canonical_duplicate_identical',len(html)>1 and len(identities)==1,known,details)
    check('canonical_fragment',any(urlsplit(value).fragment for value in canonical or []),known,details)
    empty=[m for m in metadata or [] if m['kind']=='canonical' and not m['raw_value'].strip()]
    if empty:rec.check('canonical_invalid','issue',{'elements':empty[:20]})
    outside=[m for m in metadata or [] if m['kind']=='canonical' and m['in_head'] is False]
    check('canonical_outside_head',bool(outside),complete and not any(m['in_head'] is None for m in metadata or [] if m['kind']=='canonical'),{'elements':outside[:20]})
    url=urlsplit(page.final_url)
    check('url_underscores','_' in url.path,True,{'url':page.final_url,'scope':'succesvolle HTML-pagina; bestanden apart beoordelen'})
    check('url_parameters',bool(url.query),True,{'url':page.final_url,'interpretation':'kan functioneel en correct zijn'})
    check('url_nonstandard',any(char.isupper() for char in url.path) or '//' in url.path or '%20' in url.path.lower() or len(page.final_url)>115,True,{'url':page.final_url})
    examples=[]
    for link in getattr(rec,'blank_links',[]):
        target=urlsplit(link['destination'])
        if (target.scheme,target.netloc)!=(url.scheme,url.netloc) and not {'noopener','noreferrer'}&set(link['rel'] or []):examples.append(link)
    check('external_blank_rel',bool(examples),complete and not page.coverage.get('links_truncated') and not getattr(rec,'blank_links_truncated',False),{'links':examples[:20],'interpretation':'aanvullend advies, moderne browserbescherming meewegen'})
    headers=facts.get('response_headers',{})
    for key in HEADERS:
        check('header_missing_'+key.replace('-','_'),facts.get('headers_observed',False) and not headers.get(key),
              facts.get('headers_observed',False) and key not in facts.get('headers_truncated',[]),{'header':key,'observed_value':headers.get(key)},key!='strict-transport-security' or url.scheme=='https')
    values=facts.get('response_header_values',{})
    duplicates={key:items for key,items in values.items() if key in HEADERS and len(set(items))>1}
    check('header_values_conflicting',bool(duplicates),facts.get('header_values_observed',False) and not facts.get('headers_truncated'),{'values':duplicates,'interpretation':'meerdere CSP-waarden zijn niet automatisch onjuist'})
