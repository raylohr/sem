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
