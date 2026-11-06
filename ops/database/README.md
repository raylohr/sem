# Productiedatabase

De enige blijvende database is `sem_prod` op db-pg (192.168.178.152).
De appserver is tex-gps (192.168.178.159). Verbindingen gebruiken TLS met
`sslmode=verify-full` en `ops/database/certs/db-pg.crt`.

- `sem_prod_app`: runtimeaccount; alleen gegevens lezen en schrijven.
- `sem_prod_migrate`: eigenaar van het applicatieschema, uitsluitend voor migraties.

Beide accounts hebben unieke wachtwoorden en geen superuser-, CREATEDB- of
CREATEROLE-rechten. Schema `sem` bevat de applicatie; `public` bevat PostGIS.
Runtimeaccounts krijgen geen migratiecredentials. De productiedatabase wordt niet
voor geautomatiseerde tests gebruikt. `ops/test.sh` maakt daarvoor een tijdelijke,
geïsoleerde PostgreSQL-container zonder productiecredentials of externe poorten.

`bootstrap.sql` is uitsluitend voor een nieuwe databasehost; niet opnieuw uitvoeren
op een bestaande installatie. Stel wachtwoorden interactief in met `\password`.
Het HBA-fragment is een voorbeeld voor databasebeheer; wijzig geen bestaande
beheerregels of regels voor andere applicaties zonder inspectie.

## Opheffen van de oude devdatabase

De applicatieconfiguratie gebruikt `sem_test` niet meer. CONNECT-rechten zijn van
beide devaccounts ingetrokken; nieuwe verbindingen zijn gecontroleerd geblokkeerd.
De lege database en rollen zijn nog aanwezig op de databasehost. Definitief verwijderen
van de oude database en rollen vereist beheer op de databasehost:

```sh
sudo -u postgres psql -X -v ON_ERROR_STOP=1 -f retire-dev.sql
```

Het script controleert de database-eigenaar en richt zich uitsluitend op
`sem_test`, `sem_test_app` en `sem_test_migrate`. Productie blijft behouden.
Backup en herstel van productie zijn beschreven in `docs/06-productie-deployment.md`.
