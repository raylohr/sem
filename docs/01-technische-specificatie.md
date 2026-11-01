# SEM-platform — technische specificatie

Datum: 1 november 2026  
Status: eerste specificatie; infrastructuurgegevens deels nog open

## Doel

Een webapp op sem.lohr.nl die per project SEO/SEM-brongegevens, eigen scans en inhoudelijke beoordelingen samenbrengt. Het platform ondersteunt meerdere gebruikers met afzonderlijk instelbare projecttoegang. Werknaam: SEM; projectdirectory: /srv/sem. De naam kan later veranderen.

## Eerste productieversie

- Accounts op uitnodiging of aangemaakt door een beheerder; geen publieke registratie.
- Login, e-mailwachtwoordherstel, TOTP-2FA en herstelcodes. 2FA verplicht voor platformbeheerders; instelbaar voor overige gebruikers. Herstel en reset zijn geaudit.
- Projecten met een primaire website en expliciet toegestane crawlhosts; ondersteuning voor andere domeinen en concurrenten in het model.
- Projectlidmaatschappen met rollen: projectbeheerder, bewerker en lezer.
- Bronnenoverzicht, afzonderlijke importbatches, status, fouten en aantallen.
- Imports van de aanwezige GSC-, Ahrefs- en Screaming Frog-bestanden, inclusief XLS, XLSX, UTF-8 CSV en UTF-16 TSV.
- Eerste GSC-API-koppeling voor Search Analytics (URL/datum met klikken, vertoningen, CTR en positie). De aanwezige volledige indexeringsrapporten blijven uploadimports; die veronderstellen we niet één-op-één via een API te kunnen ophalen. API-dekking en toegangsvoorwaarden worden vóór implementatie geverifieerd.
- Sitemapimport en begrensde HTML-crawl met statuscode, titel, description, koppen, canonical, robots, taal en links.
- Een gecombineerd URL-overzicht met filters op bron, meetmoment, paginatype en technische signalen.
- Productiehosting, logging, back-up en herstelcontrole.

De samengestelde contentaudit wordt als aparte importsoort behandeld. In de eerste versie worden bronbestand, tabbladen en geselecteerde bevindingen/acties bewaard; vrije tekst uit de werkmap wordt niet automatisch als bewezen technisch feit geïnterpreteerd. Volledige redactionele auditworkflow volgt later.

## Latere uitbreidingen

Ahrefs-API afhankelijk van abonnement/API-toegang; overige API-connectoren; periodieke imports en crawls via Celery Beat; concurrentiemonitoring; wijzigingsrapportages en meldingen; browsergestuurde JavaScript- en cookieonderzoeken vóór/na toestemming; contentanalyse en AI met bronverwijzingen. Advertentiedata en conversies zijn uitbreidingen op de aanvankelijke SEO-focus.

## Rechten

| Actie | Lezer | Bewerker | Projectbeheerder |
|---|---|---|---|
| Projectgegevens en rapporten bekijken | Ja | Ja | Ja |
| Imports en scans starten | Nee | Ja | Ja |
| Bevindingen en acties bewerken | Nee | Ja | Ja |
| Importbatch verwijderen | Nee | Nee | Ja |
| Bronverbindingen configureren | Nee | Nee | Ja |
| Bestaande gebruikers aan project koppelen | Nee | Nee | Ja |

Alleen een platformbeheerder maakt accounts aan, beheert platforminstellingen en kan alle projecten beheren. Projectbeheerders krijgen geen toegang tot andere projecten. Nieuwe projecten zijn uitsluitend zichtbaar voor expliciete leden en platformbeheerders. API-geheimen worden niet teruggetoond aan gebruikers.

Autorisatie geldt op de server voor iedere aanvraag, export, download en achtergrondtaak. Alleen een project-ID in een URL of taakargument geeft geen toegang. Een taak controleert bij uitvoering opnieuw projecttoegang of een actieve geplande opdracht. Intrekken van toegang verhindert nieuwe verwerking onder die gebruiker. Dit wordt expliciet getest.

## Architectuur

```text
Gebruiker → HTTPS sem.lohr.nl → tex-revprox → Django/Gunicorn op tex-gps
                                               ├─ productiedatabase (apart schema/database en account)
                                               ├─ privébestandsopslag
                                               └─ Redis → Celery-workers
```

