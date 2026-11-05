# Benchmark na R6 — vandersteenhoven.nl

Onderzocht op 5 november 2026, productiecommit `da1518a`. Referentie: de aangeleverde Screaming Frog 25.0 Unlicensed / Text Only-export van 4 november 2026. Dit is een nieuwe productiecrawl en browsersteekproef, geen offline herberekening van alleen oude HTML.

## Conclusie

De oorspronkelijke extractiegaten zijn sterk verminderd. Op dezelfde 59 HTML-pagina’s reproduceren we de getelde basisgegevens en de hyperlink-/afbeeldingsrelaties exact. Bestanden en externe doelen hebben nu eigen metingen. De 11 zware referentieafbeeldingen hebben exact dezelfde bytegroottes als SF.

Volledige gelijkwaardigheid is niet aangetoond. Eén van vier SDK-scripts blijft onbekend na een verbindingsfout. Drie directe SF-resources zijn door hostbeleid geblokkeerd. De browsersteekproef omvat 15 pagina’s, alle gedeeltelijk; de calculator ontbreekt. De herstelagenda bevat 182 voorstellen, waarvan 115 afzonderlijke zware-afbeeldinggroepen. Er zijn bovendien twee concrete analysedefecten: niet-standaard status 999 wordt als pass beoordeeld en linkcontroles benutten de verschillende meetregisters en hun toepassingsgebied onvoldoende.

**Advies: herstel eerst classificatie, herpogingen en integratie van metingen; verbeter daarna browserselectie en bundeling van opdrachten. Meer meetvelden alleen lossen de resterende problemen niet op.**

## 1. Uitgevoerde onderzoeken en eenheden

| Onderdeel | Nieuwe meting |
|---|---:|
| Crawlbatch | `3e99577e-1f2e-4721-a4a5-d9084e133bb0` |
| Hoofdcrawl | Afgerond; 364 URL-records, geen aanvraagfouten |
| Antwoorden met succesvolle eindstatus | 356 records, inclusief oorspronkelijke adressen met redirect |
| Unieke succesvolle HTML-eindadressen | 353 |
| HTTP-foutantwoorden in hoofdcrawl | 8 |
| Sitemapdekking | Alle 327 URL’s geprobeerd; negen sitemapdocumenten verwerkt |
| Bestandsdoelen | 631 |
| Externe linkdoelen | 191 |
| Bewaarde bestands-/doelverwijzingen | 35.687 |
| Browserrun | `dd59e5ba-7441-4688-be95-eefaa1d19413` |
| Browsermeting | 15 bezochte URL’s, alle HTTP 200, alle gedeeltelijk |
| Gecombineerd dossier | `7397270d-7d11-4f10-ba28-d34732412f07` |
| Dossierpresentatie | 359 genormaliseerde crawl-URL’s, 15 browser-URL’s, 182 groepen, 5.746 waarnemingen |

Recordaantallen, genormaliseerde bronadressen en unieke eindadressen zijn verschillende eenheden. De 359 dossier-URL’s zijn daarom niet hetzelfde als 364 crawlrecords of 353 succesvolle HTML-eindadressen.

Instellingen: sitemapontdekking; max. 10.000 URL’s, diepte 20, vijf aanvragen/seconde, maximaal vier gelijktijdige pagina-aanvragen; connect-timeout 15 s, read-timeout 60 s en maximaal drie paginapogingen. Bestanden: 500 MiB totaalbudget, 10 MiB per bestand, CSS-diepte drie. Externe links: maximaal 1.000 doelen. Browser: maximaal 20 pagina’s, 60 s per bezoek, desktop, maximaal 100 aanvragen/20 MiB per bezoek. Robotsbeleid: auditmodus, zoals expliciet in de scan vastgelegd. Bestaande hosttoestemmingen zijn behouden.

De hoofdcrawl is volledig binnen de ontdekte scope. Dit betekent niet dat ieder bestand, alle JavaScript-inhoud of de hele website volledig onderzocht is. De aparte fasen bewaren hun eigen onbekenden.

