# Stap 4 — analyse van pagina’s en relaties

De basisanalyse is uitgebreid met module `page-relations-v1`, basisregels versie 3,
meetextractie `assessment-v3` en HTML-extractie `html-v4`. Elke afgeronde webcrawl
en browserbatch krijgt deze controles automatisch. Onder **Bevindingen** kunnen
bestaande crawl- en browserbatches opnieuw worden geanalyseerd met opgeslagen HTML
of gerenderde DOM. Er worden tijdens heranalyse geen netwerkrequests gedaan.
Historische analyses en oorspronkelijke bronrecords blijven behouden.

## Controles

| Bereik | Uitvoering |
|---|---|
| Pagina | HTTP-foutstatus, gemeten redirectketen/lus, ontbrekende/meervoudige/ongeldige canonical, tegengestelde Googlebot-instructies, geblokkeerde noindex |
| Inhoud | Bestaande metadata-/H1-controles, HTML-taal, sprongen in headingniveaus |
| Assets | Ontbrekend alt-attribuut; leeg alt is toegestaan. HTTP-resources op HTTPS, uitsluitend uit verzamelde resource-URL’s |
| Structured data | Begrensde JSON-LD-syntaxiscontrole met Python stdlib JSON; geen volledige Schema.org- of rich-resultvalidatie |
| Beveiliging | Afwezigheid van CSP, HSTS op HTTPS en X-Content-Type-Options als configuratiesignaal, uitsluitend bij waargenomen headers |
| Canonicalrelaties | Gemeten doelstatus/redirect/noindex, canonicalketens en lussen, maximaal tien stappen |
| Hreflang | ISO 639-1-taal, optionele structurele scriptcode en ISO 3166-1-regio of x-default, conflicterende doelen per code, eigen verwijzing, gemeten doelstatus/canonical en terugverwijzing |
| Linkrelaties | Bestaande 404-controles plus interne links naar serverfouten, redirects en niet-indexeerbare/alternatieve pagina’s |
| Duplicaten | Bestaande exacte tekst-/metadata-overeenkomsten, aangevuld met maximaal vijf voorbeeldpagina’s als doelbewijs |
| Structuur | Unieke interne inlinks/outlinks, linkafstand vanaf de start-URL en kandidaten zonder waargenomen interne verwijzingen |

Een ontbrekende canonical, herhaalde metadata, link naar een uitgesloten pagina,
headingniveau of ontbrekende beveiligingsheader vraagt beoordeling. Het is niet
vanzelf een defect, rankingverlies, beveiligingslek of overtreding.

## Dekking en bewijs

Uitkomsten blijven `issue`, `pass`, `unknown` en `not_applicable`. De analysepagina
laat aantallen per controle zien, naast alle bevindingen. Onder **Paginaresultaten**
kan men per URL naar de uitgevoerde controles, interne ondersteuning en bewijs.
Uitkomsten zijn filterbaar en resultaten zijn afzonderlijk gepagineerd. De bestaande
JSONL-export neemt controles, structuurmetingen, doelen en bewijsverwijzingen mee.
Lezers kunnen bekijken en exporteren; mutaties blijven voor projectbewerkers.

Relatiecontroles gebruiken uitsluitend doelen in dezelfde batch en hetzelfde
consentscenario. Andere crawls of actuele netwerkresponses worden niet als bewijs
bijgemengd. Niet-gemeten canonical-/hreflangdoelen blijven onbekend. Bevindingen
bewaren bron- en waar beschikbaar doelrecord, oorspronkelijke meetdatum, URL’s,
waargenomen statussen, instructies en HTTP-/browserbewijs.

Bij herextractie van oude documenten zijn eerder niet bewaarde headers onbekend.
Het opnieuw lezen van HTML bewijst geen ontbrekende header. Nieuwe scans bewaren
alleen een expliciete headerallowlist, zonder Set-Cookie of Authorization. Robots-
instructies voor andere bots worden niet als Googlebot-noindex geïnterpreteerd.
HTTP Link-canonicals worden naast HTML-canonicals bewaard.

