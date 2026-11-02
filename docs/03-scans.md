# Sitemapimport en basiscrawler

Scans draaien op een aparte Celery-queue (scans) met twee scanprocessen, samen
maximaal twee CPU-cores en 1 GiB geheugen. Per websitecrawl worden standaard
acht HTTP-verzoeken parallel verwerkt; checkpoints en databasewerk blijven op de
hoofdthread van die scan. Imports en GSC-ophaalacties blijven op queue celery.
Dezelfde isolatie geldt voor dev/test en productie. De GPS-app blijft afzonderlijk.

## Doelen en projectrechten

GET /projects/<project-id>/scans/targets/ toont de scandoelen van een project.
Alleen projectbeheerders maken doelen en exacte toegestane hosts aan:

POST /projects/<project-id>/scans/targets/create/

```json
{
  "name": "Eigen website",
  "url": "https://www.gmw.nl/",
  "kind": "own",
  "allowed_hosts": ["www.gmw.nl", "gmw.nl"]
}
```

kind kan own of competitor zijn. De host uit url wordt altijd toegevoegd.
Zonder allowed_hosts is uitsluitend die host toegestaan. Geen wildcards of
impliciete subdomeinen. DNS-validatie gebeurt vóór elk netwerkverzoek.

POST .../targets/<target-id>/deactivate/ schakelt het doel uit; een lopende scan
stopt bij de volgende controle. Bestaande batches blijven behouden.

## Starten en annuleren

Een projectbeheerder of bewerker start via:

POST /projects/<project-id>/scans/targets/<target-id>/start/

```json
{"kind":"crawl","max_urls":50,"max_depth":3,"duration":120}
```

Of:

```json
{"kind":"sitemap","url":"https://www.gmw.nl/sitemap_index.xml","max_urls":2000,"max_depth":3,"duration":120}
```

url is optioneel: de doel-URL voor crawls; /sitemap.xml op de doelhost voor
sitemaps. Deze versie ontdekt geen sitemap-URL's automatisch via robots.txt.
Gebruik een expliciete URL als de website een andere locatie hanteert.

Crawl en sitemap gebruiken één broncode `semscan` met weergavenaam SEMScan.
Het subtype staat in request_metadata.scan (crawl of sitemap) en wordt ook in
het bronnenoverzicht getoond. Bestaande batches zijn geconverteerd met behoud
van hun UUID, gebruiker, tijd, records en oorspronkelijke broncode in
request_metadata.source_original. Elke uitvoering blijft zelfstandig verwijderbaar.
De catalogusadapter bewaart het verschil tussen gemeten crawlgegevens en
sitemapvermeldingen, die geen gemeten HTTP-status krijgen.

Een scan creëert een afzonderlijke batch met uitvoerende gebruiker, start-/importtijd,
doel en een snapshot van instellingen. Alleen toegestane projectleden kunnen die
via het bestaande bronnenoverzicht/recordendpoint bekijken of documenten downloaden.

POST /projects/<project-id>/scans/<batch-id>/cancel/ annuleert een pending of
processing scan; actor en tijd worden vastgelegd. Annulering is coöperatief vóór
het volgende verzoek, tijdens downloadblokken/wachten en vóór publicatie. Een
lopend socketverzoek kan nog tot zijn time-out duren. Er worden daarna geen
resultaten gepubliceerd. Een mislukte of pending scan kan via het bestaande
sources/<batch-id>/retry/ opnieuw worden ingepland. Voor een geannuleerde scan
maak je een nieuwe batch. Bij elke stap worden gebruiker, doel en hosts hercontroleerd.

## Resultaten

De crawler bewaart per pagina de aangevraagde/finale URL, status en redirectketen,
titel(s), descriptions, H1/H2, canonicals, robots-instructies, taal, woordenaantal,
interne/externe uitgaande link-URL's, responstijd, tekst/bodyhash en maximaal
100.000 tekens zichtbare tekst. Technische indexeerbaarheid is gebaseerd op HTTP,
robots-instructies en canonical, niet op een Google-indexeringsmeting.

Het betreft server-HTML, zonder JavaScript-rendering of cookies/toestemmingsinteractie.
Navigatielinks worden gevolgd; assets worden niet als aparte crawlitems ingepland.
Queries blijven betekenisvol behouden; fragmenten worden verwijderd. Robotsblokkades
en HTTP-/netwerkfouten zijn aparte observaties, geen verzonnen statuscodes.

