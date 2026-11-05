**Inhoudelijke gap-analyse: onze crawler versus de aangeleverde Screaming Frog-scan**

Onderzoek: vandersteenhoven.nl, 4 november 2026. Dit rapport beoordeelt het aangeleverde bestand, opgeslagen productiemetingen en de huidige implementatie. Er zijn geen nieuwe websitecrawls uitgevoerd en geen productiegegevens gewijzigd voor deze analyse.

**1. Hoofdconclusie**

De basisextractie van HTML is aanzienlijk vollediger dan het huidige bevindingenrapport suggereert. Op de 59 succesvolle HTML-pagina's uit de Screaming Frog-scan kunnen we dezelfde aantallen ontbrekende omschrijvingen, ontbrekende H1-koppen, meervoudige titels en afbeeldingsverwijzingen reproduceren. Alle 828 genormaliseerde HTTP(S)-bron/doelparen van gewone SF-hyperlinks zitten ook in onze opgeslagen links.

De grootste inhoudelijke gaten zijn: resources zelfstandig ophalen en beoordelen; externe bestemmingen controleren; beschikbare gegevens vertalen naar regels; context van elementen bewaren; en betrouwbare aanbevelingen maken. Onze sitemapintegratie heeft het eerdere ontdekkingsprobleem voor deze site sterk verminderd, maar maakt de overige meetgebieden niet automatisch volledig.

Een percentage als '80% gelijkwaardig aan Screaming Frog' is niet verantwoord. Deze gratis Text Only-scan test niet alle functies, een aantal CSV's bevat geen meetwaarden en voor verschillende regels ontbreken URL-lijsten. We hebben een sterke basisvergelijking op één website, geen gevalideerde productpariteit.

**2. Bronnen en afbakening**

- Aangeleverd: `/home/raymond/data/sf-exports.zip`, 141 CSV's en 34 screenshots. Slechts 34 CSV's bevatten datarijen. Veel bestanden zijn alleen een kolomkop.
- Screaming Frog 25.0, Unlicensed, Text Only. Het dashboard vermeldt starttijd 11:55:26, geen API's en een afgeronde crawl. De exports zijn later op dezelfde dag aangemaakt. Browser-, toegankelijkheids-, AI- en API-tabbladen met nulwaarden bewijzen geen uitgevoerde controles.
- Onze oorspronkelijke crawl: `94885360-cccb-4b96-9461-3b84c0f03875`; 368 URL-pogingen, waaronder 5 downloadfouten. Alle 327 sitemap-URL's zijn geprobeerd.
- Aanvullende gerichte herdownload: `60a7e737-a420-4ad1-a8c5-fe48d7e91fdc`; alle vijf geven HTTP 200. Voor één van de 59 gemeenschappelijke pagina's is die nieuwere meting gebruikt. De oorspronkelijke crawl is niet overschreven en blijft onvolledig.
- De eigen brontijdstempels zijn UTC. De SF-screenshots geven lokale kloktijden zonder expliciete tijdzone. Het zijn verschillende meetmomenten en user-agents; live-responseverschillen zijn daarom geen automatisch parserdefect.
- Code-inspectie: `scans/extraction.py`, `scans/network.py`, `scans/seeding.py`, `assessments/services.py`, `assessments/advanced.py`, browser- en rapportagemodules.

Het meegeleverde `comparison-summary.json` legt identifiers, aantallen en SHA-256 van het ZIP-bestand vast. `export-inventory.csv` specificeert alle CSV-bestanden en datarijen. `url-comparison.csv` bevat onze waarden per gemeenschappelijke pagina.

Belangrijke exportbeperking: er is geen `Internal: All`-export met alle paginametingen en geen machineleesbaar totaaloverzicht van alle issues. De HTTP-headerexports bevatten alleen `Address`, geen headerwaarden. Aantallen uit screenshots zijn bruikbaar, maar niet voldoende om alle individuele meldingen en drempels te reconstrueren. Een `Title`-kolom in een linkexport betreft bovendien de linkcontext, niet vanzelf de documenttitel.

