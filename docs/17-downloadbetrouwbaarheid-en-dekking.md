# R1 — downloadbetrouwbaarheid, pogingenhistorie en URL-dekking

Nieuwe webcrawls gebruiken `reliability_version=page-retry-v1`. De instellingen
zijn via de scanmodal aanpasbaar: verbinding 1–60 seconden, lezen 1–120 seconden,
maximaal 1–5 pogingen inclusief de eerste. Standaard: 10, 30 en 3. Instellingen
worden bij het starten vastgezet. Oudere batches zonder deze versie behouden hun
oorspronkelijke gedrag en krijgen geen verzonnen pogingenhistorie. De bestaande
veilige HTTP-client gebruikt de ingestelde time-outs; automatische urllib-retries
blijven uitgeschakeld. Browsertransport houdt zijn eigen bestaande profiel.

## Herhaalbeleid en checkpoints

De nieuwe paginawachtrij herhaalt verbinding-/leestime-outs, reset/refused,
verbindingsfouten en tijdelijke DNS-fouten. HTTP 429 en 503 kunnen ook opnieuw
worden geprobeerd. TLS-, onveilige DNS-, validatie- en andere permanente fouten
worden niet blind herhaald. Een echte 404 en een 403 zijn geen time-outretry.

Wachttijden groeien vanaf twee seconden exponentieel. Retry-After in seconden
of als HTTP-datum krijgt voorrang als die langer is. Een retry die niet binnen
het resterende netwerkbudget past wordt niet uitgevoerd. De scheduler schrijft
de eerstvolgende tijd in het SQLite-checkpoint en plant een vervolgtaak met
countdown; geen minutenlang slapende retryworker. Bij overbelasting worden
andere nog niet begonnen aanvragen aan dezelfde host uitgesteld. Reeds lopende
aanvragen worden eerst afgehandeld. Tempo en toegestane gelijktijdigheid worden
bij tijdelijke fouten verlaagd en die vertraging blijft over slices behouden.

Iedere logische paginapoging wordt vóór uitvoering vastgelegd. Een paginapoging
kan een begrensde HTTP-redirectketen en benodigde robotsopvraging omvatten; het is
geen registratie van iedere afzonderlijke HTTP-hop. Sitemap-/robotsaanvragen zijn
geen aparte paginapogingen en krijgen in deze versie geen nieuwe meerpogingenlus.
Zij gebruiken wel de ingestelde netwerk-time-outs. Deze scope staat in de UI.

Het checkpoint bewaart ook afgeronde maar nog niet verwerkte parallelle antwoorden.
Bij hervatten worden die verwerkt zonder opnieuw downloaden. In deze tijdelijke
cache worden alleen benodigde veilige headers opgeslagen, geen Set-Cookie of
autorisatie. Onderbroken pogingen worden als onderbroken geregistreerd; een
onbekende eindtijd wordt niet ingevuld met het hervatmoment. Voor een onbekend
netwerkverzoek na een harde procesuitval kan geen exactly-once-garantie worden
gegeven; de pogingenlimiet en historie blijven wel behouden.

Bij afloop van het netwerkbudget blijven reeds beschikbare antwoorden en eerdere
downloadfouten behouden. Geen extra netwerkrequests om de publicatie af te maken.
Heranalyse en bewaarde antwoorden krijgen niet een kunstmatig nieuwe meetdatum.

## Opslag

- `CrawlAttempt`: UUID, batch, URL/hash, volgnummer, start/einde, status/fout,
  time-outs en veilige technische details. Uniek per batch/URL/volgnummer.
- `ScanRetryTarget`: vastgezette URL, oorspronkelijke bronreferentie en beperkte
  context voor een gerichte vervolgmeting. Verwijderen van de bron laat de
  vastgelegde doel-URL en context staan.
- `CrawlURLCoverage`: geïndexeerde URL-inventaris met bron, ingepland/afgehandeld,
  uitkomst, status en vastgestelde linkdiepte. Inclusief gevonden maar niet
  bezochte sitemap-URL's en toegestane links die door dieptelimieten niet zijn
  ingepland, voor zover de ontdeklimieten registratie toelaten.

