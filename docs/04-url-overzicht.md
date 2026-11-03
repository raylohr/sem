# Gecombineerd URL-overzicht

Per project is een URL-register beschikbaar onder:

- HTML: /workspace/projects/<project-id>/urls/
- JSON: /projects/<project-id>/urls/
- URL-dossier: dezelfde route met <url-id>/ erachter.

Alle routes vereisen een actieve gebruiker met projecttoegang. Lezers kunnen
gegevens bekijken; wijzigingen lopen via de bestaande import- en scanrechten.

## Wat wordt gecombineerd?

Elke herkende primaire URL uit een succesvolle bronregel krijgt een projectgebonden
identiteit. De bronregel levert één afzonderlijke URLObservation op, met batch,
oorspronkelijke URL, meetdatum/-tijd indien bekend en expliciet brongebonden feiten.
De oorspronkelijke SourceRecord blijft ongewijzigd beschikbaar. Er worden geen
verkeerscijfers opgeteld over tools, imports of overlappende rapportageperioden.

Ondersteund:

- GSC-coverage-URL's met indexeringsreden en afzonderlijke laatst-gecrawld-datum.
- GSC-API met dagelijkse klikken, vertoningen, CTR (fractie) en positie.
- Ahrefs met geschat organisch verkeer, backlinks, verwijzende domeinen en ratings.
- Screaming Frog met echte HTTP-observaties, metadata en indexeerbaarheid.
- Eigen crawler met HTTP, metadata, robotsblokkades en scanfouten.
- Sitemapvermeldingen met herkomst en lastmod, zonder verzonnen paginafetch/status.
- Contentaudit met bevindingen, acties, prioriteit en bewijsstatus als uittreksel.

De sitestructuurregels uit de samengestelde Ahrefs/auditwerkmap krijgen scope
subtree. Die staan apart achter filter Gegevenstype → Sitestructuur / paden. Hun
cijfers kunnen onderliggende pagina's omvatten en worden geen individuele
paginametrics. Op de URL-detailpagina zijn de scopes zichtbaar gelabeld.

## Identiteit en herkomst

Normalisatie beperkt zich tot scheme-/hostnormalisatie (inclusief IDNA/IP-notatie),
standaardpoorten, lege rootpaden en fragmenten. HTTP/HTTPS, www, padhoofdletters,
trailing slashes, betekenisvolle escaping en queryvolgorde blijven gescheiden.
Canonical- en redirectdoelen worden niet stilzwijgend met de bron-URL samengevoegd.
Alleen absolute HTTP(S)-URL's zonder ingebedde credentials worden geïndexeerd;
onduidelijke/relatieve waarden blijven in de bron aanwezig.

Indexering doet geen netwerkverzoeken. Interne adressen in rapportages kunnen als
bron-URL worden bewaard; de afzonderlijke crawler blijft zulke fetches blokkeren.
Een URL-register kan afbeeldingen, feeds, legacy-URL's en andere bestanden bevatten.
Het aantal URL's is geen telling van zelfstandige inhoudspagina's.

Per waarneming zijn tool, batch, uitvoerende gebruiker, importtijd, meetmoment,
rapportageperiode en oorspronkelijke bronregel terug te vinden. Het URL-dossier
linkt rechtstreeks naar precies die bronregel, ook als deze buiten de eerste tien
voorbeeldregels van een batch valt.

## Datums en signalen

Een onbekende meetdatum blijft onbekend. Importtijd wordt niet als meetdatum
gebruikt. GSC-prestatiedatums blijven kalenderdatums in America/Los_Angeles.
Screaming Frog-tijdstippen zonder tijdzone leveren een kalenderdatum op; de exacte
bronwaarde blijft bewaard zonder een tijdzone te verzinnen. 1970-01-01 wordt als
onbekend behandeld. Lastmod en Google's laatst-gecrawld zijn afzonderlijke feiten,
geen vervanging voor de meetdatum van de waarneming. Een rapportageperiode is ook
geen verzonnen puntmeting en blijft afzonderlijk bij de batch beschikbaar.

HTTP-signalen tonen de verschillende echte statusmetingen binnen de selectie,
niet automatisch de huidige status. Een GSC-indexeringsreden met '404' wordt niet
omgezet in een gemeten HTTP-404. Niet-HTTP-exportcodes zoals 0 en 999 blijven raw.
Indexeerbaarheid is een technische beoordeling uit brondata; geen Google-indexstatus.
Als meerdere metingen verschillen, blijven beide signalen zichtbaar.

## Filters en paginering