**3. Eerst dezelfde eenheden vergelijken**

| Meetgroep | Screaming Frog | Onze meting | Interpretatie |
|---|---:|---:|---|
| Bekende adressen | 252 in adres-/headerlijst | 368 URL-pogingen in oorspronkelijke crawl | Verschillende scope en URL-soorten; geen directe kwaliteitsranglijst |
| Interne adressen | 179 | Primair pagina's en bestanden waar een hyperlink naartoe wijst | SF controleert daarnaast systematisch resources |
| Externe adressen | 73 | Geen algemene externe-statusmeting | Echt meetgat |
| Succesvolle HTML-pagina's voor vergelijking | 59 | Alle 59 aanwezig; 58 aanvankelijk succesvol, de laatste via herdownload | Gedeelde benchmarkset |
| Sitemap-URL's in onze volledige crawl | Niet als seed benut in deze aangeleverde run | 327 geprobeerd | Voordeel van onze huidige ingestelde onderzoeksroute |
| Interne HTML volgens SF-contenttypeoverzicht | 67 | Niet vergelijkbaar met totaal 368 | Bevat ook redirects/foutantwoorden; is niet hetzelfde als 67 succesvolle inhoudspagina's |

Het SF-dashboard toont 250 gecrawlde URL's, terwijl de volledige adreslijst en de statusbalk 252 bekende/verwerkte items tonen. Er zijn ook twee door robots geblokkeerde externe items. We gebruiken 252 als omvang van de aangeleverde adreslijst en noemen 250 expliciet een dashboardteller; we doen geen exacte uitspraak over interne telregels.

Het bestandsoverzicht van SF onderscheidt 34 afbeeldingen, 51 CSS-resources en 27 JavaScript-responses. De linkexport bevat 31 unieke interne scriptbestemmingen: vier daarvan geven een HTML-404 en vallen daardoor anders in het contenttypeoverzicht. Ook dit illustreert waarom 'aantal URL's' zonder type en status misleidend kan zijn.

**4. Wat aantoonbaar overeenkomt**

| Controle op gemeenschappelijke pagina's | SF | Wij, inclusief herdownload | Betekenis |
|---|---:|---:|---|
| Meta description ontbreekt | 59 | 59 | Basiscontrole en extractie aanwezig |
| H1 ontbreekt | 22 | 22 | Aantallen gelijk; volledige SF-issue-URL-lijst ontbreekt |
| Meerdere documenttitels | 1 | 1 | Dezelfde calculator is bij ons herkenbaar |
| Eerste titel langer dan 60 tekens | 32 | 32 afleidbaar uit opgeslagen titels | Meetgegevens aanwezig; onze bevindingenregel ontbreekt |
| Afbeeldingsverwijzingen | 965 | 965 | Geen algemeen tekort aan HTML-afbeeldingsontdekking op deze pagina's |
| Unieke bronpagina/afbeelding-paren | 456 | 456 | Exacte setvergelijking gelijk |
| Unieke afbeeldingsbestemmingen | 34 | 34 | Exacte setvergelijking gelijk |
| Ontbrekend alt-attribuut | 0 | 0 | Niet verwarren met een leeg attribuut |
| Afbeeldingsverwijzingen met lege alt | 965 | 965 aanwezig in gegevens | Interpretatie ontbreekt bij ons grotendeels |
| Eén self-canonical | 59 | 59 | Canonical-extractie correct op deze benchmarkset |

Bij gewone hyperlinks vinden we alle 828 genormaliseerde HTTP(S)-bron/doelparen uit SF ook bij ons. We normaliseren hier uitsluitend lege URL-paden naar `/` en verwijderen fragmenten. Dit is geen bewijs dat alle linkankers, DOM-posities en duplicaatfrequenties identiek zijn. SF telt 1.987 hyperlinkrelaties, wij bewaren 2.036 ruwe anchorverwijzingen; verschillende normalisatie, fragmenten en selectieregels spelen mee. De ene extra genormaliseerde bestemming bij ons is een ongeldige ECLI-achtige URL, geen aangetoonde extra bereikbare webpagina.