## 2. Gelijke HTML-cohort: 59 pagina’s

| Meetgegeven | SF | Nieuwe crawl | Resultaat |
|---|---:|---:|---|
| Ontbrekende meta description | 59 | 59 | Aantal gelijk |
| Ontbrekende H1 | 22 | 22 | Aantal gelijk |
| Meerdere titels | 1 | 1 | Dezelfde calculator herkenbaar |
| Eerste titel >60 tekens | 32 | 32 | Nu ook een actieve analyseregel |
| Self-canonical, één bestemming | 59 | 59 | Aantal gelijk |
| Afbeeldingsverwijzingen | 965 | 965 | Aantal gelijk |
| Unieke pagina/afbeelding-paren | 456 | 456 | Exact dezelfde set |
| Unieke HTTP(S)-hyperlinkparen | 828 | 828 | Exact dezelfde set |
| Lege alt-teksten | 965 | 965 | Geen automatische uitspraak over defecten |
| Ontbrekend alt-attribuut | 0 | 0 | Anders dan leeg attribuut |
| Afbeeldingen met opgegeven dimensies | — | 965 | Breedte/hoogte en bronlocatie nu vastgelegd |

SF levert niet voor iedere issue een volledige URL-lijst. Gelijke aantallen bewijzen daar niet automatisch dezelfde classificatie per URL. Setgelijkheid voor de 456 en 828 paren is wel afzonderlijk gecontroleerd. URL-normalisatie voor de setvergelijking verwijdert fragmenten en vult een leeg pad aan met `/`; querystrings blijven behouden.

### Koppen: afwijkende definitie, geen bewezen gemiste 36 fouten

Uit de ruwe kopvolgorde reproduceren we op 37 pagina’s een H1 na een H2, overeenkomstig het eerdere SF-overzicht. Na uitsluiting van expliciete navigatie/header/footer signaleren we 22 pagina’s waarvan de eerste inhoudskop geen H1 is en één pagina met een overgeslagen inhoudskopniveau. De controle op een H1 ná H2 in de inhoud geeft in deze cohort geen melding. Het verschil is inhoudelijke afbakening. Voeg bij voorkeur een apart SF-compatibel ruwe-volgorde-overzicht toe; verander de contextuele beoordeling niet stilzwijgend in dezelfde regel.

## 3. Bestanden: inventarisdekking is niet hetzelfde als meetzekerheid

Alle **119 unieke directe bestandsdoelen** uit de SF-linkexport staan in ons register: 52 CSS-, 33 JavaScript- en 34 afbeeldingsdoelen. Dit zijn verwijzingstypen, geen uitsplitsing naar het uiteindelijke Content-Type.

Binnen die 119: 112 succesvolle doelen, drie HTTP-fouten, drie beleidsblokkades en één downloadfout. De blokkades betreffen Google Fonts, jsDelivr/lucide en Tailwind CDN. Deze zijn dus ontdekt, maar niet daadwerkelijk door onze bestandsfase doorgemeten. Een automatische scopeverruiming is niet uitgevoerd.

Over de volledige site: 596 succesvolle bestandsdoelen, vier HTTP-fouten, 27 blokkades, één downloadfout en drie onderbroken/onbekende aanvragen. Het register bewaart ongeveer 64,2 MB ontvangen bestandsbytes. Er is geen bewijs dat deze hoeveelheid gelijk is aan een onbeperkte browserdownload.

### SDK-scripts op de calculator

| Script | SF | Nieuwe crawl |
|---|---:|---|
| telemetry_sdk.js | 404 | 404 bevestigd |
| editing_sdk.js | 404 | 404 bevestigd |
| resizing_sdk.js | 404 | 404 bevestigd |
| data_sdk.js | 404 | Verbinding afgebroken; eindstatus onbekend |

