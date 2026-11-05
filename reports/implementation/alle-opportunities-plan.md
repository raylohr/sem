**Implementatieplan voor alle 28 opportunities**

Gebaseerd op de inhoudelijke Screaming Frog-benchmark van 4 november 2026. De oorspronkelijke G01–G28-identifiers en effort/value-scores blijven behouden. Dit is een plan, geen opdracht om nu al de beschreven wijzigingen of releases uit te voeren.

**Doel en definitie van klaar**

Een controle is pas een productfunctie wanneer de benodigde gegevens betrouwbaar worden verzameld, de uitkomst uitlegbaar wordt afgeleid, bewijs beschikbaar is, de meetdekking zichtbaar is en een gebruiker begrijpt welke vervolgstap past. Alleen een extra kolom of een regel die afgaat op een voorbeeld is onvoldoende.

Het eindresultaat is een geïntegreerd onderzoek met HTML-pagina's, bestanden, externe linkbestemmingen en een expliciete browserfase. Scores blijven te herleiden tot versie, doelgroep, meetmoment en bewijs. We bouwen voort op de bestaande crawler, browserisolatie, catalogus, analyse en dossiers.

De 28 werkpakketten sluiten de geïdentificeerde gaten. Ze bewijzen niet automatisch volledige pariteit met iedere functie van de betaalde Screaming Frog- of Sitebulb-versie; aanvullende referentiescans blijven daarvoor nodig.

**Bouwvolgorde: zeven afzonderlijk bruikbare releases**

| Release | Doel | Werkpakketten | Indicatieve bouwinspanning* |
|---|---|---|---|
| R0 | Vaststellen wat gemeten en bewezen is | G23, G26 | 2–4 werkdagen |
| R1 | Downloads herstellen en dekking tonen | G01, G21 | 4–7 werkdagen |
| R2 | HTML-extractie en direct bruikbare controles | G09, G07, G12, G14, G15, G18, G19, G20, G28 | 7–12 werkdagen |
| R3 | Inhoudelijke interpretatie van bestaande HTML | G06, G10, G11, G16 | 5–8 werkdagen |
| R4 | Bestanden en externe bestemmingen onderzoeken | G02, G27, G03, G04, G05 | 12–20 werkdagen |
| R5 | Browseronderzoek standaard onderdeel maken | G22, G08, G13 | 12–20 werkdagen |
| R6 | Diepere analyse en geïntegreerde hersteladviezen | G17, G24, G25 | 12–20 werkdagen |

*Werkhypothese voor één ervaren ontwikkelaar op de bestaande basis, inclusief gerichte tests, basisontsluiting en integratie. Totaal ongeveer 54–91 ontwikkelwerkdagen, met circa 20% onzekerheidsruimte ongeveer 65–110 werkdagen. Dit is geen offerte, leverdatum of onderbouwde snelheidsmeting. R0/R1 en de assetprototype moeten de raming aanscherpen. Beschikbare capaciteit, meetprofielen en corpus bepalen de kalenderduur. Deelinschattingen uit de gapmatrix worden niet nog eens bij deze release-inschattingen opgeteld.

R2 en R3 leveren verbeteringen uit opgeslagen HTML op voordat het grotere assetonderdeel klaar is. R0 schrijft alvast het contract voor actievoorstellen voor; G25 wordt bij iedere release gebruikt en in R6 integraal afgerond. Ontsluiting en tests worden niet tot de laatste release uitgesteld.

**R0 — benchmark, meetcontract en scoredekking**

G23: maak een versieerbaar export- en vergelijkingsformaat. Iedere URL krijgt de ruwe meetwaarden, toegepaste regels, uitkomsten, bewijsverwijzingen, tijdstippen en instellingen. Leg URL-normalisatie en eenheden vast. Maak een kleine lokale referentiewebsite met bewuste fouten én correcte gevallen. Leg het bestaande vandersteenhoven-resultaat vast als baseline.