Filters: URL-deel, exacte host, bron, afzonderlijke import/scan, scope, gemeten HTTP,
indexeerbaarheid, HTTP-fout/ontbrekende titel/robotsblokkade, HTML/onbekend/afbeelding/overig,
en meetdatum van/tot. Onbekende meetdatums kunnen expliciet worden meegenomen.
Alle bron-/signaal-/datumfilters gelden voor dezelfde waarneming. Zo wordt een
GSC-signaal niet per ongeluk gecombineerd met de HTTP-status van een andere bron.

Standaard gelden de primaire projecthost en bestandstype HTML. HTML vereist een
bekend HTML-contenttype; onbekend wordt niet als HTML ingevuld. Het overzicht meldt
hoeveel bronmetingen met onbekend bestandstype binnen de overige filters vallen,
met links naar `content=unknown` of alle bestandstypen (`content=`). Onbekend omvat
een ontbrekende sleutel, JSON-null of een lege contenttypewaarde. Deze waarden vallen
niet onder Overige bestandstypen. Het API-veld
`excluded_unknown_content_observations` geeft hetzelfde aantal uitgesloten metingen.

Bronnen zonder expliciete selectie volgen de standaard: alle beschikbare bronnen
behalve Contentaudit voor paginametingen; bij sitestructuur ook Contentaudit.
Contentaudit kan expliciet worden geselecteerd. Een uitsluitend uit auditdata
bestaand project toont die gegevens niet ongemerkt als standaard paginametingen.
Sitestructuur heeft geen paginacontenttype: het bestandstypefilter is daar
uitgeschakeld. Zo blijft oorspronkelijke Ahrefs-sitestructuurdata uit samengestelde
auditbestanden bereikbaar. De export bewaart daarvan alleen oorspronkelijke
bronkolommen en sluit auditbeoordelingen uit. Alle filters blijven projectgebonden
en gelden voor dezelfde waarneming. Expliciet gekozen bronnen worden niet aangevuld
met andere bronnen.

Per pagina worden 25 URL's getoond. Samenvattingen worden alleen voor die zichtbare
URL's berekend. Het dossier toont 25 waarnemingen per pagina; ieder feit blijft bij
zijn eigen bron. Er wordt geen universele 'laatste waarheid' gekozen.

JSON gebruikt dezelfde filters als het HTML-formulier: q, host, source, batch,
scope, http, indexability, signal, content, date_from, date_to, include_unknown en
page. Batchkeuzes worden uitsluitend binnen het huidige project gevalideerd.

## Automatische indexering en verwijderen

publish_records publiceert bronregels en URL-observaties in dezelfde transactie.
Ook API- en scanpublicatie blijft atomair met de bijbehorende raw payloads/documenten.
Bij batchverwijdering verdwijnen uitsluitend de gekoppelde waarnemingen. Een URL
blijft bestaan zolang andere bronregels ernaar verwijzen; zonder verwijzingen
wordt hij verwijderd. Filters en samenvattingen werken direct met de resterende data.

De importdetails tonen URL-herkenning: gekoppelde regels, metadata zonder primair
URL-veld en niet-eenduidige URL's met links naar maximaal tien voorbeelden. Niets
uit de bron wordt weggegooid. De afgeleide index kan opnieuw worden opgebouwd:

```sh
docker compose exec web python manage.py reindex_urls
# Optioneel: --project <project-id>
```

Dit wijzigt geen oorspronkelijke bronrecords en behoudt bestaande URL-identiteiten
waar de URL nog voorkomt. Gebruik bij importcode de publicatieservice; handmatige
ORM/SQL-toevoegingen aan SourceRecord vereisen een indexherbouw.

## Validatie

127 regressie-/autorisatietests slagen. De volledige set van 18 echte voorbeelden
is tijdelijk met het beperkte runtimeaccount geïmporteerd en geïndexeerd. Resultaat:
10.158 URL's met 22.833 individuele URL-waarnemingen en 1.227 afzonderlijke
sitestructuurregels. Zestien niet-eenduidige URL-regels bleven in de bron aanwezig.
Alle tijdelijke gegevens van die controle zijn transactioneel teruggedraaid.

Telling plus samenvatting van de eerste pagina duurde in die praktijktest circa
0,05 seconde; dit is een lokale meting, geen garantie voor iedere workload.
Desktop-/mobielcontrole omvatte filters, herkomstlinks en gescheiden GSC/Ahrefs-cijfers.
Geen productiegegevens zijn gewijzigd; er is geen nieuwe live websitecrawl uitgevoerd.
