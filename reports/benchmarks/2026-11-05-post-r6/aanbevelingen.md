# Aanbevolen vervolg na de productiebenchmark

Effort: 1 = kleine correctie, 5 = groot subsystem. Effect: 1 = beperkt, 5 = grote verbetering van betrouwbaarheid, dekking of bruikbaarheid van de totale scan. Dit zijn relatieve engineeringinschattingen, geen werkdagen of gemeten SEO-opbrengst. P0 corrigeert meet-/herkomstproblemen; P1 verbetert direct de bruikbaarheid; P2 verdiept later.

| ID | Prioriteit | Uitbreiding/correctie | Effort | Effect op totale scanwaarde |
|---|---|---|---:|---:|
| N01 | P0 | Statusclassificatie en doelresolver corrigeren | 3/5 | 5/5 |
| N02 | P0 | Gerichte herpogingen voor aparte doelen | 3/5 | 5/5 |
| N03 | P1 | Herstelopdrachten op component/oorzaak bundelen | 3/5 | 5/5 |
| N04 | P1 | Browserselectie en budgetbenutting verbeteren | 2/5 | 5/5 |
| N05 | P1 | Browserafhankelijkheden en volledige renderingmodus | 4/5 | 5/5 |
| N06 | P1 | Hoofdinhoudselectie per template versterken | 3/5 | 4/5 |
| N07 | P2 | Nieuwe scorecatalogus expliciet kalibreren | 3/5 | 4/5 |
| N08 | P2 | Afbeeldingsadvies koppelen aan werkelijk gebruik | 3/5 | 3/5 |
| N09 | P2 | Schemavalidatie uitbreiden op bewezen cases | 4/5 | 3/5 |
| N10 | P0 | Manifestversies consistent maken | 1/5 | 3/5 |
| N11 | P1 | Vergelijkingscorpus en SF-compatibele export verdiepen | 2/5 | 5/5 |

## N01 — Statusclassificatie en doelresolver corrigeren

**Aanleiding:** 11 externe 999-antwoorden krijgen pass; 355 link_to_5xx-controles onbekend terwijl aparte doelregisters bestaan.

**Uitwerking:** Classificeer onbekende/niet-ondersteunde statussen als unknown. Raadpleeg pagina-, resource- en externe metingen met behoud van scenario/datum. Beperk interne indexeerbaarheidscontroles tot toepasselijke interne pagina’s.

**Acceptatie:** Geen 999-pass; gerichte tests voor 200/redirect/404/403/429/999 en gemengde doelen. Herberekende dekking volgt echte meetzekerheid, geen kunstmatige verhoging.

## N02 — Gerichte herpogingen voor aparte doelen

**Aanleiding:** Eén SDK connection_reset en vijf interrupted_unknown-doelen blijven onbekend; pagina-herpogingen lossen dit niet op.

**Uitwerking:** Maak een nieuwe hermeetronde voor geselecteerde resource/externe doelen met begrensde GET-retries, back-off, ruimere timeout en oorspronkelijke poginghistorie. Maak onderscheid tussen nog niet gestart en echt onderbroken.

**Acceptatie:** Alle zes huidige onbekenden kunnen expliciet worden herhaald; oorspronkelijke metingen blijven bestaan; blijvende fouten blijven unknown, niet automatisch opgelost.

## N03 — Herstelopdrachten op component/oorzaak bundelen

**Aanleiding:** 182 voorstellen, waaronder 115 zware afbeeldingen; drie SDK-fouten los omdat de vierde onbekend is.

**Uitwerking:** Voeg bovenliggende opdrachten toe voor afbeeldingoptimalisatie, calculators, gedeelde headers en templates. Behoud elke onderliggende meting. Scheid herstellen/beoordelen/opnieuw meten; geen eis van exact vier fouten voor een integratiebundel.

**Acceptatie:** Calculatoropdracht bevat drie bevestigde fouten plus één hermeetvraag; geen fictieve vierde fout. Afbeeldingen worden als campagne met geprioriteerde lijst aangeboden; gebruiker bevestigt eigenaar en zakelijke prioriteit.

## N04 — Browserselectie en budgetbenutting verbeteren

**Aanleiding:** 15 pagina’s gebruikt bij budget 20; calculator met bekende SDK-problemen niet geselecteerd.

**Uitwerking:** Selecteer op bestaande bevindingen én templateclusters; voeg expliciete risicodoelen toe. Vul ongebruikte dynamische reserve achteraf met nog niet onderzochte representatieve pagina’s.

**Acceptatie:** Bij voldoende kandidaten 20/20 bruikbare selecties; calculator en relevante templates opgenomen; selectiegrond en dekking per template zichtbaar.

## N05 — Browserafhankelijkheden en volledige renderingmodus