De downloadfout heeft code `connection_reset`. De browsersteekproef heeft de calculator niet geselecteerd en vult dat bewijs niet aan. De huidige voorstelregel bundelt uitsluitend exact vier bewezen SDK-404-groepen. In deze echte meting blijven daarom drie SDK-opdrachten los staan. Dat is een concrete tekortkoming in robuustheid en actionability. Gewenst: bundeling op gedeelde calculator/integratie, met expliciet drie bevestigde fouten en één te herhalen meting. Geen verzonnen vierde 404.

### Zware afbeeldingen

Alle 11 zware afbeeldingen uit de SF-referentie zijn succesvol gemeten. Alle 11 gemeten bytegroottes zijn exact gelijk aan SF. De drempel is 102.400 bytes; dit is een adviesgrens. Over de volledige crawl zijn 115 zware-afbeeldinggroepen gevonden. Het register bevat relaties naar bronpagina’s en bestanden.

Wat nog ontbreekt: een betrouwbare berekening van overmaat/besparing op basis van gekozen responsive bron, weergavegrootte en visuele kwaliteit. “Groter dan 100 KiB” is geen aangetoonde performancefout en rechtvaardigt geen 115 los geprioriteerde ontwikkeltaken.

## 4. Externe doelen en statusinterpretatie

Alle 70 externe hyperlinkdoelen uit de SF-linkexport staan in het nieuwe externe register: 52 succesvol, 17 met registeruitkomst http_error, één onderbroken/onbekend. SF’s eerdere 73 externe adressen omvatten ook andere doelsoorten; die teller is niet gelijk aan 70 externe hyperlinkdoelen.

- De Apple Podcasts-404 uit SF is opnieuw als 404 gemeten.
- Alle 22 Rechtspraak-bestemmingen met SF-status 403 geven nu HTTP 200. Dat bewijst bereikbaarheid op dit meetmoment, niet inhoudelijke correctheid of identieke toegang voor iedere bezoeker.
- Zeven SF-hyperlinks met eerste status 302 eindigen bij ons op 404. Vergelijk een eerste redirectstatus niet rechtstreeks met een eindstatus. Beoordeel de redirectketen en bestemming; het resultaat mag niet zonder meer als een nieuwe fout sinds gisteren worden beschreven.
- Niet-standaard status 999 komt ook in SF voor. Over onze volledige externe inventaris zijn er **11** van deze antwoorden.

**Gevonden classificatiedefect:** het register noemt 999 een http_error, terwijl `external_http_error` deze antwoorden als pass telt. Daardoor bevat de regeldekking 181 passes bij slechts 170 HTTP-200-antwoorden. Een onbekende/niet-ondersteunde status moet geen positieve bereikbaarheidsbeoordeling krijgen. De 11 gevallen moeten apart als onbekend/toegang niet vastgesteld worden beoordeeld. Er zijn acht echte 404-meldingen en twee onderbroken externe aanvragen in de volledige set.

## 5. Browserfase: bruikbare waarnemingen, beperkte dekking

Er zijn 15 pagina’s bezocht, alle HTTP 200, geen bezoek-time-outs. Alle bezoeken blijven gedeeltelijk. Er zijn 30 geblokkeerde Google Fonts-aanvragen. De browser bewaart 989 resource-antwoorden met HTTP 200, screenshots, DOM, oorspronkelijke HTML en meetcontext.

Voor 179 waargenomen afbeeldingselementen zijn intrinsieke dimensies vastgelegd; onzichtbare elementen kunnen terecht geen weergegeven afmeting hebben. Alle 15 pagina’s hebben een titelbreedteschatting. Bij negen bezoeken bereikte de begrensde scroll het documenteinde; bij zes niet.

De instelling was maximaal 20 pagina’s, met vijf plaatsen gereserveerd voor dynamisch ontdekte doelen. Er ontstond geen nieuwe bewaarde doelwachtrij, waardoor de reserve ongebruikt bleef. De selectie bevat veel vroeg gesorteerde artikelpaden, contactvarianten en enkele functionele pagina’s, maar niet de calculator met bekende bestandsproblemen. Deze selectie is geen gevalideerde template-steekproef.

