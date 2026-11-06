# Eén productieomgeving

Er is één vaste applicatieomgeving: `sem-prod`, bereikbaar op `https://sem.lohr.nl`.
Er is geen devwebsite of blijvende testomgeving. Code wordt in `/srv/sem` gewijzigd,
automatisch getest en daarna naar productie uitgerold. Wijzigingen zijn na de release
direct zichtbaar voor gebruikers.

## Automatisch testen

```sh
./ops/test.sh
```

Dit bouwt een testimage en start een tijdelijke PostgreSQL-container op een intern
Docker-netwerk zonder gepubliceerde poorten. De tests gebruiken geen `.env`, geen
productiecredentials en geen productiegegevens. De tijdelijke database staat in
RAM en containers en netwerk worden na afloop verwijderd, ook bij een testfout.
De testconfiguratie gebruikt een geheugencache en mailbackend zonder externe mail.
De testrunner weigert databases buiten deze configuratie.

## Release

Commit en push de gewenste wijzigingen. Voer vanuit een schone checkout uit:

```sh
./ops/deploy-production.sh
```

Het script bouwt uit de gepushte Git-commit, voert eerst automatisch alle tests uit,
stopt daarna schrijvende processen, voert migraties uit en vervangt de containers.
Een testfout stopt de release voordat productie wordt onderbroken. Een push alleen
start geen release. Iedere applicatie- en gatewayimage heeft de Git-commit als tag.
Er zijn geen broncodemounts in productie. Startcontroles lopen iedere seconde tijdens
de opstartfase; daarna geldt het normale healthcheckinterval.

Een specifieke gepushte commit deployen kan na checkout met
`./ops/deploy-production.sh COMMIT`. Databasemigraties worden niet automatisch
teruggedraaid. Maak voor ingrijpende datawijzigingen een database- en mediabackup
met `python3 ops/backup-production.py`.

## Beheer

```sh
export SEM_PROD_IMAGE=$(docker inspect sem-prod-web-1 --format '{{.Config.Image}}')
export SEM_PROD_GATEWAY_IMAGE=$(docker inspect sem-prod-gateway-1 --format '{{.Config.Image}}')
docker compose ps
```

Alle services staan in `compose.yaml`. Productie gebruikt database `sem_prod`,
accounts `sem_prod_app` en `sem_prod_migrate`, netwerk `sem-prod_default` en
volumes `sem-prod_media` en `sem-prod_redis_data`. Alleen de migratieservice krijgt
migratiecredentials. Wachtwoorden en sleutels staan buiten Git in `.env`.

## Netwerk

De reverse proxy op tex-revprox (192.168.178.151) verzorgt HTTPS voor sem.lohr.nl
en stuurt verkeer naar de gateway op 192.168.178.159:8002. De gateway laat alleen
het proxy-IP toe en overschrijft forwarded headers. De applicatie zelf luistert
alleen op localhost:8002; localhost:8001 wordt niet meer gebruikt.
De applicatie en databasehost blijven dezelfde gedeelde infrastructuur gebruiken.

Controleer na een release `http://127.0.0.1:8002/ready/`, login en belangrijke
gebruikersacties. De eerste beheerder moet bij aanmelden 2FA instellen.
