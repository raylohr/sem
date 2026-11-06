# TrafficStack

Django-werkplek voor projecten, Google Search Console en URL-imports uit Screaming Frog en GSC.
GSC-API-resultaten blijven met oorspronkelijke brongegevens en meetmomenten bewaard.
Via Data → Import worden Screaming Frog Internal → All CSV-/XLS-/XLSX-exports en originele
GSC Page indexing CSV-ZIP-exports automatisch herkend.
Een voorbeeld toont domeinen, URL-aantallen, crawl-tijden en de huidige gebruiker.
Na bevestiging verschijnt een doorzoekbare URL-lijst met HTML- en resourcefilters.
Elke import bewaart alle oorspronkelijke kolommen als afzonderlijke projectsnapshot;
hetzelfde bestand wordt binnen een project niet dubbel geïmporteerd.
Tijden zonder tijdzone worden geïnterpreteerd als Europe/Amsterdam.
De GSC-import bewaart URL-voorbeelden, laatste crawl-datums, grafiek en metadata;
het gerapporteerde totaal en de rapportdatum blijven apart van het geïmporteerde aantal.
Bevestig bij GSC de rapportcategorie; de bestandsnaam is slechts een aanwijzing.

Projectinstellingen definiëren het hoofddomein en de toegestane hosts: standaard
het hoofddomein en www, optioneel ook overige subdomeinen. Nieuwe imports (SF,
GSC-bestanden en GSC-API) filteren afwijkende URL-rijen en registreren de aantallen.
Bij bevestiging van een bestandsimport worden de actuele regels opnieuw toegepast.
Bestaande voltooide imports worden niet achteraf gewijzigd.
Bij opslaan van een project moet het hoofddomein via DNS een A/AAAA-adres opleveren.
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
