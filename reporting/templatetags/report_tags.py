from django import template
from reporting.actions import STATUSES,PRIORITIES,EFFORTS
from reporting.catalog import CATEGORIES
from assessments.templatetags.assessment_tags import LABELS as ANALYSIS_LABELS
register=template.Library()
LABELS={'crawl':'Webcrawl','browser':'Browseronderzoek','privacy:baseline':'Privacy: geen toestemmingsonderzoek','privacy:no_choice':'Privacy: zonder keuze','privacy:reject':'Privacy: weigeren','privacy:accept':'Privacy: accepteren','privacy:withdraw':'Privacy: intrekken',**ANALYSIS_LABELS,**CATEGORIES,**STATUSES,**PRIORITIES,**EFFORTS,'pending':'Ingepland','processing':'Wordt opgebouwd','complete':'Afgerond','failed':'Onderbroken',
    'measured':'Beoordeeld','insufficient':'Onvoldoende onderzocht','not_applicable':'Niet van toepassing','signals':'Signalen voor beoordeling',
    'observed':'Geen signalen binnen de onderzochte scenario’s','profile':'Afhankelijkheidsprofiel','created':'Aangemaakt','updated':'Gewijzigd',
    'verification':'Controle met nieuwe meting','linked':'Probleem gekoppeld','reopened':'Opnieuw beoordelen','pass':'Geslaagd',
    'a11y_contrast':'Contrast en herkenbaarheid van links','a11y_alt':'Alternatieve tekst bij afbeeldingen','a11y_structure':'Overige automatische toegankelijkheidscontroles','performance_field':'Veldmetingen gebruikerssnelheid','broken_resource':'Kapotte browserresources',
    'availability':'Bereikbaarheid','indexability':'Indexeerbaarheid','file_type':'Bestandstype','local':'Lokale controle','response':'Antwoord','hint':'Aanwijzing uit URL of HTML',
    'own':'Eigen website','external':'Externe website','same_page':'Binnen dezelfde pagina','action':'Andere actie','navigation':'Navigatielink','download':'Download','loaded_resource':'Geladen bestand','instruction':'Technische instructie','fragment':'Paginaonderdeel','contact':'Contactactie','issue':'Probleem vastgesteld','unknown':'Onbekend','not_tested':'Niet onderzocht','allowed':'Toegestaan','excluded':'Uitgesloten van onderzoek','local':'Lokaal gecontroleerd','invalid':'Ongeldig','unsupported':'Niet ondersteund',
    'remaining':'Signaal nog aanwezig','not_remeasured':'Niet opnieuw gemeten'}
@register.filter
def report_label(value):return LABELS.get(value,value)

from reporting.references import REASONS
LABELS.update(REASONS)
LABELS.update({'resource_http_error':'Bereikbaarheid bestanden','external_http_error':'Bereikbaarheid externe links','resource_mime_mismatch':'Bestandstype van geladen bestanden','resource_download_mime':'Bestandstype van downloads','resource_image_large':'Grootteadvies afbeeldingen','reference_fragment_missing':'Ontbrekend paginaonderdeel','reference_contact_invalid':'Ongeldige contactlink'})
