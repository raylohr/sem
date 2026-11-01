# SEM

Django/Celery/Redis-projectskelet; geen publieke publicatie. Projectrechten, imports,
2FA en mailflows volgen in de volgende bouwstappen. De admin is daarom nog niet
bedoeld voor productiegebruik. SMTP staat expliciet uit (dummy backend).

## Credentials

Alle credentials staan in /srv/sem/.env naast manage.py (modus 0600).
Vul PG_PASSWORD_TEST_APP, PG_PASSWORD_TEST_MIGRATE, PG_PASSWORD_PROD_APP en
PG_PASSWORD_PROD_MIGRATE in. De Django-sleutels zijn al gegenereerd.
Gebruik enkele aanhalingstekens rondom waarden. Niet met source laden.
Compose leest .env en geeft alleen de relevante credentials aan elke service door.
Django ontvangt deze via de containeromgeving. Het bestand wordt niet ingebouwd.

## Testomgeving starten

```sh
cd /srv/sem
docker compose build
docker compose --profile tools run --rm migrate
docker compose up -d web worker scan-worker
curl http://127.0.0.1:8001/health/
curl http://127.0.0.1:8001/ready/
```

Voor beheercommando's met migratierechten:
```sh
docker compose --profile tools run --rm migrate python manage.py createsuperuser
```

Voor productie: gebruik bij ieder commando:
```sh
SEM_PORT=8002 docker compose -f compose.yaml -f compose.prod.yaml up -d web worker scan-worker
```
Gebruik dezelfde bestanden en SEM_PORT voor productie-migraties en beheer.
Test en productie hebben aparte projectnamen en volumes. Beide binden alleen op
localhost; reverse proxy en HTTPS volgen later. SMTP staat uit, 2FA volgt nog.
Er worden geen migraties bij containerstart uitgevoerd.

## Tests

Gebruik alleen de testmigratierol voor Django-databasetests, met --keepdb en de
vooraf gemaakte sem_test. Geen productiecredentials voor tests gebruiken.
Dependencies zijn exact vastgelegd in requirements.txt; werk die bewust bij.

Specificatie: docs/01-technische-specificatie.md.

## Projecten en toegang

Platformbeheerders maken projecten en lidmaatschappen in Django-admin aan.
Projectrollen: admin, editor, viewer. Gewone staffstatus geeft geen toegang.
Projectoverzicht: GET /projects/; detail: GET /projects/<uuid>/.
Projectbeheerder kan bestaande actieve gebruikers koppelen of hun rol wijzigen:
POST /projects/<uuid>/members/ met JSON {"user_id": 1, "role": "viewer"}.
Intrekken: POST /projects/<uuid>/members/<user_id>/remove/.
Alle mutaties vereisen een ingelogde sessie en CSRF-token. Minimaal één actieve
projectbeheerder blijft bij mutaties via deze endpoints behouden. Platformbeheer
via Django-admin kan dit bewust overrulen. Gewone gebruikers loggen in via
/accounts/login/. De projectendpoints retourneren voorlopig JSON; een volledige
projectinterface, 2FA, auditlog van rolwijzigingen en mailflows volgen nog.

## Bronnen en importbatches

GET /projects/<uuid>/sources/ geeft batches met bron, methode, gebruiker,
importtijd, meetdatum/periode, status en aantallen. POST op sources/create/ maakt
voorlopig uitsluitend batchmetadata aan (JSON: source, method, label en optionele
original_filename/measured_at/period_start/period_end). Gebruiker, project, status
en importtijd worden door de server bepaald. Dit endpoint maakt alleen metadata; gebruik sources/upload/ voor echte bestanden.
API-ophaalacties volgen later.

POST /projects/<uuid>/sources/<batch-uuid>/remove/ verwijdert als projectbeheerder
de batch en gekoppelde SourceRecords. Andere batches blijven behouden. Een minimaal
verwijderlog bewaart project, batch-ID, actor en verwijdertijd. Bronbestanden worden na batchverwijdering via een duurzame opruimwachtrij
verwijderd; afgeleide analyses volgen later. publish_records is de transactionele toegang voor toekomstige workers:
controleert opnieuw projectrechten, publiceert eenmaal en herstelt geen verwijderde
batch. De bronnenweergave is voorlopig JSON, met read-only inspectie in admin.


## Bestandsimports

POST /projects/<uuid>/sources/upload/ ontvangt multipart met veld file.
Ondersteund: XLS, XLSX, UTF-8 CSV/TSV en UTF-16 CSV/TSV, maximaal 20 MiB,
100 tabbladen, 100.000 records en 500.000 cellen. Bestandstype en rapportstructuur
worden gecontroleerd; bronherkenning voor GSC, Ahrefs, Screaming Frog en de
samengestelde contentaudit. Formules worden niet uitgevoerd.

Optionele velden: label, source (ter controle), measured_at, period_start, period_end.
preview=1 geeft tabbladen, kolommen, tellingen, waarschuwingen en vijf voorbeeldrecords
zonder iets op te slaan. Zonder preview: HTTP 202, privé bronbestand, checksum,
inhoudsfingerprint en Celery-verwerking. Een duplicate geeft 409 met batch-ID;
allow_duplicate=1 staat expliciet een extra batch toe, binnen hetzelfde project.

