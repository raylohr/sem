# Search Visibility Toolkit

Django-werkplek voor projecten, bronimports en Google Search Console.
Ondersteunt imports uit Search Console, Ahrefs, Screaming Frog en contentaudits,
met een URL-overzicht dat oorspronkelijke brongegevens en meetmomenten bewaart.

De ingebouwde websitecrawler, sitemapscanner, browseronderzoeken en hun afgeleide
analyses, technologie-/privacyverrijking en onderzoeksdossiers zijn verwijderd.
De migraties verwijderen bijbehorende scanbatches en databasetabellen.
Historische migraties blijven beschikbaar voor bestaande installaties.

Start de testomgeving met `docker compose up -d --build`.
Voer databasemigraties uit met `docker compose run --rm migrate`.