G26: definieer per controle de doelgroep en noemer: HTML-pagina, uniek bestand, linkvoorkomen, unieke bestemming of browserpagina. Leg de statussen vast: geslaagd, signaal, onbekend, niet van toepassing. Meetfout en blokkade blijven herkenbare redenen onder onbekend of een afzonderlijke technische uitkomst. Een niet-uitgevoerde assetcontrole levert geen punten op.

Leg tevens het bewijscontract voor G25 vast: wat is gemeten, waar, welke interpretatie volgt, wat is slechts een hypothese, welke actie past en welk nieuw bewijs bevestigt herstel. Geen automatische oorzaak- of omzetclaim.

Acceptatie: alle 28 opportunities zijn aan een releasemijlpaal en bewijssoort gekoppeld; exports van een herhaalde analyse met dezelfde invoer/configuratie zijn inhoudelijk reproduceerbaar; lege of niet-uitgevoerde controles worden niet als geslaagd gepresenteerd. Oude dossiers behouden hun oorspronkelijke scoremodel.

**R1 — retries, time-outs en volledigheid**

G01: maak connect- en read-time-out instelbaar per scanprofiel. Voeg begrensde herhaalpogingen toe voor tijdelijke netwerkfouten, met oplopende wachttijd en een lager tempo waar nodig. Gebruik 10 seconden connect en 30 seconden read als beginprofiel voor deze site, niet als onbewezen universele optimumwaarde. Begin met maximaal drie pogingen totaal, waaronder de eerste, en versieer die instelling.

Bewaar iedere poging, foutcategorie, duur en uiteindelijke uitkomst. Gebruik geen onbegrensde urllib-retries naast een tweede applicatieretrylus. HTTP 404 wordt niet behandeld als downloadtime-out. Voor 429/503 blijven passende wachttijd en Retry-After relevant; 403 wordt niet blind herhaald alsof wachten toegang garandeert. Wachttijden worden via checkpoints en taakplanning afgehandeld, niet met lang slapende workers. Netwerkbudget en maximale looptijd blijven eindig.

Retries binnen een lopende crawl maken deel uit van de eindmeting, met behoud van de pogingen. Een retry ná afronding wordt een nieuwe vervolgmeting. De oorspronkelijke batch en de feitelijke meetdata blijven behouden. Maak expliciet zichtbaar dat een eerdere fout door een nieuwe waarneming is opgevolgd. Bij samenstellen van een nieuwe meetset wordt per URL de gekozen bron vastgezet, nooit stilzwijgend in een oud dossier vervangen.

G21: maak filters voor sitemap-only, alleen links, overlap, niet bezocht, downloadfout en succesvol opgehaald. Scheid gevonden, ingepland, geprobeerd en met succes opgehaald. Toon afzonderlijk of er een linkroute vanaf het startadres is vastgesteld. Een alleen in de sitemap gevonden URL is een kandidaat voor onderzoek, geen bewezen verweesde pagina.

Acceptatie: vaste testgevallen met twee tijdelijke fouten en daarna succes leveren één eindresultaat en drie pogingen; annuleren en hervatten dupliceren geen aanvragen/resultaten; een blijvende fout blijft zichtbaar; een 404 blijft een 404. De oorspronkelijke vijf historische fouten en hun latere successen zijn als twee meetmomenten navolgbaar. Alle 327 sitemap-URL's zijn in de benchmark verklaarbaar als bezocht of met expliciete reden niet bezocht.

**R2 — rijkere extractie, zonder onnodig opnieuw te crawlen**

G09 is de basis: introduceer een nieuwe extractorversie die naast regel/kolom ook een elementpad, elementsoort, linkdoel, target, rel, context zoals header/main/footer en relevante ARIA-verwijzingen bewaart. Houd de oorspronkelijke attribuutwaarde apart van de opgeloste en genormaliseerde URL. Bij een ambigu of niet-uniek elementpad blijft die beperking zichtbaar. Bewaar onbewerkte structurele aanwijzingen voordat een browser ongeldige HTML herstelt.

Daarop volgen:

