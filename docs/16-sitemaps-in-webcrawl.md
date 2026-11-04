# Sitemaps als standaard bron voor webcrawls

Nieuwe webcrawls hebben `discover_sitemaps=true`. De start-URL blijft behouden.
Na de eerste paginapoging vult de crawler zijn bestaande wachtrij aan vanuit
sitemaps. Informatie uit gewone interne links wordt daarnaast gewoon verwerkt.
Een losse sitemapimport blijft beschikbaar en haalt geen webpagina's op.

Ontdekking gebruikt `Sitemap:`-regels uit robots.txt op het uiteindelijke
startadres, waar mogelijk uit de bestaande robotscache. Als er geen toegestane
verwijzingen zijn, worden `/sitemap.xml`, `/sitemap_index.xml` en
`/wp-sitemap.xml` geprobeerd. Sitemapindexen worden recursief verwerkt; een
genormaliseerde URL wordt per wachtrij slechts eenmaal toegevoegd. Gecomprimeerde
sitemaps worden begrensd uitgepakt. XML-entiteiten en DTD's zijn verboden.

Alle aanvragen gebruiken het bestaande veilige transport, de gekozen robotsmodus,
exacte toegestane hosts, expliciete blokkades, DNS-pinning, aanvraagsnelheid,
transfer- en tijdslimieten. Voor robots.txt zelf geldt de bestaande uitzondering
op robots-toegangsregels. Een sitemap geeft geen extra hosttoestemming.
Onbekende eigen subdomeinen worden in de bestaande projectbeoordeling opgenomen.

Grenzen: maximaal 100 sitemaplocaties, indexdiepte 10, dezelfde pagina- en
ontdeklimieten als de linkcrawl (maximaal vijfmaal het pagina-aantal in de
wachtrij). Beschikbare paginagegevens blijven behouden bij een verlopen
netwerkbudget. Ontbrekende, onbereikbare of ongeldige sitemaps verhinderen het
volgen van links niet. Begrensde ontdekking of een resterende pagina-wachtrij
maakt de nieuwe webcrawl onvolledig. Een ontbrekende sitemap op zich is geen
downloadfout van een webpagina.

Het SQLite-checkpoint bewaart sitemapvoortgang, URL-herkomst en linkrelaties.
Een vervolgtaak hoeft de startpagina niet opnieuw te verwerken. Een sitemap die
tijdens verwerking wordt onderbroken kan opnieuw worden opgehaald; URL's en
gepubliceerde pagina's worden niet gedupliceerd. Oude checkpoints zonder deze
nieuwe instelling veranderen niet stilzwijgend van scope.

Per bronrecord staan:

- `discovery_sources`: start_url, sitemap en/of internal_link.
- `sitemap_url`: het eerste sitemapdocument waarin de URL werd aangetroffen.
- `depth`: kortste vastgestelde interne-linkroute vanaf de start-URL, of null.
- `exploration_depth`: afzonderlijke afstand vanaf een crawlseed, gebruikt om
  uitbreiding via links te begrenzen; geen maat voor websitearchitectuur.
- `link_depth_basis`: uitleg van het onderscheid.

Herkomst kan achteraf worden aangevuld: een al bezochte sitemappagina die later
via een interne link wordt gevonden krijgt beide bronnen. Linkdiepte wordt na
het verzamelen afzonderlijk bepaald; sitemapvermeldingen tellen niet als links
en geven geen diepte 0. De eindberekening is begrensd tot 1 miljoen unieke
linkrelaties en 1.000 linkniveaus. Extra herkomstnodes zijn begrensd tot vijfmaal
het paginabudget naast de bestaande wachtrij; overschrijding blijft zichtbaar.
Geen gevonden route is geen bewijs dat een pagina buiten dit onderzoek verweesd
is. Een ontdekte interne link kan zelf afkomstig zijn van een sitemappagina die
geen bekende route vanaf het startadres heeft.

De brongegevens, URL-waarnemingen en paginametingen bewaren de herkomst.
Het URL-dossier toont leesbare bronlabels en onbekende linkdiepte. De scanpagina
toont aantallen sitemapdocumenten, sitemap-URL's in de wachtrij, afgehandelde
URL's en de overlap met interne links. Afgehandeld betekent een ophaalpoging,
geen garantie op HTTP 200. Historische crawls worden niet herschreven.

Validatie dekt onder meer sitemappagina's zonder gewone links, latere
bronaanvulling, echte linkdiepte, onbekende diepte bij niet-bereikbare seeds,
indexcycli, gzip, onveilige XML/hosts, ontbrekende sitemaps, pagina-/dieptegrenzen,
verlopen budgetten, hervatten van checkpoints en publicatie naar de database.
Er is geen nieuwe Django-databasemigratie nodig.

De volledige suite slaagt met 409 tests. Een offline replay van de opgeslagen
vandersteenhoven.nl-documenten verwerkte alle 327 sitemap-URL's in de gezamenlijke
wachtrij (329 URL-pogingen totaal). Ontbrekende pagina-antwoorden in die replay
waren synthetisch: dit bevestigt wachtrijdekking, geen actuele bereikbaarheid.
Er zijn daarbij geen externe aanvragen of databasepublicaties gedaan.