Sitemapimport ondersteunt XML-urlset, XML-sitemapindex en gzip. URL's worden binnen
één batch ontdubbeld. De bron-sitemap en lastmod/priority/changefreq blijven per URL
beschikbaar; pagina's uit de sitemap worden niet automatisch opgehaald. Een vermelding
is daarom geen bewijs van bereikbaarheid of indexering. XML-DTD's en entities zijn verboden.

Ruwe HTTP-documenten (inclusief geraadpleegde robots.txt en redirects) worden per
batch bewaard met URL, status, contenttype en meettijd. Cookies/gevoelige response-
headers worden niet apart opgeslagen. Download als bijlage:

GET /projects/<project-id>/scans/<batch-id>/documents/<index>/download/

Alleen complete batches worden gepubliceerd; dit gebeurt atomair met bronrecords
en documenten. Verwijderen via de bestaande batchverwijderactie verwijdert beide,
zonder andere scans of het scandoel te verwijderen.

## Grenzen en netwerkbeleid

- Crawl: standaard 100.000, maximaal 100.000 URL-observaties; diepte standaard 99, maximaal 100.
- Sitemap: standaard 100.000, maximaal 100.000 URL's; maximaal 10.000 sitemapdocumenten.
- Tijd- en overdrachtsbudget schalen met het aantal URL's; zie Grote scans hieronder.
  Totale tijd maximaal zeven dagen; netwerk-time-outs zijn 5s.
- Requesttempo: standaard maximaal twaalf verzoeken per seconde per host,
  instelbaar van één tot vijftien. Een atomaire Redis-begrenzer deelt de ruimte
  tussen gelijktijdige scans van dezelfde host. Dit is een bovengrens, geen garantie.
- robots_mode is standaard ignore (audit): robots.txt wordt opgehaald en als
  bewijsmateriaal bewaard, maar blokkeert pagina's niet. Met respect worden
  weigeringen, crawl-delay en request-rate wel toegepast. Historische scans zonder
  nieuwe instelling behouden respect_robots=True. 404/410 betekent geen bestand;
  bij respect blokkeren andere fouten conservatief; bij ignore kan de scan doorgaan.
  Onbekend robotsbeleid blijft onbekend in de Googlebot-beoordeling.
- Noindex, canonical, HTTP-status, toegestane hosts en netwerkbeveiliging blijven
  afzonderlijke controles, ook wanneer robotsfilters zijn uitgeschakeld.
- Maximaal 5 MiB per document, inclusief begrensde uitpakking. Het totale budget
  is minimaal 25 MiB en schaalt mee met het URL-maximum; zie hieronder. Dit zijn
  productlimieten, lager dan de maximale documentomvang van het sitemapprotocol.
- Alleen HTTP/HTTPS op standaardpoorten, zonder gebruikersnaam/wachtwoord.
- Elke host moet exact zijn toegestaan. Alle DNS-antwoorden moeten publiek zijn;
  private, loopback, link-local, multicast en gereserveerde adressen worden geweigerd.
- De TCP-verbinding gebruikt het gecontroleerde IP; HTTPS houdt de oorspronkelijke
  SNI/Host en certificaatverificatie. Iedere redirect wordt opnieuw gecontroleerd.
- Geen omgevingsproxy's, cookies of externe authenticatie in scanverzoeken.
- Bij 429/503 wordt het tempo verlaagd en een gedeelde hostpauze ingesteld
  (Retry-After in seconden, begrensd tot 120 seconden; andere waarden vijf seconden).
  De URL krijgt maximaal drie nieuwe pogingen, waarna het foutantwoord wordt
  opgenomen. Trage antwoorden verlagen eveneens het tempo. Tijd/downloadlimieten
  blijven gelden.

Periodieke planning, vergelijking met eerdere scans en browsergestuurde cookie/JavaScript-scans volgen nog. Het gecombineerde URL-dashboard is inmiddels beschikbaar.

## Bronnen

- https://www.sitemaps.org/protocol.html
- https://docs.python.org/3.12/library/urllib.robotparser.html
- https://urllib3.readthedocs.io/en/stable/advanced-usage.html

## Grote scans: 100.000 URL’s

De huidige grenzen en uitvoering:

