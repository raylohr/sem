# Technische eisen crawler en website-assessment

Datum: 3 november 2026  
Status: doelontwerp met acceptatiecriteria; geen verklaring van geïmplementeerde functionaliteit.

## Doel en afbakening

1. Prospectscan zonder klantcredentials: gedetailleerd onderzoek van de publieke website, compacte categoriescores en concrete verbeterkansen met controleerbaar bewijs.
2. Klantassessment: dezelfde metingen verrijken met aangeleverde GA4-, GSC-, Ahrefs- en andere klantdata, zakelijke prioritering en vergelijking door de tijd.

Deze eisen concretiseren de eerdere uitbreidingsrichting. De huidige implementatie en operationele grenzen staan in [03-scans.md](03-scans.md). Nieuwe modules wijzigen bestaande instellingen niet stilzwijgend. Implementatiestatus wordt per module afzonderlijk verantwoord.

## Benchmarkbeleid

| Referentie | Relevante benchmark | Gevolg voor het ontwerp |
|---|---|---|
| Screaming Frog | HTTP, metadata, robots, canonicals, links, duplicaten, hreflang en structured data | Herleidbare SEO-observaties en controles |
| Sitebulb | Resources, server-HTML versus render, toegankelijkheid en performance | Breder onderzoek dan HTML-inventarisatie |
| Lumar | SEO, toegankelijkheid en sitesnelheid samenbrengen | Gezamenlijke rapportage met bewijs per categorie |
| Oncrawl | Crawlgegevens combineren met serverlogs en imports | Optionele klantverrijking; werkelijk botbezoek apart van eigen crawl |
| WebPageTest | Waterfalls, verbindingstijden, filmstrips en herhaalbezoeken | Diagnose van browserlaadgedrag |
| Cookiebot | Cookies, dynamische tracking en browseropslag | Inventarisatie met expliciete toestemmingsscenario's |
| Wappalyzer | Technologieherkenning en lookup-API | Versieerbare herkenning en leveranciersverrijking |
| axe-core | Automatische toegankelijkheidscontroles | Bestaande meetengine met elementgebonden bewijs |

Vergelijk op dezelfde testset met gelijke URL-scope, browser, viewport, meetlocatie, netwerkprofiel, wachttijden en toestemming. Leg toolversies en instellingen vast. Beoordeel dekking, juiste/onjuiste detecties, reproduceerbaarheid, bewijs en kosten per scan. Meer velden bewijst geen betere kwaliteit. Er wordt geen algemene superioriteit of pariteit geclaimd zonder meetresultaten.

Referenties verplichten geen abonnement of runtimekoppeling. Hergebruik, licenties, API-rechten en kosten worden bij implementatie beoordeeld. Scheid eigen waarnemingen, bestaande meetengines en externe kennisbronnen.

## C01 — Ontdekking en dekking

- Ontdek pagina's via server-HTML, gerenderde DOM, expliciete URL-lijsten en sitemaps. Ontdek sitemaplocaties via robots.txt; behoud XML-indexen en gzip.
- Inventariseer afbeeldingen/srcset, CSS, JavaScript, fonts, frames/embeds en overige browserresources met verwijzende pagina en ontdekkingsmechanisme.
- Onderscheid pagina's, assets, interne en externe bestemmingen. Externe linkcontrole en third-party resources krijgen afzonderlijke scope en budgetten.
- Bewaar ontdekt, ingepland, bezocht, geblokkeerd, mislukt en overgeslagen apart, met reden: onder meer robots, hostscope, tijd, grootte, diepte en URL-limiet.
- Meld afkapping per veld/pagina, inclusief linklimieten. Ontbrekend, leeg, afwezig en niet onderzocht blijven onderscheiden.
- Vergelijk sitemap- en crawlvondsten; voeg later GSC-/logvondsten toe. Niet gevonden in een beperkte crawl is geen bewijs van een weespagina.

## C02 — Browserrendering

- Gebruik Playwright/Chromium; bewaar server-HTML en gerenderde DOM als afzonderlijk bewijs. Rapporteer verschillen in content, links en SEO-signalen.
- Leg browser-/engineversie, user-agent, viewport, taal, tijdzone, locatie, netwerk-/CPU-profiel, cachebeleid, wachttijden en gereedheidscriteria vast. Niet-beheerste instellingen blijven onbekend.
- Gebruik begrensde, configureerbare rendercriteria; network-idle alleen garandeert geen volledige pagina. Registreer time-outs, consolefouten en mislukte/geblokkeerde resources.
- Ondersteun begrensd scrollen voor lazy loading en expliciete interactiescenario's. Geen willekeurig automatisch klikken of insturen van formulieren.
- Privacyonderzoek gebruikt schone contexten voor geen keuze, weigeren, accepteren en intrekken na accepteren. Bewaar de handelingen en tijdlijn; een onbedienbare banner geeft een onbekend/onvolledig scenario.
- Screenshots onderbouwen bevindingen. Browsermetingen hebben eigen budgetten en begrensd parallellisme.

