# Playwright-crawler — configuratie en uitvoering

De Playwright-bron heeft een configuratiescherm onder **Data → Koppelingen
→ Playwright → Crawlinstellingen** en een aparte Celery-uitvoeringsengine.
Configuratie en uitvoering (stappen 1 en 2) zijn beschikbaar. HTML-archivering,
startbediening in de UI en periodieke planning volgen in stappen 3 en 4.
Er worden geen screenshots gemaakt. De beschrijving van stap 1 hieronder legt
ook vast welke onderdelen destijds uitsluitend werden voorbereid.

## Instellingen

- Hoofddomein: afkomstig uit het bestaande project.
- Subdomeinen: standaard aangevinkt bij een nieuwe crawlerconfiguratie. De keuze
  wordt bij expliciet opslaan op `Project.include_subdomains` vastgelegd. De UI
  vermeldt dat dit ook de hostgrenzen van andere projectbronnen bepaalt. Alleen
  een formulier bekijken verandert deze projectinstelling niet. Bestaande
  configuraties tonen altijd de actuele projectinstelling.
- Vertrekpunt: bestaande URL-inventaris en projectwebsite, met optionele extra
  start-URL’s (maximaal 1.000).
- Sitemaps: standaard aan, met maximaal 100 extra sitemap-URL’s. De uitvoering
  gaat in stap 2 ook sitemapverwijzingen uit robots.txt en sitemapindexen lezen.
- Nieuwe interne URL’s ontdekken: standaard aan.
- Robots.txt-crawlregels respecteren: standaard uit. Dit staat los van het
  lezen van sitemapverwijzingen.
- Uitsluitingen: hosts inclusief onderliggende subdomeinen, en padprefixen die
  gelden op alle toegestane hosts. `/beheer` omvat `/beheer` en `/beheer/...`,
  niet `/beheerder`. De invoer bevat geen reguliere expressies of wildcards.
- Desktopprofiel: willekeurig per crawl of een vast profiel uit een onderhouden
  pool van tien desktopprofielen. Dit is geen statistische marktaandeel-top-10.
  Concrete user-agentversies worden in stap 2 aan de browserruntime gekoppeld;
  stap 1 bewaart alleen de profielkeuze.
- Standaard 1 paginastart per seconde en 1 parallelle agent. Maximaal 20 agents,
  een gezamenlijke ingestelde snelheid tussen 0,05 en 20 paginastarts/seconde.
- Voorbereide vertraging: standaard boven 10 seconden responstijd terug naar
  0,25 paginastarts/seconde; beide waarden zijn instelbaar. Het adaptieve gedrag
  wordt in stap 2 uitgevoerd.

Externe links krijgen later uitsluitend bereikbaarheidscontroles. Benodigde
externe resources mogen voor rendering worden geladen. Geen van beide wordt
in stap 1 opgehaald.

## Validatie en toegang

Alleen projectbeheerders kunnen de configuratie wijzigen; bewerkers en lezers
kunnen haar bekijken. Opslaan controleert de actuele account- en projectrechten
opnieuw en schrijft een auditgebeurtenis. Er is maximaal één configuratie per
project. Bij projectverwijdering wordt deze mee verwijderd.

Start- en sitemap-URL’s moeten HTTP(S)-adressen binnen de gekozen projecthosts
zijn, zonder inloggegevens of afwijkende poorten. Fragmenten worden verwijderd;
functionele queryparameters blijven behouden. Externe hosts en URL’s die onder
uitsluitingen vallen worden geweigerd. Numerieke grenzen en de verlaagde snelheid
worden zowel op formulier- als databaseniveau gecontroleerd.

Deze configuratievalidatie vervangt de netwerkbeveiliging van stap 2 niet: de
uitvoering moet iedere navigatie, redirect en resourcebestemming opnieuw toetsen,
inclusief DNS/IP-grenzen en het gedeelde snelheidsbudget.

De bronregistratie gebruikt `source=playwright` en `method=crawl`. De oude,
verwijderde crawlerapps worden niet teruggezet. De uitvoerings-, opslag- en
bedieningsstappen volgen afzonderlijk volgens het overeengekomen plan.

# Stap 2 — uitvoering

De engine is toegevoegd; starten vanuit het projectscherm, HTML-archivering en
herhaalplanning volgen in stappen 3 en 4. Er worden geen screenshots gemaakt.

## Uitvoeringsarchitectuur

- `crawl-worker`: aparte Celery-queue `crawler`, met autoscaling van 1 tot maximaal
  20 processen en prefetch 1. Normale imports blijven in de bestaande queue.
- `crawl-browser`: apart browserimage met exact gepinde Playwright/browserversies.
  Geen database-, Google- of SE Ranking-credentials. Alleen een intern netwerk,
  alleen-lezen rootfilesystem en tijdelijke browser-home en profielen.