- G07: opgegeven width/height en responsive broninformatie van afbeeldingen.
- G12: titellengte in tekens, met instelbare adviesdrempels.
- G14: lengte, meervoud, plaatsing en overlap van descriptions en koppen.
- G15: geneste of meervoudige html/head/body en metadata buiten head.
- G18: afzonderlijke beveiligingsheadercontroles met toepasselijkheid en uitleg.
- G19: URL-vorm en parameters met onderscheid tussen bestanden en pagina's.
- G20: identieke dubbele canonicals versus werkelijk verschillende bestemmingen; bron HTML/header blijft bekend.
- G28: target/rel-signalen met passende beperkte ernst en browsercontext.

Herextracteer bestaande opgeslagen HTML waar dat kan. Historisch niet bewaarde HTTP-headers blijven onbekend; ontbrekende waarden worden niet ingevuld met wat de website vandaag teruggeeft. Herextractie verandert geen meetdatum en bewijst geen herstel.

Acceptatie: de calculator levert bewijs voor het geneste document, gekoppeld aan de dubbele titel. De 32 titels boven de vastgelegde 60-tekengrens zijn reproduceerbaar. Canonicalfixtures onderscheiden identiek dubbel, conflict, geldige eigen canonical, redirect en ongeldige bestemming. Afbeeldingsafmetingen worden niet verkocht als bewezen layoutshift. De negen target=_blank-voorbeelden zijn herleidbaar, zonder automatisch kritisch oordeel.

**R3 — interpretatie van afbeeldingen, links en koppen**

G06: onderscheid ontbrekend alt-attribuut, leeg attribuut, informatieve afbeelding en decoratieve afbeelding. De laatste twee zijn niet altijd automatisch betrouwbaar vast te stellen; geef dan een reviewadvies met bewijs. Voor een klikbare afbeelding moet ook de naam van de link worden onderzocht.

G10: onderscheid zichtbare ankertekst en toegankelijke naam. Statische HTML kan een naam afleiden uit tekst, afbeelding, aria-label of aria-labelledby, maar complexe gevallen vereisen browserbevestiging. Laat regels niet doen alsof eenvoudige tekstconcatenatie de volledige accessibility tree reproduceert.

G11: splits ontbrekende H1, eerste heading, H1 na H2, opwaartse niveausprong en herhaalde H2. Gebruik de positie in gedeelde navigatie/footer om ruis te beperken. De 37 SF Non-Sequential-meldingen en onze ene heading_jump moeten verklaard worden vanuit definities, niet blind gelijkgetrokken.

G16: extraheer Open Graph en X Cards. Controleer ontbrekende/verkeerde/conflicterende waarden. De aanwezigheid van een previewafbeelding kan al worden vastgelegd; de bereikbaarheid ervan wordt na R4 automatisch via de resourcecontrole onderzocht.

Acceptatie: een decoratieve afbeelding met alt="" krijgt geen automatische inhoudelijke fout; een naamloze afbeeldingslink krijgt wel een concreet signaal; de 965 verwijzingen worden niet automatisch 965 actiepunten. Gewone pagina's met meerdere H2's slagen. De ontbrekende social-tags op de 59 benchmarkpagina's zijn reproduceerbaar. Waar bewijs geen zekere uitspraak toelaat, staat 'beoordelen' of 'onbekend'.

**R4 — bestanden en externe doelen daadwerkelijk meten**

G02: voeg een eigen resourcewachtrij toe, met unieke targets en relaties naar alle pagina-elementen die ernaar verwijzen. Ontdek scripts, stylesheets, afbeeldingen, srcset-bronnen en relevante lazy-loadattributen. Breid gecontroleerd uit naar CSS-imports/url's en fonts. Leg per brontype vast wat wel en niet wordt ondersteund; onbeperkt willekeurige JavaScript doorzoeken is geen betrouwbare vervanging voor de browserfase.

