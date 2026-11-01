# Google Search Console — eerste API-connector

Status: geïmplementeerd en getest met nagebootste antwoorden. Geen Google-account
gekoppeld en geen live Google-data opgehaald. Configuratie volgt later op verzoek.

## Later configureren

1. Maak een Google Cloud-project en schakel de Search Console API in.
2. Configureer het OAuth-toestemmingsscherm en de toegestane gebruikers.
3. Maak een OAuth-client van type Web application.
4. Registreer exact deze redirect-URI:
   https://sem.lohr.nl/integrations/gsc/callback/
5. Vul in /srv/sem/.env GSC_CLIENT_ID en GSC_CLIENT_SECRET in.
6. De INTEGRATION_TOKEN_KEY_TEST en INTEGRATION_TOKEN_KEY_PROD zijn gegenereerd.
   Bewaar ze veilig met de back-up; zonder juiste sleutel zijn refresh-tokens
   onleesbaar. Verander ze niet zonder een tokenmigratie of opnieuw koppelen.
7. Publiceer eerst de app via HTTPS/proxy en configureer vertrouwde forwarded headers.
   De sessiecookies zijn secure; interactieve OAuth vereist een werkende HTTPS-route.
8. Hermaak de relevante containers na configuratiewijzigingen.

Test en productie hebben verschillende tokenversleutelingssleutels. Dezelfde
OAuth-clientconfiguratie wordt voorlopig uit .env ingelezen; voor gelijktijdig
publieke test en productie moeten afzonderlijke clients/callbacks worden ingericht.
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
Er is nog geen automatische periodieke API-ophaalplanning of volledige koppelinterface.

## Officiële documentatie

- https://developers.google.com/identity/protocols/oauth2/web-server
- https://developers.google.com/webmaster-tools/v1/searchanalytics/query
- https://developers.google.com/webmaster-tools/v1/how-tos/all-your-data