De interne graaf gebruikt unieke URL-paren, inclusief nofollow-links, exclusief
zelflinks en niet-gemeten externe doelen. Redirects tellen niet als extra linkedges.
De graaf is per scenario gescheiden. De gemeten crawl-diepte blijft afzonderlijk
van de nieuw berekende linkafstand. Zonder bekende start-URL is afstand onbekend.
Een kandidaat zonder inlinks wordt alleen gemeld bij een afgeronde crawl met
volledig bekende, niet-afgekapte linkinventaris en een bekende start-URL. Het is
geen bewezen orphan buiten deze dataset; daarvoor zijn bijvoorbeeld sitemap-,
GSC- of klantlijsten nodig.

## Grenzen en prestaties

- Geen volledige toolpariteit, bijna-duplicaatvergelijking, Link Score of
  Google-indexeringsbewijs. Scores en vrije AI-analyses vallen buiten deze module.
- Hreflang betreft HTML en bewaarde HTTP Link-headers. Sitemap-annotaties worden
  nog niet geëxtraheerd. Scriptcodes worden structureel beoordeeld; er is geen
  volledige IANA-registervalidatie. Relatieve verwijzingen worden tegen hun basis
  opgelost vóór de URL-controle.
- JSON-LD: maximaal twintig blokken, 65.536 tekens per blok. Afkapping en onvolledige
  scripts blijven onbekend. Externe contexten worden niet opgehaald.
- Afbeeldingen/resources: maximaal 200 inventarisitems per pagina; headings 200,
  hreflang 100. Afwezigheid van problemen bij afkapping geeft geen pass.
- Graaf: maximaal 100.000 URL/scenario-nodes en 1.000.000 geobserveerde unieke
  linkrijen. Bij budgetoverschrijding blijven structuurcontroles onbekend.
- Verwerking in chunks van honderd pagina’s, gebundelde doel-lookups en beperkte
  bewijssamples. Elke regel bewaart maximaal twintig voorbeelden en vijf doelen
  per pagina. Het aantal bevindingen telt getroffen pagina’s per nieuwe regel,
  terwijl bestaande 404-bevindingen afzonderlijke linkvoorkomens blijven tellen.
- Alle verwerking blijft transactioneel. Mislukte heranalyse bewaart het vorige
  gepubliceerde resultaat. Verwijdering van een batch ruimt afgeleide data op.

## Referenties

- [Google: canonicalisatie](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls)
- [Google: taalversies en terugverwijzingen](https://developers.google.com/search/docs/specialty/international/localized-versions)
- [Google: robots-instructies](https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag)
- [Google: noindex](https://developers.google.com/search/docs/crawling-indexing/block-indexing)

## Uitgevoerde schaalcontrole

De synthetische controle op sem_test verwerkte 10.000 pagina’s en 100.000 links
met de volledige analysemodule in 157,82 seconden, met 131,6 MiB piek-RSS en
2.557 queries. De gegevens zijn teruggedraaid en er zijn geen externe requests
gedaan. Dit is een begrensde testmeting op gedeelde infrastructuur, geen SLA of
bewijs voor datasets van 100.000 pagina’s. De bestaande 404- en dekkingslookups
gebruiken nu eveneens een begrensde statusindex en setverwerking. Deze module
voert aanzienlijk meer controles uit dan de oorspronkelijke basisanalyse.

De definitieve volledige suite slaagt met **353 tests**. De live controle op
sem_test doorloopt queue, native geïsoleerde browser, drie analyses, DNS-verrijking,
UI en bewijsdownloads; tijdelijke gebruikers, projecten en batches zijn opgeruimd.
Er zijn geen nieuwe databasemigraties nodig. De analysemodule is op test geactiveerd.

De eerder goedgekeurde correctiecommit `c377edb` staat afzonderlijk op productie.
Verhuizer.eu is daarmee hergeanalyseerd uit opgeslagen HTML en opnieuw met twintig
browserpagina’s onderzocht; historische batches blijven intact. Deze stap-4-module
is geen onderdeel van die productiecommit.