Gebruik afzonderlijke budgetten voor pagina's, unieke bestanden, externe linkdoelen, bytes en recursie. Een resource telt niet als extra inhoudspagina in de SEO-score. Deduplicatie geldt binnen dezelfde meetcontext: URL, relevante aanvraaginstellingen en scenario. Queryparameters worden niet zonder bewijs verwijderd. MIME-type volgens URL-verwijzing en werkelijk antwoord worden afzonderlijk bewaard: de vier SDK-bestemmingen zijn scripts die HTML-404 teruggeven.

G27: bewaar veilige responseheaders voor onder meer MIME, encoding, cache, redirect en byte-interpretatie. Bewaar opgegeven bestandsgrootte, gedownloade bytes en na decompressie gelezen bytes afzonderlijk. Een afgekapt bestand heeft geen volledig gemeten grootte. Kopieer geen willekeurige cookies of autorisatiegegevens naar algemene rapporten.

G03/G05: bouw resourcefouten, afbeeldingsgewichten en hergebruik op deze metingen. Een defect gedeeld bestand moet alle betrokken bronpagina's tonen, maar kan één herstelopdracht opleveren. Een statische som van bestandsgroottes is niet hetzelfde als werkelijk gemeten browserverkeer.

G04: voer een beperkte statuscontrole op unieke externe HTTP(S)-bestemmingen uit, met expliciete project-/scaninstelling. Volg alleen de begrensde redirectketen en crawl de gelinkte externe site niet verder. Het hostbeleid onderscheidt navigatie, resourceverkeer en externe-linkmeting. Expliciete blokkades voor het betreffende doel gaan voor. Publieke DNS-validatie en pinning gelden ook na iedere redirect.

Acceptatie: vier SDK-404's worden gevonden en naar de calculator herleid. De 11 SF-afbeeldingsgewichten zijn aan dezelfde unieke targets te vergelijken wanneer het meetmoment geen verandering laat zien. Een 403 wordt niet automatisch als kapotte bezoekerslink aangemerkt. Resource-, pagina- en externe-budgetten worden afzonderlijk gerapporteerd. Onderbreken/hervatten levert geen dubbele resourcefetches door verlies van checkpointstatus. Een resourcehost krijgt geen navigatierecht.

**R5 — browsermeting als expliciete onderzoeksfase**

G22: gebruik de bestaande geïsoleerde Playwright-uitvoering, maar neem haar op in het standaard onderzoeksproces. Bouw na de HTML-crawl een diverse selectie op basis van paginatype, URL-pad, dynamische aanwijzingen en eerdere onzekerheid. Laat omvang en budget zien; een steekproef is geen volledige JavaScript-crawl. Combineer initiële HTML en gerenderde DOM in de presentatie, niet door de eerste meting te overschrijven.

Ontdek dynamische linkkandidaten via gecontroleerd scrollen en begrensde interacties. Laat DOM-verwijzingen terugstromen naar een expliciete vervolgwachtrij, met dezelfde scope, limieten en meetherkomst. Automatiseer geen willekeurige formulieracties. Baseline- en toestemmingsscenario's blijven gescheiden. Voor de benchmark is een representatieve browsermeting nodig; de aangeleverde Text Only-run geeft hiervoor geen vergelijkingsbewijs.

G08: leg intrinsieke en weergegeven afbeeldingsafmetingen vast met currentSrc, viewport, device-pixel-ratio, lazy-loaddekking en toepasselijke CSS-context. Een ontbrekende geometrie wordt niet als nul of als berekende besparing weergegeven.

G13: voeg een aparte, reproduceerbare titelbreedteschatting toe met vaste font-/modelversie. Gebruik niet de titel zoals die in de pagina-body wordt weergegeven. Een pixelbreedte is een modelmatige SERP-indicatie, geen garantie van Google-weergave.

Acceptatie: een fixture waarvan links pas na scrollen ontstaan wordt binnen het ingestelde budget onderzocht; HTML/DOM-verschillen blijven zichtbaar; ontbrekende browserdekking drukt geen fictief positief resultaat uit. Afbeeldingsmetingen zijn herleidbaar naar viewport en bron. Herhaling met dezelfde invoer/modelversie geeft dezelfde titelbreedteschatting. Geen automatische vergelijking van browsermetingen met de gratis Text Only-baseline.