- Crawl én sitemap ondersteunen maximaal **100.000 URL-resultaten per batch**.
- Linkdiepte standaard 99, instelbaar van 0 tot 100; de crawler ontdekt maximaal
  vijfmaal het aangevraagde aantal URL's. Sitemapindexen: maximaal 10.000 documenten.
- Tijd standaard proportioneel aan het aantal URL's: crawl zes seconden per URL
  plus een minuut, sitemap één seconde per URL plus een minuut, minimum twee minuten
  en maximum zeven dagen. Een expliciete API-duration blijft mogelijk binnen die
  grenzen. Het betreft verstreken tijd sinds de eerste verwerkingsronde, ook bij
  pauzes of wachttijd tussen vervolgtaken.
- Overdrachtsbudget proportioneel: minimaal 25 MiB of maximaal aantal URL's × 1 MiB,
  zowel voor ontvangen als uitgepakte inhoud. Per document blijft 5 MiB gelden.
  De scan stopt ook bij minder dan 1 GiB vrije schijfruimte. Een waarschuwing meldt
  dat een scan onvolledig is bij een tijd-, diepte-, ontdek-, download- of schijflimiet.
- Het ingestelde tempo en eventuele robotsregels, reactietijden en overbelasting
  bepalen de doorlooptijd. Het URL-maximum garandeert geen aantal bereikbare pagina's.
  Verbindingen worden per thread hergebruikt, met DNS-validatie voor ieder verzoek.

### Checkpoints en publicatie

De scan-worker verwerkt rondes van circa twee minuten en plant zichzelf opnieuw
in. De actieve wachtrij, URL-ontdubbeling, resultaten en gzip-gecomprimeerde ruwe
HTTP-documenten staan in een privé SQLite-checkpoint onder media/scan-work. Ze staan
niet allemaal in RAM en worden niet als openbare bestanden geserveerd. Een bestands-
lock voorkomt dat meerdere taken hetzelfde checkpoint gelijktijdig verwerken.
Robotsbeleid, overdrachtscounters en starttijd blijven over rondes behouden.

Een late-acknowledged taak wordt bij verlies van het workerproces opnieuw aangeboden.
Een onderbroken scan kan na 15 minuten zonder heartbeat via Opnieuw starten worden
hervat. Bij brokeruitval wordt de batch failed en blijft het checkpoint bewaard.
Bestaande failed batches zonder checkpoint beginnen bij opnieuw starten vanaf nul.
Annuleren en ingetrokken toegang stoppen verdere requests/publicatie. Een worker-
update krijgt 150 seconden om een verwerkingsronde af te maken.

De batchdetails tonen checkpointvoortgang. Alleen complete batches publiceren
bronrecords, ruwe documenten en URL-observaties atomair. Publicatie leest kleine
chunks uit het checkpoint. Grote metadata wordt expliciet gemarkeerd als ingekort;
het oorspronkelijke HTTP-document blijft beschikbaar. Voltooide checkpoints worden
opgeruimd. Batch- en projectverwijdering ruimen ook achtergebleven checkpoints via
de bestaande duurzame opruimwachtrij op. Mislukte/geannuleerde scans houden het
checkpoint tot hervatten of batchverwijdering; er is nog geen automatische retentie.

### Synthetische schaalcontrole

Voer alleen met de testmigratierol uit:

```sh
docker compose --profile tools run --rm migrate python manage.py shell < ops/checks/scan_scale.py
```

Dit simuleert 100.000 HTML-pagina's en controleert 100.000 bronrecords, documenten,
URL's en observaties, met hervatting tussen verwerkingsrondes. Het doet geen echte
netwerkrequests. Databasewijzigingen worden teruggedraaid en privébestanden opgeruimd.
Dit controleert verwerking en geheugen, niet het crawltempo van een externe website.

Met `-e SCAN_SCALE_KIND=sitemap` vóór `migrate` controleert dezelfde schaalproef
een sitemapindex met twee urlsets van elk 50.000 URL’s.

De schaalproef op deze host verwerkte een synthetische crawl met 100.000 ruwe
HTML-documenten en een sitemapindex met twee keer 50.000 URL's. In beide gevallen
zijn alle 100.000 bronrecords, URL's en observaties gecontroleerd. Piek-RSS was circa
110 MiB voor de crawl en 153 MiB voor de sitemap. De oude orphan-URL-controle is
vervangen door een geïndexeerde scalaire subquery om een kwadratisch queryplan bij
nieuwe projecten zonder plannerstatistieken te voorkomen.