Django, Celery en eigen Redis draaien in Docker Compose. Web en worker gebruiken dezelfde applicatie-image. Redis publiceert geen hostpoort en is geen blijvende bronopslag. Databasegegevens en taakstatussen worden duurzaam opgeslagen; verwerking kan na fouten opnieuw worden gestart zonder dubbele resultaten. Aanvankelijk server-rendered Django-pagina's; geen aparte frontend-app nodig.

Een eigen database op de bestaande PostgreSQL-productieserver op 192.168.178.152:5432 krijgt een eigen beperkt account. De opgegeven Django-backend is django.contrib.gis.db.backends.postgis. PostgreSQL-versie, beschikbaarheid van de PostGIS-extensie in de nieuwe database, databasenaam en account moeten bij aansluiting worden vastgesteld. Ontwikkeling en tests gebruiken een afzonderlijke database. Er is nog geen databaseverbinding uitgevoerd of database aangemaakt.

Privébestanden staan buiten de webroot, in blijvende opslag buiten de containerlaag. Downloads lopen via projectautorisatie. Bronbestanden en database worden samen opgenomen in het back-up- en herstelplan.

## Conceptueel datamodel

- User: globaal account, actiefstatus, 2FA en beheerrechten. Vanaf het begin een eigen Django-usermodel.
- Project: naam, primaire website, instellingen en tijdzone.
- ProjectMembership: unieke combinatie gebruiker/project en rol.
- SiteTarget: eigen website of concurrent, toegestane hosts en crawlgrenzen.
- SourceConnection: projectgebonden bron, configuratie, eigenaar en verwijzing naar beschermde credentials.
- ImportBatch: één upload, API-ophaalactie of eigen scan, bron, project, uitvoerder, verantwoordelijke gebruiker, importtijd, meetperiode, status, foutmeldingen, aantallen, checksum en parser-/connectorversie.
- SourceRecord / Observation: batchgebonden bronrecord of getypeerde meting; altijd herleidbaar naar de oorspronkelijke rij, sheet of API-pagina.
- ProjectURL: projectgebonden URL-identiteit, oorspronkelijke schrijfwijze en afgeleide normalisatie. Parameters en verschillende hosts worden niet zonder beleid samengevoegd.
- Finding / Action: menselijke of afgeleide bevinding, bewijsverwijzingen, prioriteit en opvolgstatus.
- AuditEvent: wie welke beheeractie wanneer uitvoerde, zonder broninhoud of credentials te loggen.
- ScheduledJob (later): planning, bron/doel, verantwoordelijke gebruiker en uitvoeringshistorie.

URL-metingen blijven per bron en meetmoment naast elkaar bestaan. Er is geen onverklaarbare overschrijving door de laatst geïmporteerde waarde. Schattingen van Ahrefs en metingen uit GSC blijven afzonderlijke metrieksoorten. Hiërarchische sitestructuurdata wordt niet als individuele paginameting opgeteld.

## Herkomst en tijd

Elke record heeft een batch en daarmee project, bron, gebruiker en importtimestamp. Waar beschikbaar bewaren we ook bronmeetdatum, rapportageperiode, crawldatum en bronpublicatiedatum. Tijdstippen worden in UTC opgeslagen en in de gekozen tijdzone getoond; datumwaarden zonder tijdzone blijven datumwaarden. Ontbrekende meetdatums blijven onbekend; importtijd vervangt ze niet. Bij automatisering worden systeemuitvoering én verantwoordelijke gebruiker geregistreerd.

## Import en bronnenoverzicht

Upload/API-opvraag → validatie → voorlopige batch → Celery-verwerking → zichtbare resultaten.

Het overzicht toont bron, bestandsnaam of API-selectie, gebruiker, importtijd, meetperiode, status, aantallen, waarschuwingen en verwijderactie. Er zijn controles op bestandsinhoud, grootte, structuur, type en dubbelen. Een lege export is een geldige batch met nul resultaten, geen bewijs dat een hele website probleemvrij is. Gedeeltelijke API-resultaten en exportlimieten worden zichtbaar gemaakt. Publicatie van resultaten gebeurt pas na succesvolle afronding; mislukte batches blijven inspecteerbaar en veilig opnieuw uitvoerbaar.

