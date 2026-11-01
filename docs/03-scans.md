# Sitemapimport en basiscrawler

Scans draaien op een aparte Celery-queue (scans) met één scan-worker, CPU-limiet
0,5 en geheugenlimiet 512 MiB. Imports en GSC-ophaalacties blijven op queue celery.
Geen wijzigingen aan de GPS-app of productieomgeving.

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

- Crawl: standaard 50, maximaal 200 URL-observaties; diepte maximaal 5.
- Sitemap: standaard 1.000, maximaal 5.000 URL's; maximaal 50 sitemapdocumenten.
- Tijd: standaard 120, maximaal 180 seconden coöperatief; netwerk-time-outs zijn 5s.
- Requesttempo: standaard minstens 1 seconde per host; robots crawl-delay en
  request-rate kunnen dat verder vertragen.
- robots.txt wordt gerespecteerd. 404/410 betekent geen robotsbestand; andere
  fouten worden conservatief geblokkeerd. Er is geen negeer-robots-optie.
- Maximaal 5 MiB per document, inclusief begrensde uitpakking; maximaal 25 MiB
  totale overdracht én totale uitgepakte documentinhoud per batch. Dit zijn eigen
  productlimieten, lager dan de maximale omvang van het sitemapprotocol.
- Alleen HTTP/HTTPS op standaardpoorten, zonder gebruikersnaam/wachtwoord.
- Elke host moet exact zijn toegestaan. Alle DNS-antwoorden moeten publiek zijn;
  private, loopback, link-local, multicast en gereserveerde adressen worden geweigerd.
- De TCP-verbinding gebruikt het gecontroleerde IP; HTTPS houdt de oorspronkelijke
  SNI/Host en certificaatverificatie. Iedere redirect wordt opnieuw gecontroleerd.
- Geen omgevingsproxy's, cookies of externe authenticatie in scanverzoeken.
- Bij 429/503 stopt de crawl; limieten/onvolledigheid verschijnen als waarschuwingen.

Deze versie bevat nog geen periodieke planning, vergelijking met eerdere scans,
gecombineerd URL-dashboard of browsergestuurde cookie/JavaScript-scan.

## Bronnen

- https://www.sitemaps.org/protocol.html
- https://docs.python.org/3.12/library/urllib.robotparser.html
- https://urllib3.readthedocs.io/en/stable/advanced-usage.html
