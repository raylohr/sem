# Google Search Console — eerste API-connector

Status: geïmplementeerd, getest en in productie gekoppeld. De gebruiker heeft
bevestigd dat koppelen en ophalen werken. De gebruikershandleiding staat op
/workspace/handleiding/gsc/ en is ook vanuit projectkoppelingen te openen.
Google-instellingen kunnen per omgeving verschillen.

## Configureren via de website

1. Maak een Google Cloud-project en schakel de Search Console API in.
2. Configureer het OAuth-toestemmingsscherm en de toegestane gebruikers.
3. Maak een OAuth-client van type Web application.
4. Registreer exact deze redirect-URI:
   https://sem.lohr.nl/integrations/gsc/callback/
5. Meld als platformbeheerder aan met 2FA en open Integraties:
   https://sem.lohr.nl/workspace/settings/google/
   Het stappenplan staat op /workspace/settings/google/handleiding/.
   Vul de client-ID en het client secret in en sla op. De wijziging werkt direct
   in de webapp en workers; een containerherstart is niet nodig.
6. De INTEGRATION_TOKEN_KEY_TEST en INTEGRATION_TOKEN_KEY_PROD zijn gegenereerd.
   Bewaar ze veilig met de back-up; zonder juiste sleutel zijn refresh-tokens
   onleesbaar. Verander ze niet zonder een tokenmigratie of opnieuw koppelen.
7. Publiceer eerst de app via HTTPS/proxy en configureer vertrouwde forwarded headers.
   De sessiecookies zijn secure; interactieve OAuth vereist een werkende HTTPS-route.
8. Open een project → Koppelingen → Verbinden met Google en kies de property.

Test en productie hebben verschillende tokenversleutelingssleutels. De OAuth-clientconfiguratie wordt per omgeving in de eigen database opgeslagen.
Zonder opgeslagen configuratie blijven GSC_CLIENT_ID en GSC_CLIENT_SECRET uit .env
beschikbaar als fallback. Een expliciet uitgeschakelde databaseconfiguratie gebruikt
geen fallback. Voor gelijktijdig publieke test en productie moeten afzonderlijke
clients/callbacks worden ingericht.
Geen secrets of tokens delen in chat of Git. Alleen de read-only webmasters-scope
wordt aangevraagd, geen schrijfrechten op Search Console.

## Projectkoppeling

- GET /projects/<project-id>/connections/gsc/ toont configuratiestatus en verbindingen.
- POST .../gsc/connect/ met JSON {"property":"sc-domain:gmw.nl"} geeft de Google
  authorization_url. Ook URL-prefix-properties zoals https://www.gmw.nl/ zijn mogelijk.
- Projectbeheerder opent die URL, geeft toestemming, en komt terug op de callback.
- De callback controleert state, PKCE, sessiegebruiker, vervaltijd (10 minuten),
  projectbeheerrechten, verleende scope en toegang tot exact de gekozen property.
- Alleen het refresh-token wordt versleuteld opgeslagen; access-tokens blijven tijdelijk.
- POST .../gsc/<connection-id>/disconnect/ schakelt de verbinding uit en wist het
  lokale refresh-token. Bestaande batches blijven behouden. Dit trekt geen Google-
  toestemming voor andere projecten in. Globale intrekking kan in het Google-account.

Verbindingen zijn projectgebonden en deelbaar binnen projectrollen. Een bewerker
mag met een door de projectbeheerder gekoppelde bron data ophalen. De eigenaar
van de verbinding moet actief projectbeheerder blijven. De uitvoerende gebruiker
moet actief bewerker of beheerder zijn, ook bij verwerking en publicatie.

## API-import

POST /projects/<project-id>/connections/gsc/<connection-id>/import/:

```json
{"start":"2026-10-01","end":"2026-10-31"}
```

Een import beslaat 1–31 dagen en maakt altijd een eigen verwijderbare batch.
Die bewaart de actor, importtijd, meetperiode en een snapshot van property,
dimensies, zoektype en dataState. Celery vraagt per dag web-zoekprestaties op,
gegroepeerd naar datum en URL: klikken, vertoningen, CTR en positie.

Paginering: 25.000 rijen per pagina, tot 50.000 per dag; deze eerste versie begrenst
één batch op 100.000 rijen. Bij een grens verschijnt een waarschuwing. Ook onder
het maximum garandeert Google geen volledige dataset. Geen-resultaatdagen blijven
als ruwe lege API-response bewaard. Meetdatums blijven Google-datums in
America/Los_Angeles; importtijd wordt in UTC opgeslagen. Alleen definitieve data
wordt gevraagd (dataState=final), zodat zeer recente dagen kunnen ontbreken.

