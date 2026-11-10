# Playwright-crawler — stap 1

De nieuwe Playwright-bron heeft een configuratiescherm onder **Data → Koppelingen
→ Playwright → Crawlinstellingen**. In deze fase worden uitsluitend instellingen
opgeslagen. Er is geen browserafhankelijkheid, netwerkuitvoering, Celery-crawlqueue,
HTML-opslag of periodieke planning toegevoegd. De bron staat daarom zichtbaar op
**In voorbereiding**. Er zijn geen screenshots voorzien.

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
