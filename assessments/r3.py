"""Content interpretation from bounded source HTML; no browser or network access."""
from collections import Counter, defaultdict
import re
from urllib.parse import urlsplit

RULES={
 'image_empty_alt_review':('assets','low','Lege alt-tekst: functie afbeelding beoordelen','Controleer of de afbeelding decoratief is. Laat alt="" staan bij decoratie; voeg alleen bij informatieve afbeeldingen passende tekst toe.'),
 'image_link_name_missing':('assets','medium','Afbeeldingslink zonder naam in statische HTML','Geef de link een passende naam via tekst, alt of ARIA. Controleer de uiteindelijke naam ook in de browser.'),
 'link_name_missing':('assets','medium','Link zonder naam in statische HTML','Voeg beschrijvende linktekst of een passende toegankelijke naam toe; controleer het resultaat in de browser.'),
 'link_name_generic':('content','low','Algemene linknaam in de inhoud: beoordelen','Maak de naam specifieker als het doel uit de omringende context onvoldoende blijkt; behoud een passende expliciete ARIA-naam.'),
 'heading_first_not_h1':('content','low','Eerste inhoudskop is geen H1: beoordelen','Beoordeel de start van de kopstructuur in de hoofdinhoud; navigatie en footer zijn afzonderlijk uitgesloten.'),
 'heading_h1_after_h2':('content','low','H1 staat na een H2 in de inhoud','Controleer de volgorde in de hoofdinhoud; pas de kopniveaus aan als de inhoudelijke hiërarchie niet klopt.'),
 'heading_content_jump':('content','medium','Kopniveau overgeslagen in de inhoud','Controleer de getoonde opeenvolgende koppen en herstel de hiërarchie waar nodig.'),
 'heading_repeated_h2':('content','low','Dezelfde H2-tekst komt terug in de inhoud','Beoordeel of de herhaalde kop bewust is of specifieker kan. Meerdere verschillende H2-koppen zijn toegestaan.'),
 'social_og_missing':('content','low','Open Graph-profiel onvolledig','Vul de relevante Open Graph-velden in als een voorspelbare deelpreview gewenst is.'),
 'social_x_missing':('content','low','X Card-profiel onvolledig','Vul de relevante Card-velden in; titel, omschrijving en afbeelding kunnen uit Open Graph worden afgeleid.'),
 'social_values_invalid':('content','medium','Social metadata bevat ongeschikte waarden','Herstel lege waarden, ongeldige absolute HTTP(S)-adressen of niet-ondersteunde Card-types. Bereikbaarheid wordt hier niet gemeten.'),
 'social_values_conflicting':('content','medium','Tegenstrijdige social metadata','Kies één waarde per enkelvoudig veld; meerdere Open Graph-afbeeldingen zijn toegestaan.'),
 'social_outside_head':('content','medium','Social metadata buiten de hoofd-head','Plaats de deelmetadata in de head van het hoofddocument.'),
}