Vervolg: neem bekende fout-/risicopagina’s gericht op, cluster op daadwerkelijke paginatemplates en vul ongebruikte reserveplaatsen aan. Bied daarnaast een expliciete volledige renderingmodus. Externe afhankelijkheden blijven een afzonderlijke hostkeuze. De huidige fonts-blokkade maakt onder meer contrast, tekstgeometrie en vormgeving gedeeltelijk.

De SF-run was Text Only. De kolommen voor echte/weergegeven afbeeldingsafmetingen en diverse browser-/AI-controles vormen dus geen vergelijkingsreferentie. Onze pixelschatting gebruikt DejaVu Sans/20 px en 600 px adviesbreedte; SF’s eerdere 561-pixelgrens is niet hetzelfde model. Labtimings via onze begrensde broker zijn geen WebPageTest/Lighthouse- of veld-CWV-metingen.

## 6. R6-inhoud en schema’s

Op de 59 gedeelde pagina’s heeft de hoofdinhoudselectie 22 keer een expliciet main-element en 37 keer een fallback. De Nederlandse zinslengte-indicatie is acht keer meetbaar, drie teksten vragen juridische expertbeoordeling en 48 blijven onbekend. Elf pagina’s krijgen een contextueel dunne-hoofdinhoudsignaal. De fallback produceert bewust geen zeker oordeel.

Over de volledige crawl blijft `main_content_thin` 277 keer onbekend en de leesbaarheidscontrole 340 keer onbekend. Dat is een reëel resterend dekkingsgat. Templateherkenning en sterkere contentselectie zijn zinvoller dan kunstmatig een leesbaarheidsscore invullen. Een onbekende uitkomst is geen probleem op de klantwebsite, maar een grens van ons onderzoek.

In de 59 gedeelde HTML-pagina’s is geen ondersteunde gestructureerde markup aangetroffen. R6 kan syntaxis, vastgezet vocabulaire en twee gekozen profielen afzonderlijk beoordelen; deze site bewijst diepgang of SF-pariteit daarvan niet. Een positief/negatief regressiecorpus met JSON-LD, Microdata en RDFa op meerdere sites blijft nodig. De huidige implementatie is geen volledige JSON-LD/RDF/SHACL- of rich-result-validator.

## 7. Metingen worden nog onvoldoende gekoppeld aan controles

De hoofdanalyse kent 355 onbekende `link_to_5xx`-controles, 344 onbekende `link_to_redirect`-controles en 350 onbekende `link_to_nonindexable`-controles. Dit is niet hetzelfde als 355 mislukte pagina-aanvragen: de hoofdcrawl heeft nul aanvraagfouten.

Code-inspectie laat zien dat de pagina-linkcontroles volledigheid afleiden uit doelen in de pagina-index. Externe doelen en apart gemeten bestanden zijn daarin niet geïntegreerd. Bovendien moeten controles voor **interne** indexeerbaarheid alleen daarvoor toepasselijke doelen beoordelen. Een externe verwijzing hoort die controle niet automatisch onbekend te maken.

Gewenst: één contextbewuste doelresolver die pagina-, bestands- en externe metingen raadpleegt voor bereikbaarheid, met afzonderlijke toepasselijkheid voor indexeerbaarheid. Behoud bewijsdatum, scenario en onbekende doelen. Dit verbetert inhoudelijke betrouwbaarheid zonder extra siteverkeer.

Het dossier toont daarom terecht geen technisch cijfer: technische meetdekking 77,0%, onder de huidige grens van 80%. Content heeft score 84 bij 98,9% dekking. Gebruikerservaring blijft onvoldoende onderzocht met 42,9%; privacy heeft 0% omdat geen afzonderlijke toestemmingsscenario’s zijn uitgevoerd. Deze cijfers horen bij de bestaande report-v1-catalogus; nieuwe controles zitten niet automatisch in die formule.

