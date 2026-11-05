# R2 — HTML-extractie en directe controles

R2 volgt op R1 en omvat G09, G07, G12, G14, G15, G18, G19, G20 en G28 uit het implementatieplan. Extractie heet `html-v5`; analyses gebruiken regelversie 4 en extractor `assessment-v4`.

Nieuwe crawls bewaren elementpaden, bronposities, header/main/nav/footer-context, oorspronkelijke URL-attributen, target/rel, ARIA-verwijzingen en opgegeven afbeeldingsafmetingen, srcset en sizes. Dit is bron-HTML, geen gereconstrueerde browser-DOM of volledige accessibility tree. De aanvullende parser begrenst nodes, nestingsdiepte en collecties. Een afgekorte inventaris levert geen bewijs dat ontbrekende signalen afwezig zijn.

## Controles en instellingen

Het geavanceerde crawlformulier bevat tekenlengteadviezen: titel 30–60, description 70–155 en H1/H2 maximaal 70 tekens. De instellingen worden opgeslagen bij de scan en vastgezet in iedere analyse. Dit zijn configureerbare adviezen, geen harde zoekmachinegrenzen of pixelmetingen.

Nieuwe controles onderzoeken metadata buiten de hoofd-head, meerdere html/head/body-structuren, titels en koppen, opgegeven afbeeldingsafmetingen, URL-schrijfwijze en parameters, afzonderlijke securityheaders en externe links met target=_blank zonder expliciete noopener/noreferrer. Afmetingen bewijzen geen layoutshift; ontbrekende headers bewijzen geen exploit. Moderne browsers beschermen veel _blank-links impliciet. Deze signalen vragen beoordeling en hebben doorgaans lage ernst.

Canonicalanalyse onderscheidt herhaalde identieke HTML-instructies (laag advies) en verschillende genormaliseerde bestemmingen in HTML/HTTP-headers (hoog). Broninstructies blijven beschikbaar. De bestaande canonical-doelcontrole blijft ongeldige of ongeschikte bestemmingen beoordelen. Losse fragment-HTML zonder bewijs voor de head geeft geen zekere plaatsingsuitspraak.

## Ontsluiting en compatibiliteit

De linkpagina toont context, elementpad en oorspronkelijke attributen en kan op paginagebied filteren. Bevindingen tonen concrete waarden en locaties, aanbevelingen en verwijzingen naar oorspronkelijke bronrecords/bewijsdocumenten. De analyse-export bevat de nieuwe controles, instellingen en bewijssnapshots.

Het bestaande scoremodel `report-v1` behoudt zijn oorspronkelijke controles en gewichten. R2-adviezen worden in bevindingen getoond en veranderen niet automatisch de scoreformule. Het totale paginaoordeel kan door nieuw waargenomen adviezen wel naar ‘met bevindingen’ veranderen. Het oude canonical-meervoudsignaal blijft onderdeel van die versie van de score, maar de bevinding wordt vervangen door de specifiekere uitleg. Historische dossiers behouden hun opgeslagen uitkomsten.

Heranalyse met documentherstel kan opgeslagen HTML opnieuw uitlezen zonder netwerkverzoeken en zonder bronrecords of meetdatum te wijzigen. Historisch niet bewaarde HTTP-headers blijven onbekend. Heranalyse is geen nieuwe meting en bewijst geen herstel. Een nieuwe scan is nodig voor actuele websitegegevens.

## Verificatie

De volledige suite slaagde met 437 tests. Aanvullende gerichte tests controleren onder meer impliciete head-structuren, begrensde inventarissen en bewijspresentatie. De opgeslagen benchmark reproduceert op de 59 gemeenschappelijke succesvolle HTML-pagina’s 32 eerste titels langer dan 60 tekens. Herextractie van de opgeslagen calculatorpagina vindt twee html-, head- en body-elementen en metadata buiten de hoofd-head. Dit bewijst de beschreven R2-verbeteringen, niet volledige pariteit met Screaming Frog of Sitebulb.