**R6 — diepere inhoud, schema's en uitvoerbare voorstellen**

G17: maak een versieerbare selectie van hoofdinhoud naast de bestaande volledige tekst. Leg taalherkenning, methode voor woordtelling en leesbaarheidsprofiel vast. Beoordeel dunne inhoud per paginatype; een contactpagina hoeft geen lang artikel te zijn. Nederlands en juridische inhoud zijn expliciete testgevallen. Bij onbekende taal of onvoldoende tekst volgt geen fictieve leesbaarheidsscore.

G24: splits detectie van gestructureerde gegevens, JSON-syntaxis, schemavalidatie en validatie tegen een gekozen rich-result-profiel. Ondersteun expliciet benoemde JSON-LD-, Microdata- en RDFa-vormen; documenteer overige onbekenden. Pin de gebruikte vocabulaires/profielen, toon hun versie en maak actualiseren beheersbaar. Een geldig schema garandeert geen zoekresultaatuitbreiding.

G25: voltooi over alle meetgebieden de actievoorstellen. Iedere opdracht bevat het probleem, gemeten voorbeeld, locatie, getroffen bereik, vermoedelijke gemeenschappelijke oorzaak, herstelrichting, benodigde discipline en acceptatietest. Gebruik eerst controleerbare regeltemplates. AI kan suggesties en samenvattingen toevoegen, met opgeslagen model/prompt/inputreferenties; AI mag meetuitkomsten of bewijs niet verzinnen of een taak zelfstandig opgelost verklaren. Een eigenaar wordt door de gebruiker toegewezen of komt uit een expliciete projectregel.

Accepteer bewuste uitzonderingen zonder historische feiten of scores te herschrijven. Groepeer vier SDK-fouten als calculatoropdracht wanneer het bewijs dat ondersteunt; maak van 965 lege alt-verwijzingen geen 965 automatisch bewezen defecten. Zakelijke prioriteit blijft los van technische ernst. GA4/GSC/Ahrefs-verrijking is mogelijk via het bestaande concept, maar automatische omzetimpact is geen stilzwijgend onderdeel van deze SF-gaplijst.

Acceptatie: een onbekende of juridisch complexe tekst krijgt een verklaarbare beoordeling; syntaxisfout, schemafout en rich-result-voorwaarde zijn afzonderlijk herkenbaar. Het prospectrapport toont de belangrijkste gevalideerde opdrachten met drilldown. Een actie wordt pas als opgelost bevestigd met nieuw passend meetbewijs en controle van acceptatiecriteria. Alleen ontbreken in een nieuwe resultatenlijst is onvoldoende.

**Gegevensmodel en integratie**

Bouw voort op `ImportBatch`, `SourceRecord`, `ProjectURL`, `PageMeasurement`, `LinkObservation`, `AnalysisRun`, browserbezoeken, bewijs en dossiers. Voorgestelde uitbreidingen worden in R0/R1 definitief ontworpen:

| Onderdeel | Functie | Kernvelden / relaties |
|---|---|---|
| FetchAttempt | Iedere netwerkpoging en retries reconstrueren | Batch/target, doelsoort, poging, tijden, time-outs, status/fout, redirectbewijs, bytes, configuratieversie |
| ResourceMeasurement | Eén gemeten bestand binnen een meetcontext | ProjectURL, FetchAttempt, gemeten/verwacht MIME, grootte, dimensies, decodeer-/afkapstatus |
| PageResource | Hergebruik en bronlocaties | Paginabron, resource, elementsoort/locator, oorspronkelijke URL, discovery mode |
| LinkCheck | Bestemmingsmeting los van de link zelf | LinkObservation/doel, FetchAttempt, status, blokkadereden, tijd en scope |
| MeasurementSet / manifest | Expliciete selectie van oorspronkelijke en vervolgmetingen | Project, basiscrawl, vastgezette bronrecordreferenties, selectiegrond, versie, volledigheid |
| BrowserElementObservation | Gemeten elementcontext | Browserbezoek, viewport, locator, geometrie/currentSrc, relevante toegankelijke naam en bewijsverwijzing |