## 8. Actionability en extra waarde

Sterk: bron- en elementlocaties, gemeten bestemming, datum/context, discipline, vermoedelijke oorzaak als hypothese, acceptatiecriteria, expliciete eigenaar en historische verificatie. Het platform levert een gecombineerd dossier en workflow boven op de losse technische signalen. De nieuwe scan vindt naast de referentiecohort veel aanvullende sitemap-pagina’s en hun bestandsrelaties. Het technologieprofiel benoemt onder meer WordPress, Elementor, Contact Form 7, jQuery en Google Fonts; dat is geen bewijs van AVG-conformiteit.

Zwak: 182 groepen leiden tot 182 conceptopdrachten. Daarvan zijn 115 zware-afbeeldinggroepen. De oorzaak blijft vaak een generieke templatehypothese. De calculatorproblemen vallen uiteen over script- en documentstructuurregels. Er is nog geen compacte, gevalideerde herstelagenda voor een klant.

Advies: maak een bovenliggende verbeteropdracht per gedeelde component/paginatemplate, behoud de onderliggende controles en onderscheid **herstellen**, **beoordelen** en **opnieuw meten**. Laat de gebruiker zakelijke prioriteit en eigenaar bevestigen. Toon niet elke lage-ernstadviesregel als zelfstandige hoogst noodzakelijke klus.

Ook gevonden: het dossiermanifest noemt `action-templates-v1`, terwijl alle 182 opgeslagen voorstellen aantoonbaar `action-templates-v2` vermelden. Dit is een versieherkomstdefect; laat het manifest dezelfde versieconstante gebruiken. De nieuwe bewijsvelden zijn wel aanwezig.

## 9. Vervolgprioriteiten

Zie [aanbevelingen.md](aanbevelingen.md) voor effort/effect-scores en concrete acceptatiecriteria. Eerste volgorde:

1. Correcte status-/scopeclassificatie en hergebruik van doelmetingen.
2. Gerichte herpogingen voor bestands- en externe fouten/onderbrekingen.
3. Compacte opdrachten over verwante regels en bestanden heen.
4. Gerichtere browserselectie, reserve aanvullen en afhankelijkheidsdekking.
5. Betere hoofdinhoudselectie en een breder benchmarkcorpus.

De status van alle 28 oorspronkelijke opportunities staat in [gap-matrix.md](gap-matrix.md). “Geïmplementeerd” en “met SF gelijkwaardig bewezen” zijn daar nadrukkelijk verschillende uitspraken.

## 10. Bewijs en reproduceerbaarheid

- [comparison-summary.json](comparison-summary.json): aantallen, cohorten, bronchecksums, dekking en dossier.
- [url-comparison.csv](url-comparison.csv): 59 gedeelde pagina’s.
- [external-comparison.csv](external-comparison.csv): 70 externe referentiedoelen, eerste SF-status naast onze eindstatus.
- [large-images.csv](large-images.csv): 11 afbeeldingen en beide bytegroottes.
- [sf-errors-comparison.csv](sf-errors-comparison.csv): eerdere SF-foutdoelen en nieuwe uitkomsten.
- [measurement-snapshot.json.gz](measurement-snapshot.json.gz): bevroren geselecteerde productiegegevens, zonder HTML-bodies of cookie-/authenticatiewaarden.
- [report-audit.json](report-audit.json): gecontroleerde dossierweergave en aangetroffen versieverschil.
- [benchmark.py](benchmark.py): offline reproduceerbare vergelijking; gebruiksaanwijzing in [README.md](README.md).

De volledige SF-adres-/issue-/headermeetwaarden ontbreken deels in de oorspronkelijke export. Daarom geen productbreed pariteitspercentage. Verschillen tussen meetmomenten, user-agents, redirectlagen, beleidskeuzes en definities worden niet automatisch als crawlerdefect geteld. De oorspronkelijke rapportage van 4 november blijft als historische nulmeting bewaard.
