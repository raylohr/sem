# R4 — bestanden en externe bestemmingen daadwerkelijk meten

R4 implementeert G02, G27, G03, G05 en G04. Nieuwe scans gebruiken `html-v7`, analyses `assessment-v6` en regelversie 6. De aparte bestandsfase gebruikt het profiel `resources-v1`. Productie en historische meetdata worden niet automatisch gewijzigd.

## Gebruiken

In Websitescans → crawl-instellingen staat **Bestanden en externe bestemmingen**. De webinterface zet bestandsmeting standaard aan en externe-linkmeting standaard uit. De API accepteert expliciete booleans `check_resources` en `check_external_links`; weglaten schakelt die controles niet stilzwijgend in. Sitemapimports voeren geen bestandsfase uit. Gerichte R1-herdownloads krijgen evenmin automatisch een brede bestandsfase.

Aparte instellingen:

| Instelling | Standaard |
|---|---:|
| Unieke bestanden maximaal | 10.000 |
| Unieke externe doelen maximaal | 1.000 |
| Bestandsbudget, gelezen bodybytes én uitgepakte bytes | elk maximaal 250 MiB |
| Extern budget, gelezen bodybytes én uitgepakte bytes | elk maximaal 10 MiB |
| Leeslimiet per bestand | 5 MiB |
| CSS-verwijzingsdiepte | 3 |
| Afbeeldingsgrootteadvies | 102.400 bytes |

Voor externe statuscontroles wordt per antwoord hoogstens 16 KiB gelezen. De oorspronkelijke totale scantijd begrenst ook de bestandsfase. Het globale inventarisplafond is 50.000 targets en 100.000 verwijzingen. Meer ontdekkingen leveren expliciete onvolledigheid op. De bestandsfase is sequentieel en deelt de bestaande hostbegrenzing; de concurrency-instelling betreft de paginacrawl.

Pagina-, bestands- en externe aantallen en bytebudgetten blijven gescheiden. Een bestands- of extern target wordt niet als extra inhoudspagina of als extra pagina in de scoreformule toegevoegd. Herkenbare downloadlinks gaan naar de bestandsfase. Herkenbare bestanden uit sitemaps krijgen in URL-dekking de status ‘afzonderlijke bestandsmeting’. Extensies zijn een aanwijzing: een bestand achter een URL zonder herkenbare extensie kan aanvankelijk nog als pagina worden opgehaald; niet-HTML-inhoud krijgt geen geslaagde HTML-controles.

## Ontdekking en hostbeleid

De extractor ontdekt onder meer scripts, stylesheets, afbeeldingen, srcset/imagesrcset, expliciete lazy-loadattributen, posters, iframes, media en herkenbare downloadlinks. Social previewafbeeldingen worden als afbeeldingskandidaten meegenomen. Inline CSS en opgehaalde CSS ondersteunen url(), quoted @import, comments en begrensde CSS-stringescapes. Stylesheet- en fontverwijzingen worden gevolgd tot de ingestelde diepte. Willekeurige JavaScript, CSS-computed styles, runtime-lazy-loading en SVG-interne verwijzingen worden niet uitgevoerd of volledig geïnterpreteerd; die horen bij verder browseronderzoek.

HTML-kandidaten zijn begrensd op 2.000 per pagina; inline styles op twintig blokken van 64 KiB. CSS-discovery verwerkt hoogstens 1 MiB tekst en 2.000 verwijzingen per stylesheet. Onvolledigheid blijft in de dekking staan.

Bestandshosts zijn de toegestane paginahosts plus expliciet toegestane extra bestandshosts, minus bestandblokkades. De toestemming verleent geen navigatierecht. Externe bestemmingen vereisen scan-opt-in en hebben een eigen hostblokkade. Redirects worden alleen binnen het betreffende meetbeleid gevolgd. Publieke DNS-validatie en pinning vinden opnieuw plaats bij iedere aanvraag, ook na redirects. Toegang en huidige hostblokkades worden tijdens het onderzoek opnieuw gecontroleerd; nieuwe grants verruimen de bevroren scan niet stilzwijgend.

Er worden geen cookies of autorisatieheaders meegestuurd. De bestaande robots-instelling geldt ook hier. Requests, redirects en robotsverzoeken gebruiken de host-rate-limiter. Middelen zijn baseline GET-metingen, geen toestemmingsscenario’s of browsersimulatie.

## Metingen en beoordeling

Per unieke URL en doelsoort worden opgeslagen:

- HTTP-status, eindadres, redirectketen, meetmoment en fout/blokkadereden;
- beperkt bewaarde veilige responseheaders voor MIME, lengte, encoding en caching;
- opgegeven Content-Length, daadwerkelijk gelezen gecodeerde bodybytes en gelezen uitgepakte bytes;
- volledigheidsvlaggen, een hash van de bewaarde body en een beperkt tekstfragment bij HTTP-fouten;
- iedere bewaarde verwijzende bron, elementpad, bronpositie en eventuele tussenliggende stylesheet.

