"""Independent main-content, syntax, vocabulary and selected rich-result controls."""
RULES={
 'main_content_thin':('content','low','Weinig hoofdinhoud voor dit vermoedelijke paginatype','Beoordeel doel, volledigheid en bruikbaarheid. Voeg geen tekst toe om uitsluitend een woordgrens te halen.'),
 'content_readability_review':('content','low','Lange zinnen in Nederlandse hoofdinhoud: beoordelen','Beoordeel begrijpelijkheid voor het bedoelde publiek. Juridische precisie en vaktaal kunnen bewust zijn.'),
 'structured_syntax_invalid':('structured_data','medium','JSON-LD bevat een syntaxisfout','Herstel de getoonde JSON-fout; vocabulaire en rich-result-profiel worden daarna afzonderlijk gecontroleerd.'),
 'structured_vocabulary_review':('structured_data','medium','Gestructureerde gegevens wijken af van het vocabulaireprofiel','Controleer type, veld en waardevorm tegen de vastgezette Schema.org-versie. Een profielmelding is geen volledige RDF/SHACL-uitspraak.'),
 'structured_rich_profile':('structured_data','medium','Gekozen rich-result-profiel onvolledig','Herstel de getoonde verplichte profielvelden en controleer daarna actuele zoekmachinevoorwaarden en zichtbare inhoud. Geldige markup garandeert geen zoekresultaatuitbreiding.'),
}


def local(rec):
    facts=rec.page.facts;content=facts.get('main_content');truncated=rec.page.coverage.get('analysis_truncated')
    if not content:
        rec.check('main_content_thin','unknown');rec.check('content_readability_review','unknown')
    else:
        known=content.get('complete') and not truncated and content.get('selection_confidence')=='explicit'
        minimum={'home':100,'article':200,'general':100}.get(content.get('page_type'))
        dynamic=rec.page.coverage.get('rendering')!='browser' and content.get('word_count',0)<50 and any(any(t in u for t in ('_next/','react','vue','nuxt')) for u in facts.get('resource_urls',[]))
        rec.check('main_content_thin','not_applicable' if minimum is None else 'unknown' if not known or dynamic else 'issue' if content['word_count']<minimum else 'pass',
            {'main_content':content,'minimum_words':minimum,'basis':'paginetype-afhankelijk advies; geen bewijs van lage kwaliteit of rankingeffect'})
        reading=content.get('readability',{})
        rec.check('content_readability_review','unknown' if not known or reading.get('status')!='measured' else 'issue' if reading['mean_words_per_sentence']>25 else 'pass',
            {'main_content':content,'interpretation':reading.get('reason')})
    data=facts.get('structured_data')
    if not data:
        for key in ('structured_syntax_invalid','structured_vocabulary_review','structured_rich_profile'):rec.check(key,'unknown')
        return
    syntax=data.get('syntax',[]);complete=data.get('complete') and not truncated
    errors=[entry for entry in syntax if entry['state']=='issue']
    rec.check('structured_syntax_invalid','issue' if errors else 'unknown' if not complete or any(e['state']=='unknown' for e in syntax) else 'pass' if syntax else 'not_applicable',{'elements':errors,'validator':data['version']})
    rec.check('structured_vocabulary_review','unknown' if truncated and data['schema_state']!='issue' else data['schema_state'],
        {'elements':data.get('schema_errors',[]),'unknown':data.get('unknown',[]),'vocabulary':data.get('vocabulary'),'scope':data.get('scope')})
    profiles=rec.schema_profiles
    selected=[p for p in data.get('profiles',[]) if p['profile'] in profiles]
    state='issue' if any(p['state']=='issue' for p in selected) else 'unknown' if not complete or any(p['state']=='unknown' for p in selected) else 'pass' if selected else 'unknown' if profiles and data['schema_state']=='unknown' else 'not_applicable'
    rec.check('structured_rich_profile',state,{'profiles':selected,'selected_profile_ids':profiles,'scope':'beperkte verplichte-veldcontrole; geen Google-weergavegarantie'})