## Twee procescards en configuratie

Ieder scandoel heeft twee cards: Website crawlen en Sitemap inlezen, met uitleg,
een startknop en een samenvatting van de instellingen. Het tandwiel opent een eigen
configuratiemodal voor de betreffende website en het betreffende proces.

De modal biedt de start-/sitemap-URL, het URL-maximum (standaard 100.000) en de
link-/sitemapdiepte (standaard 99), robotsmodus, parallellisme (standaard 8;
alleen voor websitecrawls) en requests per seconde (standaard 12). Opslaan past uitsluitend de card op de huidige
pagina aan; er wordt geen scan gestart of verzoek naar de server verstuurd.
Annuleren, het kruisje, Escape en sluiten via de achtergrond verwerpen de concept-
wijzigingen. Na vernieuwen van de pagina gelden de standaardwaarden. Er zijn hiermee
geen blijvende gebruikers- of projectvoorkeuren toegevoegd.

Starten verstuurt de opgeslagen cardwaarden naar de bestaande scan-API. Die bewaart
de gebruikte instellingen bij de batch en controleert projectrechten, hosts en
limieten opnieuw. Websitekeuze, eigen/concurrent-scandoelen, recente scans,
voortgang, annuleren en hervatten blijven beschikbaar. Automatische sitemapontdekking
is hiermee niet toegevoegd: een leeg sitemapadres gebruikt nog steeds /sitemap.xml.

### Gecontroleerde snelheidsproef

`ops/checks/scan_speed.py` gebruikt de echte netwerkclient, Redis-begrenzer,
parallelle uitvoering, extractie, checkpoints en publicatie. Alleen DNS en de
HTTP-antwoorden zijn vervangen door simulaties met 330 ms reactietijd: 4.366
pagina's en 1.146 redirects. Er gaan geen verzoeken naar externe websites.
Voer uitsluitend uit met de testmigratierol; alle databaserijen worden teruggedraaid:

```sh
docker compose --profile tools run --rm -T migrate python manage.py shell < ops/checks/scan_speed.py
```

Een echte website kan langzamer reageren of beperkingen opleggen; de proef
bewijst geen vaste doorlooptijd voor GMW of andere externe websites.

De gecontroleerde proef op 2 november 2026 verwerkte 4.366 pagina's, 5.529
bewaarde HTTP-documenten en de bijbehorende URL-observaties in 518,83 seconden
(8 minuten en 39 seconden), met circa 115 MiB piek-RSS. Redirects en hervatten
na een checkpoint zijn inbegrepen. De simulatie is geen meting van de GMW-server.

De aanvullende parallelle schaalproef met 100.000 URL's (zonder gesimuleerde
netwerkvertraging of pacing) controleerde ook alle 100.000 bronrecords, URL's
en observaties en 101.161 ruwe HTTP-documenten, inclusief redirects en hervatten.
Piek-RSS was circa 316 MiB voor één proces. De containerlimiet is daarom 1 GiB
voor beide scanprocessen samen; de eerdere seriële schaalproef blijft circa
111 MiB gebruiken. Dit is een verwerkingstest, geen extern crawltempo.

## Downloadfouten en onvolledige resultaten

Netwerkfouten krijgen een vaste foutcategorie en Nederlandse melding: DNS,
verbindings-/leestime-out, TLS, reset, geweigerde verbinding of verwerking.
Bronrecords bewaren error_code en error_details met fase, exceptiontypen en
eventuele errno/verify_code. De workerlog bevat scan-ID, host, SHA-256 van de
URL en deze diagnostiek. URL-paden, queries, antwoordinhoud en ruwe
exceptionberichten worden niet naar de log geschreven. De oorspronkelijke
bron-URL blijft binnen de normale projecttoegang beschikbaar.

Crawls met mislukte downloads of HTTP 403, 429 of 5xx krijgen de terminale
status **Onvolledig**. De bruikbare resultaten, foutrecords en documenten
blijven gepubliceerd en afzonderlijk verwijderbaar; ze blijven meetellen in
de URL-catalogus. Een echte HTTP 404 is een geldig SEO-resultaat en maakt
de scan op zichzelf niet onvolledig. Robotsweigeringen bij de respect-modus
blijven een apart resultaat. De scanpagina toont de aantallen en waarschuwing.