Een apart targetmodel wordt alleen toegevoegd als `ProjectURL` onvoldoende blijkt. Voorkom dubbele URL-identiteiten. Bewaar getypeerde relaties en veelgebruikte filters in kolommen; variabele bewijsdetails en berekeningen in begrensde JSONB. Grote bodies blijven bij de bestaande document-/artifactopslag en worden niet in iedere bevinding gekopieerd.

Voor een samengestelde MeasurementSet wordt de selectie bevroren. Analyses en dossiers verwijzen naar die selectie en daadwerkelijke meetmomenten, niet naar een ongedefinieerde 'laatste waarde'. Bestaande batchanalyses blijven geldig. Als invoerbronnen uit verschillende momenten komen, staat dat zichtbaar bij de resultaten. Nieuwe URL's uit een herdownload tellen niet automatisch mee als volledig gevolgde sitestructuur.

Indexen volgen de query's: batch/target/poging, project/URL-hash, status/fout, source/resource en meetcontext/scenario. Meet een nieuwe index eerst met representatieve queries; voeg geen generieke GIN-index op iedere JSON-kolom toe. Checkpointresultaten worden door de bestaande gecontroleerde publicatiestap transactioneel gepubliceerd.

**Toetsing en releasevoorwaarden**

Elke release bevat functionele tests voor relevante juiste én foutieve situaties, passende veiligheids-/rechtentests bij nieuwe netwerk- of schrijfhandelingen, versieerbare regels en bewijs, UI/exportondersteuning, documentatie en een rollbackpad. Laat oude analyseresultaten en dossiers intact. Een nieuwe extractor- of regelversie vereist een expliciete compatibiliteitsbeoordeling bij actie-verificatie.

Vergelijkingen krijgen een verschilregister: brondata gelijk/anders, regel gelijk/anders, ontbrekende meting, niet-toepasselijk, tijdverschil of onverklaard. Markeer niet iedere SF-afwijking als eigen fout, en niet iedere SF-nulwaarde als geslaagde meting.

Voor de uiteindelijke acceptatie:

- Alle vaste referentiegevallen hebben een verwachte uitkomst en bewijs; geen onverklaarde verschillen in deze cases.
- De aangeleverde SF-waarden op de gemeenschappelijke pagina's zijn reproduceerbaar waar de benodigde data beschikbaar zijn; overige gevallen blijven als niet toetsbaar gemarkeerd.
- Vier SDK-404's, de externe blokkades/404, lege alt-voorbeelden, nested document en tijdelijke downloadfouten zijn inhoudelijk verklaard.
- Er is validatie op meerdere sites met verschillende opbouw, naast het vaste corpus. GMW en verhuizer.eu zijn beschikbare kandidaten; plan daarnaast een kleine JavaScript-site en meertalige fixture.
- Een schaalproef meet ten minste pagina's, unieke assets, verwijzingen, geheugen, query's, hervatten en looptijd. Gebruik de bestaande 10.000 pagina/100.000 linkklasse als begin en voeg een gedeelde-assetset toe; stel een onderbouwd budget vast na de nulmeting in plaats van een willekeurige SLA.
- Schijnzekerheid blijft uitgesloten: 403 is geen bewezen 404, leeg alt geen automatisch defect, heranalyse geen nieuwe meting, syntactisch schema geen gegarandeerd rich result, steekproef geen volledige site.

Productie-uitrol gebeurt per release, na testvalidatie, database-/mediaback-up waar nodig, migrations/checks, versiegebonden images en gereedheidscontrole. Nieuwe controles worden eerst zichtbaar met meetdekking; invloed op cijfers volgt alleen in een expliciet nieuw scoremodel. Een sprong door een modelwijziging wordt niet als websiteverbetering gepresenteerd. Een rollback mag nieuw bewijs niet verwijderen.