**5. Concrete gemiste bevindingen en hun betekenis**

**A. Vier kapotte scripts op de calculator — directe functionele waarde**

Op `/transitie-vergoeding-berekenen/` rapporteert SF vier 404's:

- `/_sdk/telemetry_sdk.js`
- `/_sdk/editing_sdk.js`
- `/_sdk/data_sdk.js`
- `/_sdk/resizing_sdk.js`

Alle vier URL's staan al in onze opgeslagen `resource_urls`, maar we hebben hun antwoorden niet gemeten. De gewone crawl bezoekt primair hyperlinks; browseronderzoek zou resources in zijn steekproef kunnen controleren, maar is voor dit project niet uitgevoerd.

Dit is het duidelijkste verschil tussen een verwijzing ontdekken en het doel werkelijk onderzoeken. Een bruikbare opdracht is: controleer of deze SDK's nodig zijn; verwijder de verwijzingen of herstel de bestanden; test de calculatorfunctie opnieuw. Uit een 404 alleen volgt nog niet dat de berekening zelf defect is.

De vijf interne SF-404-bestemmingen zijn deze vier scripts plus `/send.cfm?n=mail`. Onze oorspronkelijke crawl heeft acht 404's, maar dat hogere aantal betekent geen betere dekking: het betreft deels andere URL's; de vier scripts ontbreken in onze aanvragen.

**B. Externe links — nuttig, maar 403 is geen bewezen kapotte link**

SF rapporteert 23 externe 4xx-bestemmingen vanaf zeven bronpagina's: 22 geven 403 bij Rechtspraak en één geeft 404 bij Apple Podcasts. Onze crawler bewaart links maar meet die bestemmingen niet algemeen.

De goede uitbreiding is een beperkte externe-linkcontrole, met caching en eigen resource-/netwerktoestemming. De 22 antwoorden van Rechtspraak moeten als 'toegang voor de scanner geweigerd; bereikbaarheid niet vastgesteld' verschijnen. Ze mogen niet automatisch als 22 kapotte bezoekerslinks worden verkocht. Alleen het Apple-voorbeeld is hier een gemeten externe 404, nog steeds op het SF-meetmoment.

**C. Afbeeldingen — gegevens grotendeels goed, beoordeling onvolledig**

SF vindt 11 unieke afbeeldingen boven 100 kB, met 22 verwijzingen vanaf 20 pagina's. Wij hebben geen zelfstandig bestandsonderzoek met bytegewicht. De grens van 100 kB is een gekozen signaleringsdrempel, geen universele foutgrens.

Alle 965 afbeeldingsverwijzingen hebben een leeg `alt`-attribuut; er ontbreken geen attributen. Onze regel controleert momenteel vooral of het attribuut bestaat. Dat is onvoldoende voor informatieve afbeeldingen, maar `alt=""` is juist passend voor decoratieve afbeeldingen. Context van een afbeelding en de toegankelijke naam van een klikbare afbeelding moeten in de beoordeling worden betrokken.

SF levert ook opgegeven afmetingen, bijvoorbeeld `1024x1024`. Wij slaan die attributen nog niet op. De kolommen met echte en weergegeven afmetingen zijn in de SF-export leeg; volledige browsergeometrie is dus een toekomstige uitbreiding en geen in deze scan bewezen SF-overwicht.

**D. Titels en koppen — goedkope uitbreiding met bestaande gegevens**

De 32 lange titels kunnen we nu al reproduceren. Daar is geen nieuwe crawl voor nodig. De 38 titels boven 561 pixels vragen een gekozen font-/breedtemodel; dat is een benadering van weergave, geen harde Google-regel.

SF meldt 37 H1's als Non-Sequential; onze algemene niveausprongcontrole meldt één pagina in de gemeenschappelijke set. De definities zijn verschillend: onze regel zoekt alleen opeenvolgende niveaus die meer dan één stap omhoog springen. Zij controleert niet zelfstandig of de eerste kop een H1 is of een H1 pas na H2 komt. Dit is een analyseverschil, geen bewijs van exact 36 gemiste defecten.