## C03 — Datapunten

| Groep | Vereiste observaties |
|---|---|
| HTTP | Aangevraagde/finale URL, status per hop, redirecttype/-doel, keten en loopdiagnose, contenttype, HTTP-versie, meettijd, responstijd, omvang en overdrachtsbytes waar meetbaar |
| Metadata | Alle gevonden titels, descriptions, H1/H2, aantallen en tekstlengtes, taal, base-URL, meta refresh; pixelbreedte alleen met vastgelegde meetmethode |
| Indexeerbaarheid | Robots.txt per relevante user-agent, meta robots en X-Robots-Tag gescheiden naar bron/doelgroep, HTML-/HTTP-canonicals, beoordeling met afzonderlijke redenen |
| Links | Bron, ruwe/opgeloste bestemming, ankertekst, rel, type, DOM-locatie en server/renderherkomst; herhaalde links behouden |
| Resources | Verwijzende pagina, URL, type, status, MIME, bytes en timing; voor afbeeldingen alt-aanwezigheid/-waarde, opgegeven/intrinsieke afmetingen waar meetbaar, srcset en lazy-loading |
| Content | Tekst, extractiegebied en uitsluitingen, woordtelling, body-/teksthash met algoritme, afkapstatus en invoer voor bijna-duplicaten |
| Internationaal | html lang, hreflang uit HTML/HTTP/sitemap, doel-URL, x-default en paginatieverwijzingen |
| Structured data | JSON-LD, Microdata en RDFa, typen, betrokken elementen en parser-/validatiemeldingen |
| Headers | Expliciete allowlist van relevante cache-, canonical-, robots- en beveiligingsheaders; niet opgeslagen onderscheiden van ontbrekend |

Herverwerk opgeslagen documenten zonder nieuwe netwerkcrawl waar mogelijk. Behoud de oorspronkelijke meetdatum en voeg extractietijd/-versie toe. Markeer niet-reconstrueerbare historische gegevens, zoals niet-bewaarde headers en browsergedrag.

Ruwe documenten, observaties en catalogusvelden blijven gekoppeld. Gegevens worden bereikbaar via bronrecord, detail, relevante filters en export; alleen bewaren in ruwe JSON is onvoldoende ontsluiting.

## C04 — Analyse en bevindingen

- Versieerbare regels voor ontbrekende/meervoudige/dubbele metadata, headings, HTTP-fouten, redirectketens/-loops, robotsconflicten en canonicaldoelen/-ketens/-conflicten.
- Een linkmodel levert inlinks, outlinks, unieke aantallen, interne ondersteuning en verklaarde diepteberekening. Een Link Score krijgt een beschreven algoritme.
- Exacte en bijna-duplicaten gebruiken versieerbare normalisatie, extractiegebieden en drempels.
- Hreflangvalidatie controleert codes, terugverwijzingen, doelstatus en canonicalconsistentie. Structured-datavalidatie benoemt engine, regels en versie.
- Assetcontroles omvatten kapotte resources, ontbrekende alt-attributen en mixed content. Een leeg alt-attribuut is niet automatisch fout. Beveiligingsheaders worden afzonderlijk beoordeeld.
- Iedere bevinding bevat regel-ID/-versie, technische ernst, getroffen objecten, bewijs, uitleg, actie, zekerheid en afhankelijkheid van onvolledige invoer.
- Feit, interpretatie en zakelijke prioriteit blijven onderscheiden. Technische indexeerbaarheid is geen bewijs van opname in Google.

## C05 — Performance en toegankelijkheid

- Benut bestaande engines waar passend: Lighthouse/WebPageTest voor performance en axe-core voor automatische toegankelijkheid. Eigen equivalenten vereisen validatie.
- Bewaar labmetingen apart van beschikbare CrUX-/andere veldgegevens, met bron, periode, pagina-/originscope en dekking. Geen velddata is geen geslaagde test.
- Performancebewijs omvat request-waterfall, fasetijden, bytes en resources. Filmstrip, herhaalbezoek en mobiele profielen zijn configureerbare verdiepingen. Rapporteer variatie bij herhaalde runs.
- Toegankelijkheidsbewijs verwijst naar regel, element/selector, DOM-fragment en beschikbare screenshot. Automatische controles vormen geen volledige toegankelijkheidsbeoordeling.
- Ondersteun volledige HTML-crawl met expliciete steekproef van dure browsermetingen per template/paginatype. Steekproefresultaten worden niet als metingen van alle pagina's gepresenteerd.

## C06 — Privacy, technologie en Europese afhankelijkheid