GET sources/<batch-id>/download/ downloadt het bronbestand na projectcontrole.
GET sources/<batch-id>/records/?offset=0 geeft maximaal 100 verwerkte records,
met originele kolommen en sheet/row-provenance. Recordaantallen tellen ook
metadata- en diagramregels mee; de tabbladsamenvatting toont afzonderlijke aantallen.
POST sources/<batch-id>/retry/ start een pending of failed upload opnieuw.
De oorspronkelijke importgebruiker blijft behouden; ingetrokken toegang blokkeert
verwerking. Alleen complete batches leveren gepubliceerde records op.

Bij batchverwijdering verdwijnt de database-inhoud direct, het bronbestand via een
Celery-opruimtaak. Bij brokeruitval blijft de opruimopdracht in PostgreSQL staan.
Herstel/onderhoud kan zonder broker met:
```sh
docker compose exec web python manage.py cleanup_import_files
```
Er is nog geen periodieke opruimscheduler. De interface bestaat nog uit endpoints;
het uploadformulier en de volledige gebruikersinterface volgen later.

Alle 18 aangeleverde voorbeelden zijn read-only geparseerd; originele bestanden
zijn niet gewijzigd of in Git opgenomen. De twee dubbele GSC-404-exporten worden
als gelijke inhoud herkend, evenals de onvolledige 1000/2207-redirectexport.
Datums 1970-01-01 blijven in de bronwaarden staan en krijgen een onbekend-markering.
De contentaudit wordt als bronregels ingelezen; vrije tekst wordt niet automatisch
een bewezen technisch feit. URL-normalisatie en gecombineerde metrics volgen later.

## Google Search Console API

De eerste API-connector is gebouwd maar blijft uitgeschakeld zonder Google OAuth-
configuratie. Zie docs/02-gsc-integratie.md voor configuratie, endpoints, toegangs-
controle en grenzen. Tokens worden versleuteld opgeslagen; imports zijn afzonderlijke
batches met ruwe API-antwoorden. Er is nog geen Google-account gekoppeld.


## Sitemap en crawler

Projectbeheerders configureren expliciete scandoelen/hosts; bewerkers starten
sitemapimports en begrensde crawls. Scans gebruiken een eigen worker/queue,
respecteren robots.txt en blokkeren interne netwerkadressen en onveilige redirects.
Zie docs/03-scans.md voor endpoints, resultaten, annulering en productlimieten.

## Webinterface en lokale Bootstrap

De startpagina verwijst naar /workspace/. De interface bevat login, projectkaarten,
projectdashboard, bronfilters/upload/preview, batchdetails/verwijderen, websitescans
met doel- en startformulieren, en GSC-koppelingen. Bestaande JSON-routes onder
/projects/ blijven beschikbaar. Projectrechten worden ook voor alle HTML-pagina's
op de server gecontroleerd. Privépagina's krijgen no-cache-headers.

Bootstrap 5.3.8 staat volledig lokaal in core/static/vendor/bootstrap/5.3.8/,
inclusief bundle-JavaScript, sourcemaps en MIT-licentie. CSS en JS zijn bij download
gecontroleerd tegen de officiële SHA-384-hashes. WhiteNoise serveert de bestanden
met versiehashes; er zijn geen CDN's, webfonts of andere externe runtime-assets.
Eigen stijlen en formulieren staan in core/static/sem/. Bootstrap-bron:
https://getbootstrap.com/docs/5.3/getting-started/download/

104 Django-tests slagen. Een geïsoleerde Chromium-controle verifieerde desktop
(1440px), mobiel (390px), Bootstrap-widgets, nul externe requests en de echte
preview/upload/verwijderflow. Voorbeeldgegevens en browsersessie zijn opgeruimd.

## Merkmateriaal

De webinterface gebruikt Search Visibility Toolkit als productnaam, met het
ongewijzigde aangeleverde artwork uit /home/raymond/data/seo/logo. De oorspronkelijke
mountlocatie /mnt/texel/tex-gps/home/raymond/data/seo/logo correspondeert op tex-gps
met die lokale map. Gebruikte assets staan in core/static/brand/.

Het compacte logo staat in de navigatie en beheeromgeving, het tweeregelige logo
op de loginpagina. SVG/PNG-favicons en een touch-icon zijn toegevoegd. De marineblauwe
kleur #0B2D4D en cyaan #00B3E6 zijn doorgevoerd in navigatie, knoppen en accenten.
Alle artwork-assets worden lokaal geserveerd; originele bronbestanden zijn ongewijzigd.
Django-beheertemplates staan in templates/admin/ met voorrang via TEMPLATES.DIRS.
De technische projectnaam en repository blijven sem.

## Gecombineerd URL-overzicht

Onder de projecttab URL-overzicht staan gegevens uit alle voltooide bronnen per
URL samen, met filters en een dossier van afzonderlijke metingen. GSC-prestaties,
Ahrefs-schattingen, technische scans en auditbevindingen blijven gescheiden.
Bronrecord, gebruiker, importtijd en meetmoment blijven bereikbaar; onbekende datums
worden niet ingevuld met importtijd. Sitestructuurcijfers zijn een aparte scope.

Indexering en batchverwijdering werken automatisch en transactioneel. Importdetails
tonen niet-herkende URL-regels zonder brondata weg te gooien. Zie docs/04-url-overzicht.md
voor normalisatie, filters, API-routes, indexherbouw en praktijktestresultaten.
