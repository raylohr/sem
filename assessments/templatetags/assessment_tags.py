from django import template
register = template.Library()
LABELS = {
    'main_content':'Hoofdinhoud en taalprofiel','structured_data':'Gestructureerde gegevens en validatielagen',
    'browser_title_width':'Titel boven modelmatige breedteadviesgrens','queued_browser':'Ingepland voor browser','budget':'Buiten browserbudget','already_selected':'Al geselecteerd','outside_scope':'Buiten hosttoestemming','privacy_only':'Alleen waargenomen in toestemmingsscenario','file':'Bestandslink','before_scroll':'Na laden, vóór scrollen','after_scroll':'Na scrollen','after_details':'Na openen uitklapdetail','title_width':'Modelmatige titelbreedte','exploration':'Begrensde browserverkenning',
    'resource_only':'Afzonderlijke bestandsmeting (geen inhoudspagina)', 'representation_complete':'Volledige representatie (geen gedeeltelijk antwoord)',
    'resource':'Bestand','external':'Externe link','success':'Succesvol gemeten','http_error':'HTTP-fout','download_error':'Download mislukt','not_measured_budget':'Niet gemeten: budget bereikt','not_measured_time':'Niet gemeten: tijd bereikt','interrupted_unknown':'Onderbroken: eindantwoord onbekend','redirect_blocked':'Redirect niet gevolgd','declared_bytes':'Opgegeven Content-Length (bytes)','wire_bytes':'Gelezen HTTP-bodybytes vóór decompressie','decoded_bytes':'Gelezen uitgepakte bytes','wire_complete':'Volledige HTTP-body gelezen','body_complete':'Volledige uitgepakte body beschikbaar','body_truncated':'Body afgekapt','body_error':'Fout bij uitpakken','resource_scan':'Bestands- en externe meetdekking','resource_checks':'Controles per uniek meetdoel',
    'content_audit':'Dekking inhoudsanalyse','heading_evidence':'Koppen met context','link_name_evidence':'Linknamen uit statische HTML','social_metadata':'Open Graph en X Cards','static_name':'Naam uit HTML','name_basis':'Herkomst naam','name_status':'Zekerheid naamafleiding','visible_text':'Tekstinhoud','known':'Afgeleid uit HTML','contents':'Tekst en afbeeldings-alt','aria-label':'ARIA-label','aria-labelledby':'Verwezen ARIA-labels','title':'Title-attribuut','count':'Aantal','basis':'Basis','reason':'Reden','interpretation':'Interpretatie','image_counts':'Afbeeldingen per beoordeling',
    'low':'Laag / advies','element_path':'Elementpad in bron-HTML','region':'Paginagebied','raw_url':'Oorspronkelijk adres','minimum':'Minimum tekens','maximum':'Maximum tekens','in_head':'In head van hoofddocument','observations':'Waarnemingen',
    'protected_email_links': 'Cloudflare-e-maillinks: browsercontrole nodig',
    'cloudflare_email_review_required': 'Cloudflare-e-mailbescherming: browsercontrole nodig',
    'privacy-signals':'Privacywaarnemingen', 'baseline':'Zonder interactie', 'no_choice':'Geen keuze', 'reject':'Weigeren', 'accept':'Accepteren', 'withdraw':'Intrekken',
    'before_choice':'Vóór keuze','after_reject':'Na weigeren','after_accept':'Na accepteren','after_withdraw':'Na intrekken',
    'cms':'CMS','plugin':'Plugin','framework':'Framework','library':'Library','crm_marketing':'CRM / marketing','analytics':'Analytics','behavior_analytics':'Gedragsanalyse','tag_management':'Tagmanagement','advertising':'Advertenties','consent':'Toestemming','chat':'Chat','payment':'Betalingen','ecommerce':'E-commerce','embed':'Embed','fonts':'Fonts','reviews':'Reviews','cdn_waf':'CDN / WAF','hosting':'Hosting-/netwerkaanwijzing','webserver':'Webserver','email_infrastructure':'Mailinfrastructuur',
    'present':'Handtekening aanwezig','request_observed':'HTTP-verzoek bevestigd','attempted_request':'Verzoek geprobeerd, niet bevestigd','cookie_observed':'Cookie waargenomen','dns_observed':'DNS-aanwijzing','network_observed':'ASN-aanwijzing','observed':'Waargenomen','inferred':'Afgeleid',
    'eu_publishers':'Uitgevers in EU-27','non_eu_publishers':'Uitgevers buiten EU-27','unknown_publishers':'Land uitgever onbekend','unknown_parent_countries':'Land moederorganisatie onbekend','non_eu_parents':'Moederorganisaties buiten EU-27','technologies_without_supplier':'Technologieën zonder bekende uitgever','technology_count':'Herkende technologieën','data_locations':'Gegevenslocaties','gdpr_assessment':'AVG-beoordeling','definition':'Profieldefinitie',
    'central_crm':'Centraal CRM volgens klant','usage':'Gebruik volgens klant','data_location':'Gedocumenteerde gegevenslocatie','contract_entity':'Contractuele entiteit','retention':'Bewaartermijn',

    'missing_titles': 'Ontbrekende titel', 'missing_meta_descriptions': 'Ontbrekende meta description',
    'missing_h1': 'Ontbrekende H1', 'multiple_titles': 'Meerdere titels',
    'multiple_meta_descriptions': 'Meerdere meta descriptions', 'multiple_h1': 'Meerdere H1-koppen',
    'duplicate_titles': 'Dubbele titel', 'duplicate_meta_descriptions': 'Dubbele meta description',
    'duplicate_text': 'Identieke paginatekst', 'link_to_404': 'Link naar een 404',
    'issue': 'Met bevindingen', 'pass': 'Zonder bevindingen', 'unknown': 'Onvoldoende gegevens',
    'not_applicable': 'Niet van toepassing', 'pages': 'Pagina’s', 'html_inspected': 'HTML onderzocht',
    'unknown_links': 'Pagina’s met onbekende linkinventaris', 'truncated_links': 'Pagina’s met afgekorte linkinventaris',
    'legacy_link_limit_unknown': 'Historische linkdekking niet volledig bekend', 'documents_recovered': 'HTML-documenten opnieuw uitgelezen',
    'batch_status_at_analysis': 'Status crawl bij analyse', 'batch_warnings': 'Crawlwaarschuwingen',
    'input_kind': 'Gebruikte invoer', 'scope': 'Reikwijdte', 'source_url': 'Verwijzende pagina',
    'destination': 'Bestemming', 'anchor_text': 'Ankertekst', 'rel': 'Linkattributen', 'locator': 'Positie in HTML',
    'target_status': 'HTTP-status bestemming', 'target_final_status': 'Eindstatus bestemming',
    'url': 'URL', 'field': 'Onderzocht veld', 'value': 'Waarde', 'values': 'Waarden',
    'http_status': 'HTTP-status', 'final_http_status': 'HTTP-status na redirects',
    'titles': 'Titels', 'meta_descriptions': 'Meta descriptions', 'h1': 'H1-koppen', 'h2': 'H2-koppen',
    'canonicals': 'Canonicals', 'robots': 'Robots-instructies', 'robot_directives': 'Herkomst robots-instructies',
    'redirects': 'Redirectstappen', 'indexability_reasons': 'Redenen indexeerbaarheid',
    'text_truncated': 'Tekst afgekort', 'text_extraction_basis': 'Methode tekstextractie', 'body_hash': 'Documenthash',
    'link_count': 'Aantal gevonden links', 'titles_count': 'Aantal titels', 'titles_lengths': 'Titellengtes',
    'meta_descriptions_count': 'Aantal meta descriptions', 'meta_descriptions_lengths': 'Descriptionlengtes',
    'h1_count': 'Aantal H1-koppen', 'h1_lengths': 'H1-lengtes', 'h2_count': 'Aantal H2-koppen', 'h2_lengths': 'H2-lengtes',
    'normalized_titles': 'Titel voor duplicaatvergelijking', 'normalized_meta_descriptions': 'Description voor duplicaatvergelijking',
    'links_known': 'Linkinventaris beschikbaar', 'link_details_known': 'Ankerteksten en linkdetails beschikbaar',
    'links_truncated': 'Linkinventaris afgekort', 'analysis_truncated': 'Analysegegevens afgekort',
    'recovered_from_document': 'Opgeslagen HTML opnieuw uitgelezen', 'document_id': 'Bewijsdocument',
    'headers_recovered': 'HTTP-headers opnieuw uitgelezen', 'crawl-basics': 'Pagina’s en links', 'browser-basics': 'Toegankelijkheid en resources',
    'pending': 'Ingepland', 'processing': 'Wordt onderzocht', 'complete': 'Voltooid', 'incomplete': 'Onvolledig', 'failed': 'Mislukt',
    'sample': 'Selectie uit crawl', 'rendered_link': 'JavaScript-link', 'added_links': 'Nieuwe links na JavaScript', 'added_link_count': 'Aantal nieuwe links',
    'dom_content_loaded_ms': 'DOM gereed (ms)', 'load_ms': 'Load-event (ms)', 'fcp_ms': 'First Contentful Paint (ms)',
    'lcp_ms': 'Waargenomen Largest Contentful Paint (ms)', 'cls': 'Waargenomen layoutverschuiving', 'document_height': 'Documenthoogte (px)',
    'measurement_kind': 'Meetmethode', 'eligible_source_pages': 'Geschikte pagina’s in broncrawl', 'initial_sample_pages': 'Geselecteerde startpagina’s',
    'sample_limit': 'Maximaal aantal pagina’s', 'sampling': 'Selectiemethode', 'artifact_bytes': 'Omvang bewijsbestanden (bytes)',
    'visited': 'Onderzochte pagina’s', 'incomplete_or_failed': 'Onvolledig of mislukt', 'sample_limit_reached': 'Paginabudget bereikt',
    'timed_out': 'Rendertijd bereikt', 'blocked': 'Geblokkeerde verzoeken', 'resource_count': 'Aantal resources', 'scenario': 'Onderzoeksscenario',
    'profile': 'Schermprofiel', 'viewport': 'Schermgrootte', 'transport': 'Netwerkbeleid en overdracht', 'a11y_incomplete': 'Toegankelijkheidsregels voor handmatige beoordeling',
    'a11y_automatic_only': 'Alleen automatische toegankelijkheidscontrole', 'images': 'Afbeeldingsgegevens', 'image_count': 'Aantal afbeeldingen',
    'image_inventory_limit': 'Maximaal aantal vastgelegde afbeeldingen', 'images_truncated': 'Afbeeldingsinventaris afgekort',
    'broken_resource': 'Kapotte browserresource', 'network_isolation': 'Netwerkafscherming', 'axe_sha256': 'Checksum toegankelijkheidsengine',

}


from assessments.advanced import RULES
from assessments.r4 import RULES as RESOURCE_RULES
LABELS.update({key: value[2] for key,value in {**RULES,**RESOURCE_RULES}.items()})
LABELS.update({'observed_inlinks':'Unieke interne verwijzende URL’s','unique_internal_outlinks':'Unieke interne doel-URL’s','graph_depth':'Linkafstand vanaf de start-URL','root':'Start-URL','scope':'Meetbereik','graph':'Interne linkstructuur','advanced_checks':'Dekking per controle','analysis_module':'Analysemodule'})

@register.filter
def assessment_label(value):
    return LABELS.get(value, value)


@register.filter
def assessment_value(value):
    if value is None:
        return 'Onbekend'
    if isinstance(value, bool):
        return 'Ja' if value else 'Nee'
    return value