SF rapporteert ook dubbele/meerdere H2's op alle 59 pagina's. Meerdere H2's zijn op zichzelf normaal. Herhaalde koppen in menu/footer mogen niet zonder context leiden tot 59 herstelopdrachten.

**E. Ongeldige documentstructuur — meerdere symptomen, één herstelopdracht**

De opgeslagen HTML van de calculator bevat twee starttags voor elk van `html`, `head`, `body` en `title`. SF toont meervoudige head/body en een title buiten head. Wij melden de dubbele titel, maar missen de onderliggende documentstructuurdiagnose.

Waarschijnlijk is een volledig tweede HTML-document in de bestaande pagina opgenomen; dat is een te bevestigen oorzaak. De herstelopdracht moet daarom het integreren van die calculatorinhoud in de bestaande documentstructuur omvatten. Niet vier losstaande tickets zonder samenhang.

**F. Beveiliging, sociale previews en leesbaarheid**

Op alle 59 gemeenschappelijke HTML-pagina's ontbreken HSTS, CSP, X-Content-Type-Options, X-Frame-Options en Referrer-Policy in onze opgeslagen veilige headerselectie. Onze regel bundelt de eerste drie; de andere twee worden niet afzonderlijk beoordeeld. SF gebruikt daarvoor aparte regels en andere noemers, soms inclusief bestanden. Beveiligingsheaders vragen applicatiekennis; ontbrekende CSP is geen bewijs van een exploit.

SF toont ontbrekende Open Graph- en X Card-tags op 59 pagina's. Wij bewaren deze tags nog niet. Dit is uit opgeslagen HTML te herstellen en beïnvloedt vooral de presentatie bij delen van een URL.

SF noemt 23 pagina's met weinig inhoud en zes met moeilijke leesbaarheid. Wij bewaren tekst en woordtelling, maar geen vergelijkbare beoordeling. Zonder dezelfde hoofdinhoudselectie, taal en drempels zijn die aantallen niet eerlijk te reproduceren. Nederlandse juridische inhoud vraagt extra terughoudendheid.

SF signaleert daarnaast negen externe LinkedIn-links met `target="_blank"` zonder `rel`. Wij slaan target nog niet op. Dit is eenvoudig toe te voegen, maar moderne browserbescherming en context moeten worden meegewogen; het is geen hoofdprioriteit boven werkelijke download- of resourcefouten.

**6. Wat wij nu toevoegen tegenover deze aangeleverde uitvoer**

Onze actuele sitemaproute onderzoekt een groter deel van deze site: alle 327 sitemap-URL's, naast verdere hyperlinkontdekking. De aangeleverde SF-run is zonder sitemap-/browserdekking geëindigd op de gedeelde set van 59 succesvolle HTML-pagina's. Dit is een voordeel van onze huidige onderzoeksconfiguratie, geen bewijs dat SF die URL's niet kan onderzoeken.

We bewaren ruwe bronmetingen, meetmomenten, afgeleide controles en historische dossiers afzonderlijk. Herdownloadoverschrijft de oude fout niet. Ook bestaan scores met expliciete onbekenden, bewijsverwijzingen, actie-eigenaars, acceptatiecriteria en verificatie met nieuwere metingen. Die workflow voegt iets toe tegenover een verzameling CSV's.

Die voordelen mogen geen ontbrekende metingen verhullen. Voor deze website is geen browseronderzoek uitgevoerd, en technische platforms voor privacy/technologie betekenen niet dat deze onderwerpen in dit onderzoek aantoonbaar gedekt zijn. Ook zijn scores nog geen vergelijking met Google-verkeer of bedrijfsresultaat.

**7. Scores voor bouwinspanning en totale scanwaarde**

