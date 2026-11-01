# Gebruikers, projecttoegang en accountbeveiliging

Platformbeheerders beheren accounts via `/workspace/users/`: aanmaken, wijzigen,
deactiveren, tijdelijk wachtwoord instellen en na identiteitscontrole 2FA herstellen.
Nieuwe accounts en tijdelijke wachtwoorden vereisen een eigen wachtwoord bij de
volgende login. E-mailadressen zijn uniek zonder onderscheid tussen hoofdletters.
Accounts worden gedeactiveerd om bronhistorie en gebruikersverwijzingen te behouden.

De projecttab **Toegang** koppelt bestaande actieve gebruikers op e-mailadres met
rol admin, editor of viewer. Alleen projectbeheerders en platformbeheerders kunnen
dit wijzigen. De laatste actieve projectbeheerder kan niet worden verwijderd,
verlaagd of gedeactiveerd. Gewone staffstatus geeft geen platformtoegang.
Django-admin is voor gebruikers en lidmaatschappen alleen-lezen.

## Authenticator en herstel

Platformbeheerders gebruiken verplicht 2FA. Voor andere accounts is dit door de
platformbeheerder instelbaar; gebruikers kunnen ook zelf 2FA activeren. Zodra een
authenticator gekoppeld is, vereist iedere login wachtwoord én code, ook voor
JSON-routes en downloads. Codes gebruiken TOTP via django-two-factor-auth/django-otp.

Onder **Accountbeveiliging** kun je een authenticator koppelen en tien eenmalige
herstelcodes genereren. Bewaar deze buiten de app. Nieuwe herstelcodes vervangen de
oude. Gevoelige handelingen vereisen opnieuw het wachtwoord als de vorige
wachtwoordbevestiging langer dan vijf minuten geleden was.

Bij verlies van de telefoon: log in met wachtwoord en een herstelcode, kies
**Authenticator vervangen** en koppel direct een nieuwe authenticator. Oude codes,
authenticator en andere sessies worden ingetrokken. Verplichte 2FA kan niet worden
uitgeschakeld. Zonder herstelcode kan een andere platformbeheerder na
identiteitscontrole een reset uitvoeren, met eigen wachtwoord en verplichte reden.

Als de enige platformbeheerder is buitengesloten, gebruik de vertrouwde serverconsole:

```sh
cd /srv/sem
docker compose --profile tools run --rm migrate python manage.py reset_two_factor --username NAAM --reason 'Identiteit gecontroleerd; authenticator verloren'
```

Het commando vraagt om bevestiging, trekt bestaande sessies en herstelcodes in en
verplicht opnieuw koppelen bij de volgende login. Het verandert het wachtwoord niet.
Beperk OS- en databasebeheer tot vertrouwde beheerders. TOTP-sleutels en herstelcodes
staan in de OTP-databasetabellen; bescherm databaseback-ups overeenkomstig.

## Wachtwoordherstel en SMTP

SMTP staat standaard uit. Het herstelformulier meldt dit expliciet en verstuurt
geen mail. Een beheerder kan ondertussen een tijdelijk wachtwoord instellen.
Configureer later in `.env` naast `manage.py`:

```dotenv
ACCOUNT_EMAIL_ENABLED='1'
EMAIL_HOST='smtp.voorbeeld.nl'
EMAIL_PORT='587'
EMAIL_HOST_USER='gebruikersnaam'
EMAIL_HOST_PASSWORD='wachtwoord'
EMAIL_USE_TLS='1'
EMAIL_USE_SSL='0'
DEFAULT_FROM_EMAIL='SEM <sem@lohr.nl>'
PUBLIC_BASE_URL='https://sem.lohr.nl'
```

Gebruik de waarden van de mailprovider en hermaak daarna de webcontainer. Stel nooit
TLS en SSL tegelijk in. Het vaste PUBLIC_BASE_URL bepaalt de host in herstelmails.
Herstellinks verlopen na een uur, zijn eenmalig en omzeilen 2FA niet. Onbekende
adressen krijgen dezelfde bevestiging. SMTP is alleen beschikbaar in de webservice.

## Sessies, logging en controles

Wachtwoordwijzigingen, beveiligingsbeleid en herstelacties trekken oude sessies in.
Sessies verlopen na acht uur en bij het sluiten van de browser. Login- en
herstelpogingen worden begrensd met Redis; OTP heeft bovendien throttling en
hergebruikbescherming. De toepassing vertrouwt geen willekeurige forwarded-IP-header.

Het beveiligingslog onder Gebruikers toont account- en rolwijzigingen en
herstelacties zonder wachtwoorden of codes. Gevoelige pagina's hebben no-store en
no-referrer. Gunicorn verwijdert querystrings en wachtwoordhersteltokens uit
accesslogs. Configureer bij de toekomstige reverse proxy dezelfde redactie: geen
OAuth-querystrings of `/accounts/reset/`-tokens in proxylogs bewaren.

De testomgeving blijft op `127.0.0.1:8001`. Productie, reverse proxy, publiek HTTPS,
SMTP en Google OAuth moeten nog apart worden ingericht. Testdata voor controles
wordt niet als permanente gebruiker aangemaakt.