Elke oorspronkelijke API-responsepagina wordt bewaard met opvraagtijd en request,
naast de individuele SourceRecords met API-pagina- en rijverwijzing. Publicatie
gebeurt atomair. Bij fouten verschijnen geen halve resultaten; de batch is opnieuw
te starten via het bestaande retry-endpoint. Bij verwijdering verdwijnen zowel
records als API-payloads, maar blijft de verbinding beschikbaar voor nieuwe imports.

API-antwoorden gaan alleen naar vaste Google-endpoints met time-outs en omvangslimiet.
OAuth-code en state worden niet in de Gunicorn-accesslog opgenomen; de log gebruikt
alleen het URL-pad. Configureer ook tex-revprox zodat de callback-querystring niet
wordt gelogd. Tokenresponses en refresh-tokens worden niet getoond of gelogd.

De eerste connector biedt Search Analytics, geen volledige kopie van de GSC-
indexeringsrapporten. Coverage-/indexeringsexports blijven uploadimports.
De koppelinterface laat de beheerder eerst bij Google aanmelden en daarna een
beschikbare property kiezen. De keuzelijst wordt server-side bewaard; tokens komen
niet in HTML of sessiedata terecht. De oudere API-flow met vooraf ingevulde property
blijft ondersteund.

## Dagelijkse import en selectieve inspecties

Na propertyselectie staat dagelijkse import aan. De beheerder kan deze in de
koppelingenpagina uit- of inschakelen. Draai precies één Celery beat per omgeving;
Compose bevat hiervoor de service `beat` en het productie-deployscript start deze.
Beat controleert ieder uur. Per verbinding en Google-datum ontstaat maximaal één
geplande batch, afgedwongen met een database-lock en last_scheduled_date. De batch
bevat zeven dagen tot en met drie dagen geleden, zodat later definitief geworden
gegevens worden meegenomen. Overlappende batches zijn afzonderlijke bronmetingen;
tel deze niet bij elkaar op als unieke dagprestaties. Bestaande koppelingen houden
planning uit totdat een beheerder deze inschakelt.

Bij brokeruitval blijft de batch pending; volgende ticks bieden pending/failed
geplande batches opnieuw aan. Een ingetrokken eigenaar, ontkoppeling of uitgeschakelde
planning verhindert nieuwe planning. Workers controleren de actuele projectrechten.

Bewerkers kunnen op de koppelingenpagina 1–20 URL’s uit het project selecteren door
ze in te plakken, één per regel. Alleen URL’s binnen de gekoppelde property worden
aanvaard. URL Inspection draait in de bestaande worker en publiceert atomair een
eigen verwijderbare bronbatch met de ruwe antwoorden en opvraagtijd. Het URL-dossier
toont Google-indexstatus, indexeringsreden, robots-/indexeringsregels, fetchstatus,
laatste crawl en beide canonicals. Opvraagtijd is het meetmoment van de snapshot;
laatste crawl is een afzonderlijk veld. Dit is geen live test of indexeringsverzoek.
API-limietfouten blijven zichtbaar als mislukte batch; handmatig opnieuw proberen
kan via het bestaande retry-endpoint.

Aanvullende endpoints (form POST, CSRF vereist):
- `gsc/<connection-id>/property/`: `property`, uitsluitend de initiërende beheerder.
- `gsc/<connection-id>/schedule/`: `enabled=1` of `0`, projectbeheerder.
- `gsc/<connection-id>/inspect/`: `urls`, bewerker of beheerder.

Eigen OAuth-clients en serviceaccount-JSON zijn nog geen gebruikersopties. De eerste
versie gebruikt de centraal geconfigureerde OAuth-client met individuele toestemming.

## Officiële documentatie

- https://developers.google.com/identity/protocols/oauth2/web-server
- https://developers.google.com/webmaster-tools/v1/searchanalytics/query
- https://developers.google.com/webmaster-tools/v1/how-tos/all-your-data


## Platformbeheerpagina’s

Alleen een actieve platformbeheerder met geverifieerde 2FA kan de instellingen
of het stappenplan openen. POST vereist CSRF. Pagina’s hebben no-cache/no-store.
Het client secret staat versleuteld in GSCOAuthSettings en wordt nooit als
formulierwaarde teruggestuurd, ook niet na validatiefouten. Een leeg secretveld
behoudt het bestaande geheim. Configuratiewijzigingen worden zonder secrets in
het beveiligingslog vastgelegd.

Een andere client-ID vereist een nieuw secret. Bij bestaande accountkoppelingen
moet de beheerder expliciet het ontkoppelen bevestigen. Hierbij worden lokale
refresh-tokens gewist en planningen uitgezet; bronbatches blijven bewaard.
Het wijzigen van alleen het secret behoudt de bestaande koppelingen.
Een tijdens OAuth gewijzigde configuratie blokkeert de oude callback; start dan
opnieuw. Opslaan valideert de invoer, niet de geldigheid van Google-credentials;
de interactieve projectkoppeling controleert de werkelijke toegang.