Beide assen lopen van 1 tot 5. Inspanning is een engineeringinschatting voor een werkende, geteste uitbreiding met passende opslag, analyse en ontsluiting, uitgaande van de huidige code. Indicatief: 1 klein (tot circa één werkdag), 2 enkele dagen, 3 ongeveer een werkweek, 4 meerdere weken, 5 meer dan circa vier weken. Geen offerte of kalenderplanning; afhankelijkheden en validatie kunnen dit veranderen.

Scanwaarde: 1 beperkt, 2 aanvullend, 3 nuttig, 4 belangrijk voor een complete diagnose, 5 fundamenteel voor betrouwbare dekking of uitvoerbare conclusies. Het gaat om productwaarde over meerdere sites, niet uitsluitend aantallen meldingen op deze site. Scores zijn beargumenteerde productkeuzes, geen statistisch gemeten effect.

Afhankelijke werkpakketten zijn niet optelbaar: bijvoorbeeld G03 en G05 veronderstellen G02. Een lage deelinspanning betekent niet dat de noodzakelijke meetinfrastructuur gratis is. De volledige matrix met onderbouwing en acceptatiecriteria staat in `gap-matrix.csv`.

| ID | Onderdeel | Inspanning | Scanwaarde | Huidige staat |
|---|---|---:|---:|---|
| G01 | Automatische herdownload en aanpasbare time-outs | 3/5 | 5/5 | Gedeeltelijk; herstel nu handmatig |
| G02 | Zelfstandig bestandsregister en PageAsset-relaties | 4/5 | 5/5 | Ontbreekt als algemene crawlmeting |
| G03 | Kapotte scripts/styles/afbeeldingen | 2/5 | 5/5 | Ontbreekt in deze crawl |
| G04 | Externe linkstatus met blokkadeclassificatie | 3/5 | 4/5 | Ontbreekt |
| G05 | Afbeeldingsgewicht en hergebruik | 2/5 | 4/5 | Ontbreekt |
| G06 | Lege alt-tekst beoordelen naast ontbrekend attribuut | 2/5 | 4/5 | Alle data aanwezig, analyse onvolledig |
| G07 | Opgegeven afbeeldingsafmetingen | 1/5 | 2/5 | Ontbreekt in extractie |
| G08 | Werkelijke en weergegeven afbeeldingsafmetingen | 4/5 | 3/5 | Niet gevalideerd in deze benchmark |
| G09 | Linkpositie, DOM-pad, target en relatietype | 3/5 | 4/5 | Gedeeltelijk |
| G10 | Lege en onduidelijke linknamen | 2/5 | 4/5 | Data deels aanwezig, regel ontbreekt |
| G11 | Headingvolgorde en H2-controles | 2/5 | 4/5 | Gedeeltelijk; definitieverschil |
| G12 | Titellengte in tekens | 1/5 | 3/5 | Data aanwezig, regel ontbreekt |
| G13 | Titelbreedte in pixels | 3/5 | 2/5 | Ontbreekt |
| G14 | Lengte en plaatsing overige metadata/koppen | 2/5 | 3/5 | Gedeeltelijk |
| G15 | HTML-documentstructuur en title buiten head | 2/5 | 4/5 | Ontbreekt behalve meerdere titels |
| G16 | Open Graph en X Cards | 2/5 | 3/5 | Ontbreekt |
| G17 | Dunne inhoud en leesbaarheid | 3/5 | 3/5 | Alleen woordtelling/tekst aanwezig |
| G18 | Beveiligingsheaders per header en contenttype | 2/5 | 4/5 | Gedeeltelijk |
| G19 | URL-hygiëne | 1/5 | 2/5 | Ontbreekt als analyse |
| G20 | Canonicals: identiek dubbel versus tegenstrijdig | 2/5 | 4/5 | Gedeeltelijk; interpretatiegat |
| G21 | Sitemapdekking en herkomst verder ontsluiten | 2/5 | 4/5 | Kern al aanwezig |
| G22 | Browseronderzoek in standaard audit opnemen | 4/5 | 5/5 | Platform aanwezig; niet uitgevoerd voor dit project |
| G23 | Consistente benchmarkexports en regressiecorpus | 2/5 | 4/5 | Gedeeltelijk |
| G24 | Schema-validatie van gestructureerde gegevens | 4/5 | 3/5 | Niet volledig; buiten bewezen benchmark |
| G25 | Van signalen naar concrete herstelopdrachten | 3/5 | 5/5 | Workflow aanwezig; inhoudelijke vertaling onvolledig |
| G26 | Scoredekking en onzekerheid per meetgebied | 2/5 | 5/5 | Basis aanwezig, breder toepassen |
| G27 | Breder veilig HTTP-headerbewijs | 2/5 | 3/5 | Beperkt en bewust selectief |
| G28 | Externe target=_blank-links beoordelen | 1/5 | 2/5 | Attribuut ontbreekt, regel ontbreekt |