De aangeleverde voorbeelden in /home/raymond/data/seo zijn uitsluitend fixtures/bronvoorbeelden en worden niet gewijzigd. Bekende gevallen voor importvalidatie: dubbele GSC-404-export, redirectoverzicht met 2.207 URLs maar slechts 1.000 detailregels, lege XLS-rapporten, datum 1970-01-01 en UTF-16 tabgescheiden CSV.

## Batch verwijderen

Een projectbeheerder kan één batch verwijderen met zichtbare impact en bevestiging. Lopende verwerking wordt gestopt of ongeldig gemaakt; late taakresultaten mogen de batch niet opnieuw vullen. Bronbestand, opgeslagen API-payloads en batchgebonden records verdwijnen. Andere batches blijven intact. Gedeelde URL-identiteiten blijven bestaan zolang andere gegevens ernaar verwijzen.

Afgeleide analyses worden verwijderd, opnieuw berekend of expliciet ongeldig gemarkeerd. Zelfstandig geschreven notities blijven bestaan, maar verliezen de verwijderde bewijsinhoud en krijgen een melding dat bronbewijs ontbreekt. Een minimaal verwijderlog blijft bestaan met actor, tijdstip en batch-ID, zonder verwijderde broninhoud. Back-ups volgen een nog vast te stellen bewaartermijn; verwijderde batches mogen na herstel niet ongemerkt opnieuw actief worden.

## Hosting en beheer

Vastgesteld: server tex-gps; bestaande GPS-app gebruikt poort 8000; tex-revprox resolveert naar 192.168.178.151. Voorstel: SEM op LAN-adres 192.168.178.159:8001, uitsluitend bereikbaar voor de reverse proxy waar netwerkregels dat toelaten. Binding en IP worden bij installatie opnieuw gecontroleerd. HTTPS/certificaten eindigen op tex-revprox. Forwarded headers worden alleen van de vertrouwde proxy geaccepteerd. DNS en proxyconfiguratie zijn nog niet aangepast.

Eigen Django-authenticatie is de uitgangskeuze. Bestaande Authelia wordt niet automatisch hergebruikt; eventuele SSO is een latere expliciete keuze. SMTP is nodig voor uitnodigingen en wachtwoordherstel. Credentials gaan buiten Git; gevoelige API-tokens krijgen beschermde opslag. Database, Redis en uploadopslag worden niet publiek aangeboden.

Crawler: alleen toegestane doelen; limieten voor aantal URLs, diepte, verzoektempo, tijd en bestandsgrootte; robotsbeleid expliciet; blokkering van interne/reserved netwerken en onveilige redirects bij crawl- en sitemaprequests. Dit voorkomt dat door gebruikers aangeleverde URLs interne infrastructuur laten ophalen. Aparte wachtrijen en workerlimieten voorkomen dat crawls de bestaande GPS-app verdringen.

Monitoring omvat webgezondheid, wachtrijproblemen, taakfouten, schijfruimte en back-ups. Exacte capaciteit, bewaartermijnen, hersteldoelen en alarmering worden vóór productie bepaald.

## Open infrastructuurgegevens

| Gegeven | Status |
|---|---|
| Database-engine, host en poort | PostgreSQL/PostGIS; django.contrib.gis.db.backends.postgis; 192.168.178.152:5432 |
| PostgreSQL-versie, PostGIS-extensie, databasenaam en account | Nog vast te stellen |
| Database/account aanmaken en netwerktoegang | Nog uit te voeren na vaststellen infrastructuur |
| SMTP-server/provider, afzender en authenticatie | Op verzoek later te configureren; vereist vóór ingebruikname van e-mailuitnodigingen en wachtwoordherstel |
| Beheerstoegang/configuratie tex-revprox en DNS | Nog vast te stellen |
| Eerste platformbeheerder (e-mailadres) | Nog vast te stellen |
| GSC-property en account met toegang | Nodig bij API-integratie |
| Ahrefs-abonnement/API-rechten | Nodig voor latere connector |
| Opslagbudget, back-uplocatie en bewaartermijnen | Vóór productie vast te stellen |