**Eerste uitvoeringsopdracht**

Begin met R0 en R1. Leg de baseline, schema's en scorecontracten vast; bouw vervolgens configureerbare time-outs, begrensde herhaalpogingen, pogingenhistorie en sitemap-/retrydekking. De eerste oplevering moet automatisch kunnen doen wat bij de vijf downloadfouten nu handmatig is gedaan, zonder het oude bewijs te verliezen. Daarna volgt R2, terwijl het datacontract voor het grotere assetonderdeel al duidelijk is.

**Volledige traceerbaarheid per opportunity**

| Release | ID | Werkpakket | Inspanning | Scanwaarde | Afhankelijk van |
|---|---|---|---:|---:|---|
| R0 | G23 | Consistente benchmarkexports en regressiecorpus | 2/5 | 4/5 | — |
| R0 | G26 | Scoredekking en onzekerheid per meetgebied | 2/5 | 5/5 | G23 |
| R1 | G01 | Automatische herdownload en aanpasbare time-outs | 3/5 | 5/5 | G23, G26 |
| R1 | G21 | Sitemapdekking en herkomst verder ontsluiten | 2/5 | 4/5 | G01, G26 |
| R2 | G09 | Linkpositie, DOM-pad, target en relatietype | 3/5 | 4/5 | G23 |
| R2 | G07 | Opgegeven afbeeldingsafmetingen | 1/5 | 2/5 | G23 |
| R2 | G12 | Titellengte in tekens | 1/5 | 3/5 | G23 |
| R2 | G14 | Lengte en plaatsing overige metadata/koppen | 2/5 | 3/5 | G09, G12 |
| R2 | G15 | HTML-documentstructuur en title buiten head | 2/5 | 4/5 | G09 |
| R2 | G18 | Beveiligingsheaders per header en contenttype | 2/5 | 4/5 | G23 |
| R2 | G19 | URL-hygiëne | 1/5 | 2/5 | G23 |
| R2 | G20 | Canonicals: identiek dubbel versus tegenstrijdig | 2/5 | 4/5 | G23 |
| R2 | G28 | Externe target=_blank-links beoordelen | 1/5 | 2/5 | G09 |
| R3 | G06 | Lege alt-tekst beoordelen naast ontbrekend attribuut | 2/5 | 4/5 | G09, G07 |
| R3 | G10 | Lege en onduidelijke linknamen | 2/5 | 4/5 | G09 |
| R3 | G11 | Headingvolgorde en H2-controles | 2/5 | 4/5 | G23 |
| R3 | G16 | Open Graph en X Cards | 2/5 | 3/5 | G09 |
| R4 | G02 | Zelfstandig bestandsregister en PageAsset-relaties | 4/5 | 5/5 | G01, G09, G26 |
| R4 | G27 | Breder veilig HTTP-headerbewijs | 2/5 | 3/5 | G02 |
| R4 | G03 | Kapotte scripts/styles/afbeeldingen | 2/5 | 5/5 | G02, G27 |
| R4 | G04 | Externe linkstatus met blokkadeclassificatie | 3/5 | 4/5 | G01, G09, G26 |
| R4 | G05 | Afbeeldingsgewicht en hergebruik | 2/5 | 4/5 | G02, G27 |
| R5 | G22 | Browseronderzoek in standaard audit opnemen | 4/5 | 5/5 | G01, G09, G26 |
| R5 | G08 | Werkelijke en weergegeven afbeeldingsafmetingen | 4/5 | 3/5 | G02, G07, G22 |
| R5 | G13 | Titelbreedte in pixels | 3/5 | 2/5 | G12, G22 |
| R6 | G17 | Dunne inhoud en leesbaarheid | 3/5 | 3/5 | G09, G11 |
| R6 | G24 | Schema-validatie van gestructureerde gegevens | 4/5 | 3/5 | G09, G23 |
| R6 | G25 | Van signalen naar concrete herstelopdrachten | 3/5 | 5/5 | G03, G04, G06, G10, G15, G22, G26 |