Afgeronde pogingen worden per checkpoint idempotent gepubliceerd; de uiteindelijke
paginaresultaten worden éénmaal gepubliceerd. Pogingen tellen geen extra pagina's
en veroorzaken niet meerdere scoreaftrekken. Bronmetingen, analyse en dossiers
houden hun oorspronkelijke identiteit en meetmomenten. Een poging die nog loopt
is geen afgeronde fout en wordt niet als voltooid gepubliceerd.

Nieuwe schema's: `scans.0004_crawlattempt_crawlurlcoverage_scanretrytarget` en
`scans.0005_crawlurlcoverage_attempted_crawlurlcoverage_queued`.

## Interface en vervolgmetingen

De scanpagina toont gevonden, ingeplande, afgehandelde en succesvolle URL's apart,
plus herhaal- en herstelcijfers. Er zijn twee projectafgeschermde pagina's:

- URL-dekking met filters voor sitemap-only, links-only, overlap, niet bezocht,
  succes, HTTP-fout, downloadfout en blokkade, met CSV-export van de selectie.
- Downloadpogingen met URL-filter, tijden, gekozen time-outs, fout/status en CSV.

Bij een onvolledige crawl met downloadfouten kan een bewerker een gerichte
herdownload starten. Alleen vastgezette tijdelijke/onderbroken mislukte URL's
binnen de huidige hosttoestemming worden bezocht, één tegelijk, standaard met
10/30 seconden en maximaal drie pogingen. Nieuwe links worden niet gevolgd.
Dubbel klikken tijdens een actieve herdownload levert dezelfde vervolgtaak op.
Eerdere foutregels en batchstatus worden niet overschreven.

De nieuwe batch verwijst expliciet naar de oorspronkelijke crawl. Overgenomen
linkdiepte is als zodanig gelabeld; de sitestructuur is niet opnieuw gemeten.
Een gerichte herdownload krijgt geen zelfstandig oordeel over verweesde pagina's
en kan geen volledige primaire dossiercrawl zijn. Op de dekkingspagina staan de
vervolgmetingen. Er is in R1 geen stilzwijgende samenvoeging tot een nieuw volledig
auditdossier; toekomstige samengestelde meetsets moeten bronselecties bevriezen.
Een oorspronkelijke onvolledige crawl blijft daarom historisch onvolledig.

POST, CSRF en bestaande projectrechten beschermen de herdownload. Alle lees- en
exportverzoeken zijn projectgebonden. Hostblokkades, publieke DNS-validatie,
pinning, budgetten en annulering blijven gelden. Geen nieuwe netwerktoestemming
ontstaat door een herdownload.

## Validatie

Gerichte scenario's: twee time-outs gevolgd door succes; blijvende time-out;
geen retries voor 404/403/onveilige DNS; Retry-After in beide vormen; uitputting
van budget en pogingen; behouden parallelle resultaten; hervatten en
idempotente publicatie; rechten en CSRF; onveranderde oorspronkelijke bron;
geen uitbreiding via links in een herdownload; vindbare niet-bezochte URL's;
geen kunstmatige volledige dossiercrawl op een retrybatch.

De volledige suite slaagt met **429 tests**. Een echte sem_test-queueproef met
publieke HTTP naar example.com bevestigt de ingestelde time-outs, applicatierechten
voor de nieuwe opslag, URL-inventaris, beveiligde schermen en CSV-exports. Tijdelijke
gebruikers, projecten, batches en sessies zijn daarna verwijderd.

Een afzonderlijke publicatieproef schreef 7.500 pogingen en de dekking van 5.000
afgehandelde plus 1.000 niet-bezochte URL's in 26,64 seconden, met 104,6 MiB piek-RSS.
De database-transactie is teruggedraaid; geen netwerkverkeer. Dit meet alleen de
nieuwe publicatiestap, geen volledige crawl of prestatietoezegging.