Er worden geen wachtwoorden in deze specificatie opgenomen. De open gegevens blokkeren het vastleggen van het ontwerp niet, maar wel de betreffende aansluitingen.

## Acceptatie eerste versie

1. Twee gebruikers met verschillende projectlidmaatschappen kunnen elkaars data, bestanden, exports en taken niet benaderen.
2. De voorbeeldformaten importeren met correcte kolommen, aantallen en herkomst; dubbelen en lege/afgekorte exports zijn herkenbaar.
3. Upload en GSC-API leveren dezelfde batchstructuur met actor, importtijd en meetperiode.
4. Een batch verwijderen verwijdert uitsluitend die brongegevens; geen andere batch raakt beschadigd en verwerking kan niets terugplaatsen.
5. Wachtwoordherstel, 2FA en beheerrechten werken; geheimen verschijnen niet in logs of exports.
6. Een begrensde sitemapimport/crawl levert herleidbare URL-metingen en kan worden afgebroken.
7. Toegang via sem.lohr.nl en een herstelproef van database plus bestanden slagen vóór ingebruikname.

## Volgende stap

Stap 2: repository en Django/Docker-projectskelet maken op basis van deze specificatie. Externe database-, mail-, DNS- en proxywijzigingen zijn in stap 1 niet uitgevoerd.

## Voortgang database en skelet — 1 november 2026

Productie en test zijn door de gebruiker op tex-dbpg ingericht met PostgreSQL
16.15 en PostGIS 3.4. Schema sem heeft het bijbehorende migratieaccount als eigenaar.
Runtimeaccounts hebben geen schemacreatierechten; default tabel- en sequencerechten
zijn voor beide omgevingen gecontroleerd met teruggedraaide controletabellen.
HBA en UFW laten de SEM-accounts uitsluitend toe vanaf tex-gps voor hun eigen database.
Kruistoegang met appaccounts is in beide richtingen expliciet geweigerd.
TLS 1.3 met verify-full is gecontroleerd met certificaatnaam db-pg en vast hostaddr.
Publiek certificaat: ops/database/certs/db-pg.crt. Vier wachtwoorden blijven extern.
Back-ups/herstelproef staan nog open.

Het Django/Celery/Redis-skelet staat nu in /srv/sem, met eigen gebruikersmodel,
initiële migratie, Compose-configuratie, aparte secretbestanden en test als standaard.
Op gebruikersvoorkeur staan credentials in één .env naast manage.py (modus 0600,
buiten Git en Docker-buildcontext); per service worden uitsluitend eigen credentials doorgegeven. Er zijn nog geen applicatiemigraties uitgevoerd,
geen appservices gestart en geen proxy/DNS-wijzigingen aangebracht.

Projecten en lidmaatschappen zijn geïmplementeerd en op sem_test gemigreerd.
Zestien tests voor gezondheid en projectautorisatie slagen. Testservices zijn bijgewerkt.
Productie-appmigraties zijn nog niet uitgevoerd. Projectinterface is voorlopig JSON
met beheer via Django-admin; 2FA, e-mail en mutatieaudit volgen nog.

Bronnenfundament geïmplementeerd: ImportBatch, SourceRecord en minimaal
BatchDeletionEvent; projectgebonden overzicht, aanmaken van batchmetadata en
verwijderen door projectbeheerder. Server bepaalt actor en importtijd.
Batchverwerking controleert opnieuw toegang, is transactioneel en voorkomt
herpublicatie van een voltooide/verwijderde batch. 26 tests slagen.
Feitelijke uploadopslag, parsers, API-downloads en afgeleide analyses volgen nog.

Bestandsimports geïmplementeerd met preview, bronherkenning, privéopslag,
projectgebonden duplicaatdetectie, Celery-verwerking, geautoriseerde downloads,
recordpaginering en retry. Batchverwijdering heeft een duurzame bestandsopruimopdracht.
41 tests slagen; alle 18 bronvoorbeelden zijn gelezen en bekende bijzondere gevallen
zijn gecontroleerd. De volledige runtimeketen upload → Redis → Celery → database →
bestandsverwijdering is met een synthetisch bestand getest; tijdelijke testgegevens
zijn opgeruimd. Alleen sem_test is bijgewerkt; API-connectors en gebruikersinterface
volgen nog.
