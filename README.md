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
docker compose up -d web worker
curl http://127.0.0.1:8001/health/
curl http://127.0.0.1:8001/ready/
```

Voor beheercommando's met migratierechten:
```sh
docker compose --profile tools run --rm migrate python manage.py createsuperuser
```

Voor productie: gebruik bij ieder commando:
```sh
SEM_PORT=8002 docker compose -f compose.yaml -f compose.prod.yaml up -d web worker
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
