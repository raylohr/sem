# PostgreSQL-inrichting SEM

Status 1 november 2026: voorbereid, NIET uitgevoerd. Vanaf tex-gps time-out op
192.168.178.152:5432 en :22. Oorzaak (routing/firewall/serverstatus/poort) nog onbekend.

## Scheiding

| Database | Runtime Django/Celery | Alleen voor migraties |
|---|---|---|
| sem_prod | sem_prod_app | sem_prod_migrate |
| sem_test | sem_test_app | sem_test_migrate |

Iedere rol krijgt een uniek wachtwoord. Geen superuser, CREATEDB, CREATEROLE of
lidmaatschap van andere rollen. Migratieaccounts zijn eigenaar van uitsluitend
hun eigen database/schema. Runtimeaccounts kunnen rijen lezen/schrijven, maar
geen tabellen aanmaken/verwijderen. Web/worker krijgen nooit migratiecredentials.
Schema sem bevat applicatietabellen, public bevat de door beheer geïnstalleerde
PostGIS-extensie. Migraties moeten consequent met het migratieaccount draaien.

sem_test is een vooraf aangemaakte, wegwerpbare testdatabase zonder productiegegevens.
Voor Django-tests: TEST.NAME=sem_test en --keepdb, uitvoeren met sem_test_migrate;
zo is CREATEDB niet nodig. Controleer expliciet database en account vóór tests.
Deze database wordt niet tegelijk als blijvende acceptatieomgeving gebruikt.
Gebruik voor parallelle tests lokale, geïsoleerde PostgreSQL/PostGIS-containers.
De gedeelde server deelt capaciteit: begrens testbelasting; aparte testserver kan later.

## 1. Inspecteer op de databasehost

Via bestaande beheerconsole/SSH, zonder config of firewall blind te wijzigen:

```sh
sudo -u postgres psql -X
```

```sql
SELECT version();
SHOW listen_addresses;
SHOW port;
SHOW ssl;
SHOW hba_file;
SHOW config_file;
SELECT name, default_version FROM pg_available_extensions WHERE name='postgis';
SELECT datname FROM pg_database WHERE datname IN ('sem_prod','sem_test');
SELECT rolname FROM pg_roles WHERE rolname LIKE 'sem_%';
```

Controleer daarnaast firewall/routing en luisterende poorten. Maak eerst een
back-up van gewijzigde serverconfiguraties. Stop bij bestaande SEM-namen en
inspecteer ze; bootstrap.sql is bewust een eenmalig script. Installeer zo nodig
het bij de serverversie passende PostGIS-pakket na inspectie.

## 2. Maak databases en rollen

Kopieer bootstrap.sql naar de databaseserver en voer uit:

```sh
sudo -u postgres psql -X -v ON_ERROR_STOP=1 -f /pad/naar/bootstrap.sql
```

Stel daarna wachtwoorden interactief in (geen plaintext in shellhistory):

```sql
SET password_encryption = 'scram-sha-256';
\password sem_prod_migrate
\password sem_prod_app
\password sem_test_migrate
\password sem_test_app
```

Bewaar wachtwoorden in een secretsysteem of afzonderlijke bestanden met modus
0600 buiten Git. Geen wachtwoorden in deze handleiding of chat plakken.

## 3. Netwerk en TLS

Sta TCP 5432 alleen toe vanaf appserver 192.168.178.159 en bestaande expliciete
beheerhosts. Wijzig geen toegang voor andere bestaande apps. Stel zo nodig
listen_addresses in op het LAN-adres en localhost, afgestemd op bestaand gebruik;
die wijziging vereist een geplande herstart. Voeg het HBA-fragment vóór brede
LAN-regels toe en controleer volgorde/bestaande beheerregels. HBA is first-match.
Lokale toegang blijft onder bestaand serverbeheerbeleid vallen.

Gebruik een vertrouwd servercertificaat; client sslmode=verify-full en een CA-bestand.
Bij verbinden op IP moet het certificaat 192.168.178.152 als IP-SAN bevatten;
anders een passende DNS-naam gebruiken. Geen permanente TLS-verificatie omzeilen.
HBA-fragment pas activeren als SSL werkt. Docker-uitgaand verkeer verschijnt
normaal als hostadres; verifieer werkelijk bronadres bij ingebruikname.

```sql
SELECT line_number, error FROM pg_hba_file_rules WHERE error IS NOT NULL;
SELECT pg_reload_conf();
```

Controleer de herlaadlog en test nieuwe verbindingen; een reload-resultaat alleen
bewijst niet dat alle regels correct zijn toegepast.

## 4. Verificatie vanaf appserver

```sh
psql "host=192.168.178.152 port=5432 dbname=sem_prod user=sem_prod_app sslmode=verify-full sslrootcert=/pad/naar/ca.crt" -W
```

Controleer current_user, current_database(), SHOW search_path, PostGIS_Version()
en pg_stat_ssl voor de eigen verbinding. Test expliciet:

- Productie-app kan geen verbinding maken met sem_test, en omgekeerd.
- SEM-accounts kunnen geen andere databases benaderen via TCP.
- Appaccount kan geen CREATE TABLE uitvoeren in sem of public.
- Migratieaccount kan een tijdelijke verificatietabel maken; appaccount kan daarop
  SELECT/INSERT/UPDATE/DELETE uitvoeren dankzij default privileges. Verwijder die
  verificatietabel daarna met het migratieaccount.
- Verbinding zonder TLS en vanaf een niet-toegestane host wordt geweigerd.

Regel een back-up van sem_prod met herstelproef, inclusief rollen/configuratie en
bijpassende uploadbestanden. sem_test bevat geen productie-export of secrets.

Officiële referenties:
- https://www.postgresql.org/docs/current/auth-pg-hba-conf.html
- https://www.postgresql.org/docs/current/ddl-priv.html
- https://www.postgresql.org/docs/current/libpq-ssl.html
