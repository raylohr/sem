# Search Visibility Toolkit

Django-werkplek voor projecten, bronimports en Google Search Console.
Ondersteunt imports uit Search Console, Ahrefs, Screaming Frog en contentaudits,
met een URL-overzicht dat oorspronkelijke brongegevens en meetmomenten bewaart.

De ingebouwde websitecrawler, sitemapscanner, browseronderzoeken en hun afgeleide
analyses, technologie-/privacyverrijking en onderzoeksdossiers zijn verwijderd.
De migraties verwijderen bijbehorende scanbatches en databasetabellen.
De actieve applicatieapps zijn accounts, core, projects, sources, catalog en integrations.
Historische migraties staan apart in `config/retired_apps`, zonder modellen of
functionaliteit. Expliciete oorspronkelijke app-labels houden de migratiegeschiedenis intact.

Start de testomgeving met `docker compose up -d --build`.
Voer databasemigraties uit met `docker compose run --rm migrate`.