- `crawl-egress`: enige HTTP(S)-uitgang voor de browser. Alleen standaardpoorten,
  publieke DNS-adressen en publieke IP’s. DNS wordt voor de verbinding gevalideerd
  en het gekozen IP wordt rechtstreeks verbonden. Ook redirects en resources
  moeten via deze proxy. Services zijn niet op de host gepubliceerd.

De browser-RPC heeft een aparte willekeurige `CRAWLER_RENDER_TOKEN`. Deze staat in
`.env` en uitsluitend bij crawl-worker en browser-service. De proxy krijgt geen
productiecredentials. Playwright-versie staat identiek gepind in `requirements.txt`
en `ops/browser-requirements.txt`.

## Queue en begrenzing

Een project kan maximaal één actieve crawl hebben. Elke crawl bewaart zijn
instellingensnapshot en één desktopprofiel. De user-agentversie volgt de geïnstalleerde
browserruntime; alle pagina’s in de run gebruiken dezelfde profielkeuze.

Startpunten komen uit projectwebsite, extra start-URL’s, gepubliceerde bron-URL’s en
sitemaps (ook sitemapindexen en gzip). Links worden ontdubbeld, binnen de actuele
projectgrenzen gehouden en begrensd op diepte, aantallen en looptijd. Een gewijzigde
projectgrens of ingetrokken toegang stopt de oude run. Standaardlimieten: 10.000
pagina’s, diepte 10, 720 minuten, 30 seconden per rendering, 2 seconden extra
rendertijd en 2 retries. De grenzen zijn configureerbaar. Er worden maximaal 100
sitemapbestanden en 1.000 unieke externe links per run verwerkt.

Celery-agents delen een databasebudget voor het hoofddomein. De browser-service
spreidt de werkelijke navigatiestarts aanvullend na browseropstart. Standaard is
1 paginastart/seconde en 1 agent. Een trage eerste respons, timeout, 429 of tijdelijke
serverfout verlaagt snelheid en paralleliteit. Retry-After wordt begrensd gevolgd.
Na een cooldown en gezonde waarnemingen herstellen snelheid en paralleliteit
geleidelijk, tot de ingestelde maxima.

De limiet gaat over paginastarts. Onderliggende resources hebben daarnaast een
begrensd aantal requests, grootte- en tijdslimieten. Media, downloads, pop-ups en
websockets worden geblokkeerd. Scripts, stylesheets en andere noodzakelijke
publieke resources kunnen worden geladen. Formulieren worden niet bediend.

Externe links worden uitsluitend met HEAD gecontroleerd; als HEAD niet wordt
ondersteund volgt GET zonder de responsebody te lezen. Iedere redirect wordt
opnieuw gecontroleerd. Per externe host geldt een gedeeld budget van 1 request/sec.
403 of een timeout wordt als onbekend/geblokkeerd onderscheiden van een echte 404.

## Herstel en resultaat

Run- en URL-statussen, leases en uitvoeringstokens staan in PostgreSQL. De bestaande
worker controleert elke 15 seconden onderbroken/aangevraagde crawls; hij maakt
geen nieuwe periodieke crawls aan. Oude taakberichten kunnen een nieuwe poging
niet overschrijven. Retries en totale looptijd zijn begrensd. Een bereikte limiet,
aanhoudende fout of ontbrekende sitemapdekking levert een onvolledige status op.

De renderer leest server-HTML, DOM na JavaScript, tekst, redirects, links en timing.
In stap 2 worden hiervan uitsluitend uitvoeringsmetadata, checksums en linkdoelen
bewaard. De tijdelijke HTML/tekst wordt nog niet als bronbestand gearchiveerd en
niet in de URL-inventaris gepubliceerd; dit is het onderwerp van stap 3.

## Beheerstartpunt

Na het opslaan van de projectconfiguratie kan een beheerder via de server een
begrensde proefcrawl starten:

```sh
python manage.py crawl_playwright --project PROJECT_UUID --actor USER_ID --max-pages 10 --max-depth 1 --max-minutes 5
```

De actor moet actuele projectbeheerrechten hebben. De limietoverrides gelden alleen
voor die run en wijzigen de opgeslagen projectconfiguratie niet.

De releaseprocedure voert naast de geïsoleerde Django-tests echte browserproeven
voor Chromium, Firefox en WebKit uit op een lokale fixture in een netwerkloze
container. Een tijdelijke DNS-mock bestaat uitsluitend in die testprocedure;
productie heeft geen instelling om private netwerkbestemmingen toe te staan.

De release test daarnaast bearer-authenticatie, een harde procesdeadline bij
vastlopend JavaScript en de volledige Celeryketen met een geïsoleerde database,
sitemapindex, JavaScript-link, subdomein en externe HEAD-controle. De renderer
heeft bovenop browsertimeouts een harde procesdeadline en ruimt zijn browserboom op.