**8. Aanbevolen bouwvolgorde**

1. **Betrouwbaarheid en volledigheid:** G01 en G26. Configureer time-outs, begrensde herhaalpogingen en meetdekking. Tijdelijke fouten mogen geen blijvende blinde vlek worden; een ontbrekende meting mag geen voldoende opleveren.
2. **Nieuwe metingen met direct aantoonbare waarde:** G02, daarna G03/G05, plus G04. Hiermee worden de vier SDK-404's, afbeeldingsgewichten en externe blokkades/404's zichtbaar. De assetregistratie moet ook uitbreidbaar zijn voor srcset, lazy loading, CSS-url's en fonts; deze basis kost meer dan alleen HTTP-statussen toevoegen.
3. **Benut bestaande data:** G06, G10–G12, G15, G18 en G20. Dit levert inhoudelijke controles en betere interpretatie zonder alles opnieuw op te halen. G16 en G19 zijn eenvoudige aanvullingen, met lagere prioriteit.
4. **Maak uitvoering concreet:** G09/G25 en de ontsluiting van G21. Toon waar de fout zit, welk gedeeld onderdeel waarschijnlijk betrokken is en hoe herstel wordt gecontroleerd. Het kunnen registreren van een actie is niet hetzelfde als een goede actie voorstellen.
5. **Breid de gevalideerde scope uit:** G22–G24 en vervolgens diepere beeld-/inhoudsanalyse. Hiervoor zijn andere websites, browsermetingen en referentiegevallen nodig. De huidige SF-run kan deze gelijkwaardigheid niet bevestigen.

De eerste bruikbare mijlpaal is niet 'meer meldingen', maar een herhaalbaar onderzoek dat de gemeten resourcefouten vindt, tijdelijke downloads herstelt, echte onzekerheid toont en per belangrijk probleem een onderbouwde herstelopdracht geeft.

**9. Acceptatie van een volgende benchmark**

- Bewaar dezelfde URL-selectie, crawlmodus, user-agent, robotsinstelling en meetperiode waar praktisch mogelijk. Leg verschillen vast in plaats van aantallen blind af te trekken.
- Lever SF Internal All, het volledige Issues-overzicht en de relevante issue-URL-lijsten naast All Outlinks en Image Inlinks aan. Exports met alleen kopregels tellen niet als geslaagde controle.
- Vergelijk per gemeenschappelijke URL en regel: gemeten waarde, uitkomst, bewijs, eventuele uitsluiting. Rapporteer ontbrekende gegevens afzonderlijk.
- Gebruik positieve én negatieve vaste testgevallen voor onder meer dubbele/conflicterende canonicals, decoratieve/informatieve afbeeldingen, 403 versus 404, HTML-inbedding en headingvolgorde.
- Meet nieuwe assets/externe URL's onder dezelfde publieke netwerkbeveiliging. Geen impliciete volledige crawl van andere domeinen.
- Bewijs op ten minste meerdere verschillende websites dat de verbeteringen werken voordat een algemene SF-pariteitsclaim of totaalpercentage wordt gegeven.

Dit onderzoek is een analyse; er zijn geen productwijzigingen of releases uitgevoerd.