**Aanleiding:** Alle 15 bezoeken gedeeltelijk; 30 fonts-aanvragen geblokkeerd; zes scrolls bereiken documenteinde niet.

**Uitwerking:** Toon afhankelijkheidskeuzes vooraf met effect op meting, behoud toestemming per host. Bied instelbaar verkenningsbudget en expliciete brede/full-render-modus; bewaar geweigerde/afgekapte context.

**Acceptatie:** Geautoriseerde afhankelijkheden laden binnen veilige broker; complete versus partial betrouwbaar; twee gecontroleerde browserprofielen en alle benodigde bronartefacten. Geen automatische toestemming voor nieuwe hosts.

## N06 — Hoofdinhoudselectie per template versterken

**Aanleiding:** 37/59 fallbacks en 48/59 leesbaarheidsuitkomsten onbekend; gehele crawl 277 onbekende thin-content-controles.

**Uitwerking:** Combineer semantische landmarks, contentdichtheid en templateherkenning; leg selectieregels vast en laat templates beoordelen. Houd NL/vaktaal en paginadoel gescheiden.

**Acceptatie:** Handmatig gelabelde relevante templates met reproduceerbare selectie; correct uitgesloten navigatie; onbekend bij twijfel; geen gefingeerd leesniveau.

## N07 — Nieuwe scorecatalogus expliciet kalibreren

**Aanleiding:** Report-v1 negeert nieuwe diagnostiek bewust; browser/privacydekking beperkt en interne-linkscope beïnvloedt technische dekking.

**Uitwerking:** Corrigeer eerst N01; ontwerp daarna een apart geversioneerde catalogus met toepasselijkheid, risico en dekking per meetfamilie. Onderbouw weging op regressiegevallen en expertbeoordeling.

**Acceptatie:** Oude dossiers houden hun formule; nieuwe formule uitlegbaar en getest; geen punten voor unknown; diagnostiek en zakelijke prioriteit blijven apart.

## N08 — Afbeeldingsadvies koppelen aan werkelijk gebruik

**Aanleiding:** 11 bestandsgroottes exact gereproduceerd, maar 115 gewichtsignalen zeggen nog niets over effectieve besparing.

**Uitwerking:** Combineer currentSrc/DPR/viewport met intrinsieke en weergegeven maten, compressie/formaten en hergebruik; prioriteer op gekozen bron en prominente toepassing.

**Acceptatie:** Onderbouwde kandidaatbesparing met methode en onzekerheid, geen exact besparingsgetal zonder representatief beeld en kwaliteitscontrole.

## N09 — Schemavalidatie uitbreiden op bewezen cases

**Aanleiding:** Deze cohort bevat geen ondersteunde markup; huidige rich-result-profielen beperkt tot Breadcrumb en Product snippet.

**Uitwerking:** Bouw positieve/negatieve fixtures voor contexten, grafen, verwijzingen en de relevante nieuwe profielen. Breid parser/validator pas uit tegen geversioneerde officiële definities.

**Acceptatie:** Syntaxis-, vocabulaire-, profiel- en unsupported-resultaten afzonderlijk bewezen op meerdere sites; versies en herkomst blijven zichtbaar; geen resultaatsgarantie.

## N10 — Manifestversies consistent maken

**Aanleiding:** Dossiermanifest noemt action-templates-v1, alle 182 voorstellen gebruiken action-templates-v2.

**Uitwerking:** Gebruik dezelfde versieconstante voor aanmaak, manifest en export; voeg integriteitscontrole toe.

**Acceptatie:** Nieuw dossier en alle voorstelprovenances hebben dezelfde versie; historische manifestfouten worden verklaard zonder meetbewijs te herschrijven.

## N11 — Vergelijkingscorpus en SF-compatibele export verdiepen

**Aanleiding:** Sterke vergelijking op één site, maar geen volledige Internal:All/issue/headerwaarden en geen SF-browserreferentie.

**Uitwerking:** Importeer volledige URL-/issuegegevens en bewaar configuratie, redirectlagen en definities. Voeg meerdere templates/sites, een JS-run en schema-cases toe; maak raw-versus-context kopvolgorde apart zichtbaar.

**Acceptatie:** Automatische setdiffs met per-URL bewijs; positieve en negatieve controlecases; geen pariteitspercentage op basis van ontbrekende/lege exports.

Uitvoeringsvolgorde: N10 en N01, daarna N02; vervolgens N03/N04 en de beperkte afhankelijkheidsverbetering uit N05. N06 en N11 leveren daarna betere onderzoeksdekking. De volledige renderingmodus, scorecatalogus en specialistische verdieping volgen op gevalideerde meetkwaliteit. Geen van deze aanbevelingen is in deze benchmark stilzwijgend geïmplementeerd.
