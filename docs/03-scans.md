# Sitemapimport en basiscrawler

Scans draaien op een aparte Celery-queue (scans) met één scan-worker, CPU-limiet
0,5 en geheugenlimiet 512 MiB. Imports en GSC-ophaalacties blijven op queue celery.
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
- Requesttempo: standaard minstens 1 seconde per host; robots crawl-delay en
  request-rate kunnen dat verder vertragen.
- robots.txt wordt gerespecteerd. 404/410 betekent geen robotsbestand; andere
  fouten worden conservatief geblokkeerd. Er is geen negeer-robots-optie.
- Maximaal 5 MiB per document, inclusief begrensde uitpakking. Het totale budget
  is minimaal 25 MiB en schaalt mee met het URL-maximum; zie hieronder. Dit zijn
  productlimieten, lager dan de maximale documentomvang van het sitemapprotocol.
- Alleen HTTP/HTTPS op standaardpoorten, zonder gebruikersnaam/wachtwoord.
- Elke host moet exact zijn toegestaan. Alle DNS-antwoorden moeten publiek zijn;
  private, loopback, link-local, multicast en gereserveerde adressen worden geweigerd.
- De TCP-verbinding gebruikt het gecontroleerde IP; HTTPS houdt de oorspronkelijke
  SNI/Host en certificaatverificatie. Iedere redirect wordt opnieuw gecontroleerd.
- Geen omgevingsproxy's, cookies of externe authenticatie in scanverzoeken.
- Bij 429/503 stopt de crawl; limieten/onvolledigheid verschijnen als waarschuwingen.

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
- Het tempo blijft minimaal één verzoek per seconde per host. Robotsbeleid kan
  vertragen of blokkeren; 429/503 stopt een crawl. Het URL-maximum garandeert geen
  aantal bereikbare pagina's. 100.000 echte pagina's kosten bij één verzoek per
  seconde minstens circa 28 uur, exclusief vertragingen en redirects.

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