Herhaalde Celery-levering van een onvolledige scan publiceert geen duplicaten.
Een nieuwe poging start als nieuwe scan via Websitescans, zodat de oorspronkelijke
meting intact blijft. Dit voegt geen automatische blokkadeherkenning of pauzes toe.

Bestaande onterecht voltooide websitecrawls kunnen zonder dataverlies worden
herbeoordeeld met `python manage.py review_scan_outcomes` (alleen rapporteren)
of `python manage.py review_scan_outcomes --apply` (status en waarschuwing bijwerken).
Oude algemene HTTP-fouten blijven herkenbaar als historische fouten waarvan
de technische oorzaak niet is opgeslagen.

## Live voortgang en statusgrafiek

De card Scanvoortgang toont een lokaal getekende SVG-oppervlaktegrafiek.
Iedere drie seconden haalt de browser projectgebonden JSON op via
`GET /projects/<project-id>/scans/<batch-id>/progress/`. Iedere HTTP-code
krijgt een eigen kleur; downloadfouten zijn een aparte reeks. De grafiek
toont aantallen per drie seconden over de laatste vijf minuten; de legenda
toont totalen van de hele scan. Alle opgehaalde HTTP-documenten tellen mee,
ook robots.txt en redirectantwoorden. Het is geen voltooiingspercentage.

Het endpoint controleert projecttoegang en voorkomt caching. Actieve scans
worden read-only uit kleine SQLite-tellers en tijdvakken gelezen, zonder
de workerlock over te nemen of ruwe paginainhoud te lezen. De enige schrijver
blijft de scan-worker. Tellers worden atomair met records, wachtrijmutaties
en documenten bijgewerkt. Bestaande checkpoints worden bij hervatten
eenmalig bijgewerkt. De laatste vijf minuten worden bij het bestaande
checkpoint ook in scan_progress.live bewaard, zodat de grafiek na het
opruimen van een voltooide scan beschikbaar blijft. Historische scans
zonder rollup gebruiken alleen HTTP-status en tijd uit opgeslagen documenten.

De browser voorkomt overlappende verzoeken, pauzeert polling in verborgen
tabbladen en stopt bij afronding of verlies van toegang. Bij een tijdelijke
verbindingsfout blijven de laatste tellingen zichtbaar en wordt opnieuw
geprobeerd. De pagina biedt bij afronding een link om het volledige
resultaat te openen. Er worden geen externe grafiekbibliotheken of CDN's gebruikt.

### Geschatte resterende tijd

Websitecrawls tonen iedere drie seconden een tijdschatting:
`wachtrij / (verwerkte URL's / verstreken seconden)`. Het werkelijk te verwerken
aantal is begrensd door het ingestelde URL-maximum. De starttijd komt uit het
oorspronkelijke checkpoint en blijft behouden over vervolgtaken en herstarts.
De kaart toont ook verstreken tijd en gemiddeld tempo; nieuwe ontdekkingen
kunnen de schatting verhogen. Zonder verwerkt resultaat of geldige starttijd
blijft de schatting onbekend. Bij een lege wachtrij verschijnt 'Wordt afgerond…'.
Afgeronde, geannuleerde en mislukte scans krijgen geen actieve countdown.
Sitemapimports tonen geen tijdschatting: hun wachtrij bevat bestanden, terwijl
de verwerkte records pagina-URL's voorstellen.

## Standaardinstellingen teruggezet naar de eerdere GMW-scan

Nieuwe scans starten standaard met maximaal 10.000 URL's, diepte 20,
robots.txt respecteren, één gelijktijdig verzoek en maximaal één verzoek per
seconde. Dit zijn de instellingen van de eerdere GMW-crawl met 4.336 resultaten;
het oorspronkelijke tempo was serieel met minimaal één seconde tussen verzoeken.
De defaults staan centraal in scans/limits.py en worden gebruikt door formulier,
API, modal en workerfallback voor historische configuraties zonder tempo-opties.
Bestaande expliciet opgeslagen scaninstellingen blijven behouden. De limieten
van 100.000 URL's en diepte 100 blijven instelbaar, evenals sneller crawlen en
de auditmodus. Eerdere passages met 100.000/99/8/12 beschrijven de vorige defaults.
