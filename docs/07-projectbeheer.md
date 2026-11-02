# Projectbeheer in de website

Platformbeheerders maken een project aan via de knop Project aanmaken op de
projectenpagina (`/workspace/projects/create/`). De maker wordt automatisch
projectbeheerder. Naam en volledig http/https-websiteadres zijn verplicht.
Het overzicht en projectdashboard vormen de leesweergave.

Projectbeheerders en platformbeheerders wijzigen naam/website via de tab
Instellingen (`/workspace/projects/UUID/edit/`). Dit herschrijft geen bronrecords,
scandoelen of API-property's. Gebruik de bestaande projecttab Toegang om actieve
gebruikers rollen te geven. Bewerkers en lezers hebben geen instellingsrechten.

Alleen platformbeheerders kunnen verwijderen. De bevestigingspagina toont de
actuele aantallen en vereist de exacte projectnaam plus het eigen wachtwoord.
GET verwijdert nooit iets. Alle POST-formulieren vereisen CSRF en accountbeleid.

Verwijdering wist imports, bronrecords, URL-observaties, scanresultaten, scandoelen,
API-koppelingen en projectlidmaatschappen transactioneel. Bronbestanden krijgen een
duurzame opruimopdracht. Bij Redis-uitval blijft die opdracht in PostgreSQL staan;
`cleanup_import_files` kan deze later uitvoeren. Late taken kunnen verwijderde
batches niet opnieuw aanmaken. Andere projecten en gebruikers blijven bestaan.
Google-tokens worden lokaal verwijderd; dit is geen intrekking bij Google van
mogelijk elders gebruikte OAuth-toestemming.

Beveiligings- en batchverwijderlogs blijven bewaard, inclusief het oorspronkelijke
project-ID na projectverwijdering. Maken en wijzigen krijgen ook een auditregel.
De oude Django-admin add/change/delete-URL's verwijzen naar de websitepagina's;
mutaties en bulkverwijdering via Django-admin zijn geblokkeerd.

## Backup voor een release met migraties

Op tex-gps:

```sh
cd /srv/sem
./ops/backup-production.py
```

Dit maakt een PostgreSQL custom-format dump en een media-archief onder
/srv/sem-backups met beperkte bestandsrechten. Credentials worden uit de draaiende
productiecontainer gelezen en niet gelogd of in shellargumenten gezet. De dump wordt
met pg_restore --list gecontroleerd. Een media- en databaseback-up tijdens actieve
uploads is geen atomaire momentopname: plan voor herstelpunten met schrijfverkeer
een onderhoudsvenster en kopieer backups ook naar een afzonderlijke, beveiligde
locatie. Er is hiermee nog geen volledige herstelproef of automatische retentie.
