# TrafficStack

Django-werkplek voor projecten, Google Search Console en Screaming Frog URL-imports.
GSC-API-resultaten blijven met oorspronkelijke brongegevens en meetmomenten bewaard.
Via Data → Import worden Screaming Frog Internal → All CSV-exports automatisch herkend.
Een voorbeeld toont domeinen, URL-aantallen, crawl-tijden en de huidige gebruiker.
Na bevestiging verschijnt een doorzoekbare URL-lijst met HTML- en resourcefilters.
Elke import bewaart alle oorspronkelijke kolommen als afzonderlijke projectsnapshot;
hetzelfde bestand wordt binnen een project niet dubbel geïmporteerd.
Tijden zonder tijdzone worden geïnterpreteerd als Europe/Amsterdam.
Andere bestandsformaten en algemene spreadsheetimports worden niet ondersteund.

De ingebouwde websitecrawler, sitemapscanner, browseronderzoeken en hun afgeleide
analyses, technologie-/privacyverrijking en onderzoeksdossiers zijn verwijderd.
De migraties verwijderen bijbehorende scanbatches en databasetabellen.
De actieve applicatieapps zijn accounts, core, projects, sources en integrations.
Historische migraties staan apart in `config/retired_apps`, zonder modellen of
functionaliteit. Expliciete oorspronkelijke app-labels houden de migratiegeschiedenis intact.

Er is één vaste productieomgeving. Test wijzigingen met `./ops/test.sh` en rol
ze na commit en push uit met `./ops/deploy-production.sh`.
Zie [deployment](docs/06-productie-deployment.md) voor de werkwijze.