def local(rec):
    facts=rec.page.facts;coverage=rec.page.coverage
    audit=facts.get('content_audit',{})
    complete=bool(audit.get('complete')) and not coverage.get('analysis_truncated')
    basis='statische HTML; CSS, JavaScript en accessibility tree niet beoordeeld'
    def check(rule, examples, known, **details):
        rec.check(rule,'issue' if examples else 'pass' if known else 'unknown',
                  {'elements':examples[:20],'count':len(examples),'basis':basis,**details})

    images=facts.get('images',[])
    ambiguous=[image for image in images if image.get('alt_interpretation')=='empty_needs_review' and not image.get('linked')]
    # Linked images are assessed through their link name, avoiding a second blanket alt warning.
    check('image_empty_alt_review',ambiguous,complete and not facts.get('images_truncated'),
          interpretation='lege alt is toegestaan voor decoratie; rol van afbeelding niet automatisch bewezen',
          image_counts=dict(Counter(image.get('alt_interpretation','unknown') for image in images)))
    links=facts.get('link_name_evidence',[])
    known_links=complete and 'link_name_evidence' in facts and not coverage.get('links_truncated') and not any(link.get('name_status')!='known' for link in links)
    nameless=[link for link in links if link.get('name_status')=='known' and not link.get('static_name') and not link.get('hidden')]
    check('image_link_name_missing',[link for link in nameless if link.get('images')],known_links)
    check('link_name_missing',[link for link in nameless if not link.get('images')],known_links)
    generic={'hier','klik hier','lees meer','meer','click here','read more','more','learn more'}
    check('link_name_generic',[link for link in links if link.get('name_status')=='known' and link.get('region') not in ('header','footer','nav') and link.get('static_name','').strip().casefold() in generic],known_links,
          interpretation='context kan de bestemming voldoende verduidelijken; geen automatische toegankelijkheids- of rankingfout')

    headings=[h for h in facts.get('heading_evidence',[]) if h.get('region') not in ('header','footer','nav') and not h.get('hidden')]
    known_headings=complete and audit.get('headings_complete',False) and not any(h.get('truncated') for h in headings)
    check('heading_first_not_h1',headings[:1] if headings and headings[0]['level']!=1 else [],known_headings,
          scope='hoofdinhoud en document; expliciete header/nav/footer en verborgen elementen uitgesloten')
    first_h2=next((i for i,h in enumerate(headings) if h['level']==2),None)
    late=[h for i,h in enumerate(headings) if h['level']==1 and first_h2 is not None and i>first_h2]
    check('heading_h1_after_h2',late,known_headings)
    jumps=[{'previous':a,'next':b} for a,b in zip(headings,headings[1:]) if b['level']>a['level']+1]
    check('heading_content_jump',jumps,known_headings)
    repeated=defaultdict(list)
    for heading in headings:
        if heading['level']==2 and heading.get('text') and not heading.get('truncated'):
            repeated[' '.join(heading['text'].split()).casefold()].append(heading)
    check('heading_repeated_h2',[{'text':values[0]['text'],'occurrences':values[:10],'count':len(values)} for values in repeated.values() if len(values)>1],known_headings)

    social=facts.get('social_metadata',[]);by_name=defaultdict(list)
    for entry in social:by_name[entry['name']].append(entry)
    known_social=complete and audit.get('social_complete',False) and not any(e.get('truncated') for e in social)
    def value(key):return next((e['value'].strip() for e in by_name.get(key,[]) if e['value'].strip()),'')
    missing_og=[key for key in ('og:title','og:type','og:url','og:image') if not value(key)]
    # Missing required values are an issue only when the retained inventory is complete.
    rec.check('social_og_missing','issue' if known_social and missing_og else 'pass' if known_social else 'unknown',
        {'missing':missing_og,'basis':'Open Graph-basisprofiel','sources':social[:20],'network_checked':False})
    card=value('twitter:card')
    requirements={'twitter:title':value('twitter:title') or value('og:title')}
    if card in ('','summary','summary_large_image','player'):
        requirements.update({'twitter:description':value('twitter:description') or value('og:description'),
                             'twitter:image':value('twitter:image') or value('og:image')})
    if card=='player':requirements.update({key:value(key) for key in ('twitter:player','twitter:player:width','twitter:player:height')})
    if card=='app':requirements['twitter:app:name:*']=next((value('twitter:app:name:'+platform) for platform in ('iphone','ipad','googleplay') if value('twitter:app:name:'+platform)),'')
    missing_x=(['twitter:card'] if not card else [])+[key for key,val in requirements.items() if not val]
    rec.check('social_x_missing','issue' if known_social and missing_x else 'pass' if known_social else 'unknown',
        {'missing':missing_x,'basis':'X Card-profiel met Open Graph-fallback; geen preview-rendering',
         'effective_values':requirements,'sources':social[:20],'network_checked':False})
    invalid=[]
    for entry in social:
        name=entry['name'];raw=entry['value'].strip()
        reason=None
        if not raw:reason='lege expliciete waarde'
        elif name in ('og:url','og:image','og:image:url','og:image:secure_url','og:video','og:video:url','og:audio','og:audio:url','twitter:image','twitter:image:src','twitter:player'):
            try:
                parsed=urlsplit(raw)
                valid=parsed.scheme in ('http','https') and bool(parsed.hostname) and not parsed.username and not re.search(r'\s',raw)
                parsed.port
            except ValueError:valid=False
            if not valid:reason='geen geldig absoluut HTTP(S)-adres'
        elif name=='twitter:card' and raw not in ('summary','summary_large_image','app','player'):reason='onbekend Card-type'
        elif name in ('og:image:width','og:image:height','twitter:player:width','twitter:player:height') and not re.fullmatch('[1-9][0-9]*',raw):reason='geen positieve dimensie'
        if reason:invalid.append({**entry,'reason':reason})
    check('social_values_invalid',invalid,known_social,network_checked=False)
    multi_prefixes=('og:image','og:video','og:audio')
    conflicts=[{'name':name,'values':[e['value'] for e in entries][:10],'sources':entries[:10]} for name,entries in by_name.items()
               if not name.startswith(multi_prefixes) and len({e['value'].strip() for e in entries if e['value'].strip()})>1]
    check('social_values_conflicting',conflicts,known_social)
    check('social_outside_head',[e for e in social if e.get('in_head') is False],known_social and not any(e.get('in_head') is None for e in social))