HTTP-bodybytes sluiten request/responseheaders en framing uit. Een gedeeltelijk antwoord of leeslimiet bewijst geen volledige bestandsgrootte. Content-Length is een opgave van de server, geen vervanging voor gemeten bytes. Een gzip-antwoord kan verschillende gecodeerde en uitgepakte groottes hebben. Een statische som is geen daadwerkelijk browserverkeer of laadtijd. Redirect- en robotsverkeer telt in het fasebudget mee; de afzonderlijke targetgrootte betreft het eindantwoord.

404/410 en andere passende HTTP-fouten leveren bevindingen op. 401/403/429, andere tijdelijke/toegangsproblemen, een niet-gevolgde redirect en downloadfouten blijven herkenbaar als onbekend of geblokkeerd. Een 403 wordt niet automatisch een kapotte bezoekerslink. Bij een succesvol antwoord wordt de opgegeven MIME vergeleken met de functie van de verwijzing. Grote afbeeldingen geven laag advies boven de ingestelde grens, geen berekende besparing of bewezen laadtijdprobleem.

Een gedeeld defect bestand levert één bevinding per target op, met alle opgeslagen bronverwijzingen in de bestandsdetailpagina. Het onderzoeksdossier groepeert op dezelfde bestemming. Bestanden die uitsluitend uit bijvoorbeeld een sitemap komen kunnen aan het crawldossier hangen zonder te beweren dat een inhoudspagina ernaar linkt. Bij een scan zonder inhoudspagina’s blijft de bestandsinventaris het bewijs; de bestaande pagina-analysestructuur maakt dan geen losse paginabevinding aan.

## Hervatten en historische gegevens

Een SQLite-checkpoint bewaart de wachtrij, targetuitkomsten, verwijzingen en CSS-relaties. Een voltooide fetch wordt na hervatten niet herhaald. Als een proces midden in een request verdwijnt, kan niet worden bewezen of de server al antwoordde. Dat target wordt ‘onderbroken, eindantwoord onbekend’; we beloven geen onmogelijke exactly-once-netwerksemantiek en sturen het niet blind opnieuw. De resterende wachtrij kan wel doorgaan. Herstel van zo’n onbekend target vraagt een nieuwe scan; de R1-herdownloadfunctie blijft specifiek voor paginadownloads.

Nieuwe targets en verwijzingen worden in bulk gepubliceerd. De oorspronkelijke paginabronnen en eerder gemaakte dossiers blijven behouden. Heranalyse van opgeslagen HTML start geen nieuwe bestandsrequests. Ontbrekende historische bestandsmetingen blijven onbekend. Het bestaande scoremodel report-v1 houdt dezelfde controles en gewichten; de afzonderlijke targetcontroles krijgen hun eigen meetdekking.

## Ontsluiting en verificatie

De crawlpagina verwijst naar Bestanden en externe links: filters, afzonderlijke budgetten, targetdetails, alle bronverwijzingen en een volledige JSONL-export. Bevindingen linken door naar de targetmeting. Analyses tonen targetcontroles met een eigen noemer naast paginacontroles. Rapporten bevriezen ook geslaagde, onbekende en niet-toepasselijke targetcontroles, apart van hun pagina’s. Actiecontrole vereist voor een bestandsprobleem een nieuw gemeten target-pass met compatibele regelversie; een verdwenen bevinding of gewijzigde grootteadviesgrens is onvoldoende. Rapporten en exports bevatten de bevindingen en bewijssnapshots; de volledige ruwe bestandsinventaris heeft een eigen export.

De gerichte benchmark herextraheerde 59 opgeslagen HTML-pagina’s en ontdekte 220 unieke resource-URL’s. Nieuwe begrensde GET-metingen bevestigden de vier SDK-404’s van de calculator en alle elf SF-afbeeldingen boven 102.400 bytes. De SF-export noemt voor die elf alleen de URL’s, geen exacte bytegroottes; exacte historische bytegelijkheid is niet aantoonbaar. Deze nieuwe assetmetingen vervangen de eerdere paginametingen niet.

De resultaten staan in `reports/implementation/r4-verificatie.json`. De regressiesuite verifieert onder meer policy/DNS, gzip/bytegrenzen, afzonderlijke budgetten, CSS-hergebruik, onderbreken/hervatten, projecttoegang, bewijs/export en de volledige scantaak. Dit is geen claim van volledige SF- of Sitebulb-pariteit.

Afronding: 482 tests geslaagd. De echte achtergrondworker verwerkte twee synthetische bronpagina’s met vijf daadwerkelijk opgehaalde bestanden (vier SDK-404’s en één grote afbeelding). Gedeelde verwijzingen, afzonderlijke pagina-/targetaantallen, besloten bewijsweergave, dossiergroepering en exports slaagden. Tijdelijke testgegevens zijn verwijderd. R4 is actief op de gezonde testomgeving; productie is niet gewijzigd.