- Bewaar per toestemmingsscenario cookies en browseropslag met naam, domein, duur en relevante flags. Sla gevoelige waarden en identifiers niet standaard op.
- Registreer externe verzoeken, bestemming, initiator, volgorde en moment ten opzichte van toestemming. Dienst-/trackingclassificatie krijgt bewijs, herkenningsversie en onzekerheid.
- Vergelijk gedrag met gevonden privacy-/cookieverklaringen; bevindingen blijven onderbouwde signalen voor beoordeling.
- Verzamel publieke DNS-records, waaronder NS/CNAME, IP/ASN en aanwijzingen voor hosting/CDN/WAF. Herken CMS, publiek zichtbare plugins, analytics, advertenties, chat, formulieren en embeds.
- Onderhoud een leveranciersregister met bronnen, controledatum, versie, vestigingsland, moederorganisatie en diensten. Scheid leverancier, eigendom, gemeten netwerklocatie en geclaimde gegevenslocatie.
- CDN-edge/IP-geolocatie bewijst geen originhosting of opslaglocatie. Onzichtbare serverintegraties, plugins en subverwerkers blijven onbekend.
- Privacy en Europese afhankelijkheid krijgen afzonderlijke beoordelingen. Niet-Europees betekent niet automatisch onrechtmatig; Europees bewijst geen AVG-conformiteit. Contracten, doelen, bewaartermijnen en doorgiftegrondslag vragen klantinformatie en beoordeling.

## C07 — Scores en ontsluiting

Zeven categorieën: bereikbaarheid/indexeerbaarheid; structuur/interne links; content/metadata; taal/machineleesbaarheid; gebruikerservaring (performance en toegankelijkheid); privacy/toestemming; Europese afhankelijkheid/digitale autonomie. Performance en toegankelijkheid blijven afzonderlijk inspecteerbaar.

- Drilldown: categorie/score → bevinding → getroffen pagina's/resources/leveranciers → bewijs → aanbevolen actie.
- Toon enkele website-specifieke hoofdbevindingen; detail blijft doorzoekbaar, filterbaar en exporteerbaar met herkomst.
- Scores hebben versieerbare regels/gewichten, toepasselijke noemers, geteste aantallen en dekking. Niet gemeten, onbekend en niet van toepassing blijven afzonderlijk; geen impliciete voldoende.
- Aggregatie per URL/template/ernst/categorie is uitlegbaar. Kritieke problemen blijven zichtbaar naast gemiddelden. Een optionele totaalscore vereist verklaarde weging.
- Het EU-profiel beschrijft maatstaf en geografische definitie (bijvoorbeeld EU of EER) en blijft gescheiden van AVG-beoordeling.

## C08 — Klantverrijking en historie

- GA4, GSC, Ahrefs, serverlogs en andere klantbronnen zijn optioneel; de prospectscan werkt zonder credentials.
- Koppel conservatief op URL, periode en scope. Vermeng metingen, schattingen, pagina-/origin-/sectiecijfers en brondefinities niet.
- Toon technische ernst naast zakelijke prioriteit op basis van verkeer, zoekprestaties, conversies, autoriteit en klantlabels; ontbrekende data blijft zichtbaar.
- Vergelijk nieuwe, gewijzigde, opgeloste en niet-herbevestigde bevindingen. Opgelost vereist vergelijkbare dekking; niet opnieuw gemeten is niet opgelost. Meld verschillen in scanconfiguratie, regels en scoreversies.

## C09 — Uitvoering, beveiliging en bewaring

- Behoud projectrechten, batchherkomst, annulering, hervatten, budgetten en duurzame opruiming. Browserworkers krijgen eigen queue en CPU-/geheugen-/tijd-/opslaglimieten.
- DNS-/SSRF-bescherming geldt ook voor browsernavigatie, redirects, subresources, frames en achtergrondverbindingen. Blokkeer omzeiling via browser-DNS, proxies, WebSockets en serviceworkers op netwerklaag.
- Onderscheid toegestane navigatiehosts van externe resourcehosts; resource-inspectie verruimt de navigatiescope niet. Blokkades tellen mee in de dekkingsrapportage.
- Bewijs blijft privé en projectgebonden. Bewaar geen autorisatieheaders, sessietokens of complete gevoelige requestpayloads. Leg redactie, selectie en retentie vast. Verwijdering omvat afgeleide bevindingen en browserartefacten.
- Hergebruik bewijs waar mogelijk en registreer netwerk-, browser-, opslag- en externe API-kosten per scan.

## Fasering en acceptatie

1. Bestaande gegevens ontsluiten/herverwerken; meetkwaliteit, analysemodule en linkmodel toevoegen.
2. Browserlaag, assets, server/rendervergelijking, toegankelijkheid en begrensde performance.
3. Toestemmingsscenario's, DNS-/technologieverrijking en leveranciersregister.
4. Scores/rapportage voltooien, klantverrijking en wijzigingsvergelijking uitbreiden.

