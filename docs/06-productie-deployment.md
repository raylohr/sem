# Dev/test en productie

Dev/test gebruikt project sem-test, database sem_test, eigen volumes en localhost
8001. Productie gebruikt sem-prod, sem_prod, eigen Redis/media-volumes en een
onveranderlijke applicatie- en gatewayimage per Git-commit. Geen broncodemounts.
Eén .env naast manage.py levert gescheiden credentials en sleutels. Beide omgevingen
staan op tex-gps; CPU, schijf en hostbeheer blijven gedeeld.

## Release

Bouw en test wijzigingen in dev/test. Commit en push daarna naar GitHub. Voer vanuit
een schone checkout van de gewenste gepushte commit uit:

```sh
cd /srv/sem
./ops/deploy-production.sh
```

Het script haalt origin/main op, controleert dat de commit daarop staat, bouwt images
uit git archive (zonder .env), voert productiemigraties uit met de migratierol en
start web, worker, scan-worker en gateway. De productiecontainers gebruiken uitsluitend
images met de volledige releasecommit als tag; een dev-build vervangt die niet.
Geen automatische deploy bij een push. Controleer voor latere releases eerst een
recente database- en media-back-up; migraties worden niet automatisch teruggedraaid.

Een specifieke commit deployen kan na checkout van die commit met
`./ops/deploy-production.sh COMMIT`. Een oudere release is alleen veilig als het
huidige databaseschema daarvoor geschikt is.

Voor beheer van de huidige productie-images, zonder nieuwe build:

```sh
export SEM_PROD_IMAGE=$(docker inspect sem-prod-web-1 --format '{{.Config.Image}}')
export SEM_PROD_GATEWAY_IMAGE=$(docker inspect sem-prod-gateway-1 --format '{{.Config.Image}}')
docker compose -f compose.yaml -f compose.prod.yaml ps
```

Eerste beheerder interactief aanmaken (wachtwoord niet in commando of chat):

```sh
docker compose -f compose.yaml -f compose.prod.yaml --profile tools run --rm migrate python manage.py createsuperuser
```

Bij de eerste login moet de beheerder een authenticator koppelen. Gebruik de
productie-beheercommando's nooit voor databasetests.

## Reverse proxy

tex-revprox (192.168.178.151) beëindigt TLS voor sem.lohr.nl en proxyt naar
192.168.178.159:8002. De gateway op die LAN-poort laat alleen het proxy-IP toe en
overschrijft forwarded headers. Dit vermijdt afhankelijkheid van UFW voor
Docker-gepubliceerde poorten. Django vertrouwt HTTPS-forwarding alleen in productie.
De aparte localhost-binding 127.0.0.1:8002 blijft beschikbaar voor hostbeheer.

Installeer ops/proxy/sem.lohr.nl.conf op tex-revprox als
/etc/nginx/sites-available/sem.lohr.nl, test met nginx -t en reload nginx.
Het certificaat moet al bestaan. /var/www/letsencrypt blijft het webroot voor
HTTP-01-vernieuwing. Stel een Certbot deploy-hook in die nginx na vernieuwing reloadt.
Er staan geen andere websites of Authelia-regels in deze configuratie. Django
verzorgt de login en 2FA. Proxy-accesslogs zijn uit om OAuth-querystrings en
wachtwoordhersteltokens niet op te slaan; Gunicorn logt geredigeerde verzoeken.

## Controles

- localhost:8002/ready/ geeft ready; localhost:8001 blijft dev/test.
- Een LAN-verzoek naar 192.168.178.159:8002 van buiten tex-revprox krijgt 403.
- Vanaf tex-revprox moet /ready/ bereikbaar zijn met Host sem.lohr.nl.
- HTTPS sem.lohr.nl toont de login, met geldige certificaatketen.
- Login, 2FA, CSRF-formulieren en password reset controleren na proxykoppeling.
- Certbot renew --dry-run controleert vernieuwing (alle geconfigureerde certificaten).

Publieke bereikbaarheid en certificaatvernieuwing worden pas als voltooid beschouwd
nadat de Nginx-configuratie op tex-revprox is toegepast en gecontroleerd.