Ontwikkel vanaf stap 1 een verticale keten van meting tot bewijs en rapportage. Valideer een eerste prospectrapport met enkele concrete bevindingen; volledige toolpariteit is daarvoor geen voorwaarde.

Acceptatie per geleverde module:

- Fixtures met bekende goede/foute gevallen controleren relevante redirects/loops, canonicals/hreflang, duplicaten, JS-content/-links, assets, consent en toegankelijkheid.
- Verwachte observaties/bevindingen zijn aanwezig; time-outs, blokkades en afkapping worden gemeld. Bewijs is vanuit het rapport bereikbaar.
- Onbekende/niet-gemeten controles leveren geen gunstige score. Scores zijn reproduceerbaar uit opgeslagen invoer en versies.
- De relevante toolbenchmark legt verschillen en blinde vlekken vast; geen onbewezen pariteitsclaim.
- Browser-/resourceverkeer bereikt geen private netwerken; projectgrenzen, annulering en verwijdering blijven aantoonbaar werken.
- Herextractie behoudt de oorspronkelijke meetdatum; historische vergelijking onderscheidt opgelost van niet opnieuw gemeten.

## Referentiedocumentatie

Officiële documentatie geraadpleegd op 3 november 2026. Productbeschrijvingen zijn referenties, geen zelfstandig bewijs van meetkwaliteit.

- [Screaming Frog](https://www.screamingfrog.co.uk/seo-spider/user-guide/tabs/)
- [Sitebulb auditdata](https://support.sitebulb.com/en/articles/9844123-audit-data)
- [Sitebulb response versus render](https://support.sitebulb.com/en/articles/9857330-response-vs-render-report)
- [Lumar](https://www.lumar.io/)
- [Oncrawl logs](https://help.oncrawl.com/en/articles/1567762-log-monitoring-setup-step-by-step)
- [Oncrawl data ingestion](https://help.oncrawl.com/en/articles/1482979-data-ingestion)
- [WebPageTest](https://github.com/catchpoint/WebPageTest.docs/blob/main/src/getting-started.md)
- [Cookiebot trackingtechnieken](https://support.cookiebot.com/hc/en-us/articles/360004155074-What-kinds-of-cookies-and-tracking-technologies-does-Cookiebot-find)
- [Wappalyzer](https://www.wappalyzer.com/docs/api/v2/lookup/)
- [axe-core](https://github.com/dequelabs/axe-core)
- [Rijksoverheid: cookies](https://www.rijksoverheid.nl/themas/overheid-en-democratie/privacy-en-persoonsgegevens/vraag-en-antwoord/telecommunicatie/mag-een-website-ongevraagd-cookies-plaatsen)
- [EDPB: adequaatheidsbesluiten](https://www.edpb.europa.eu/topics/international-transfers-and-international-cooperation/adequacy-decision_en)


## Aanvulling: flexibele analyses en technologie-inventaris

Analysevragen worden versieerbare definities met selectiecriteria, uitvoermethode
(SQL, regels of AI) en een gevalideerd antwoordschema. Uitvoeringen bewaren gebruikte
bronversies, model-/promptinstellingen waar van toepassing, status, kosten en invoerdekking.
Bewaar resultaten per onderzocht object, ook pass/unknown/not_applicable. Antwoorden
in JSONB mogen gemeten feiten niet overschrijven. AI schrijft via gecontroleerde
applicatiefuncties; websitecontent is onbetrouwbare invoer en geeft geen instructies.
Vrije analyses wijzigen officiële scores alleen via expliciet aangewezen scoreversies.

Technologie-inventarisatie omvat CMS, frameworks/libraries, plugins, CRM/marketing
automation, analytics/gedragsanalyse, tagmanagement, advertenties, consent, formulieren,
chat, e-commerce/betalingen, zoeken/personalisatie, embeds, infrastructuur en monitoring.
Per detectie: product/categorie, aantoonbare versie, functie, pagina/resource, scenario,
bewijs, zekerheid, herkenningsregel/-versie en meetmoment. Onderscheid geladen script,
verzonden request, geplaatste cookie en aanwezigheid van een tagcontainer.
Een zichtbaar CRM-formulier bewijst niet welk centraal CRM de organisatie gebruikt.

Doelmodellen: Technology, TechnologyDetection, DetectionEvidence, Supplier,
TechnologySupplier (rol/geldigheidsperiode) en afzonderlijke CustomerDeclaration.
Leverancierskennis heeft eigen bronnen en actualisatiedatums. Klantbevestigingen
overschrijven de oorspronkelijke detecties niet. De huidige realisatie van stap 1
staat in [09-assessment-opslag-en-analyse.md](09-assessment-opslag-en-analyse.md).
