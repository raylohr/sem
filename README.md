# TrafficStack

Django-werkplek voor projecten, Google Search Console en URL-imports uit Screaming Frog en GSC.
GSC-API-resultaten blijven met oorspronkelijke brongegevens en meetmomenten bewaard.
Via Data → Import worden Screaming Frog Internal → All CSV-/XLS-/XLSX-exports en originele
GSC Page indexing-detailrapporten als losse CSV-URL-tabel, CSV-ZIP, XLS of XLSX automatisch herkend
(Nederlands en Engels). De indexeringsreden komt uit Metadata en blijft bij
de import en elk URL-record bewaard. 1970-01-01 geldt als onbekende crawl-datum;
het algemene indexeringsoverzicht zonder URL-tabel is geen URL-import.
Bij een losse GSC-CSV ontbreken metadata en grafiek; categorie en indexeringsreden
kunnen in de preview worden opgegeven en blijven bij elk URL-record bewaard.
Ahrefs Site Audit CSV-exports van Pages en ontbrekende/lege title-tags worden
ook herkend, inclusief optionele backlink- en zoekwoordkolommen. De preview laat
een crawl-/waarnemingsdatum bevestigen; de bestandsdatum is alleen een suggestie.
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

Op het projectoverzicht downloadt **Exporteren naar Excel** een werkbestand met
hoofdoverzicht, URL-inventaris, actieplan, bronwaarnemingen en leesmij. De download
bevat alle gepubliceerde URLs binnen de hostregels, los van zoekfilter/paginering.
Bronmetingen blijven gescheiden; GSC-indexeringsgegevens worden apart van
zoekprestaties geselecteerd. Beoordeling en actievelden zijn leeg en hebben
keuzelijsten voor prioriteit en controlestatus. Excel-bewerkingen worden niet teruggeschreven.

Bij Excel-export opent direct een voortgangsvenster met werkfasen, verwerkte
aantallen en verstreken tijd. Na ontvangst start de download; het venster biedt
ook een downloadlink en foutfeedback. Voortgang is alleen zichtbaar voor de
exporterende gebruiker binnen het project.

Klik op een paginatitel in het URL-overzicht voor de URL-detailpagina. Titels
komen bij voorkeur uit Screaming Frog, daarna uit Ahrefs; zonder titel verschijnt —.
De detailpagina groepeert alle gepubliceerde waarnemingen per hoofdbron, met
velden in twee kolommen en uitklapbare historie en importcontext. Bronnen en
velden worden direct uit de actuele records opgebouwd; verwijderde imports
verdwijnen automatisch uit de weergave.

Standaard toont het URL-overzicht o-traffic met een AHREFS-bronbadge en contenttype met
een SF-bronbadge. Klikken op o-traffic wisselt tussen oplopend, aflopend en
URL-padvolgorde; ontbrekend verkeer staat onderaan. De contenttypekop opent
een modal met meervoudige selectie en aantallen per SF-type/status, inclusief
URLs zonder SF-meting. Zoekterm, sortering en filters blijven behouden bij pagineren.

Index Status toont gsc_report_status als tekst zonder bronrecordlink, met GSC
als referentiebadge onder de kolomkop. De laatste beschikbare indexeringsstatus
blijft zichtbaar naast nieuwere GSC-prestatierecords. Zonder status staat unknown;
URLs zonder GSC-waarneming hebben een aparte filterkeuze. Klikken op de kop
opent een statusfilter dat gecombineerd wordt met zoeken, contenttype en sortering.

Via **Kolommen instellen** bewaart iedere gebruiker per project maximaal zeven
waardekolommen, naast de vaste pagina-/URL-kolom. Bron, detailrapport en veld
worden uit de aanwezige gepubliceerde records opgebouwd. De nieuwste waarneming
waarin het gekozen veld voorkomt levert de waarde; rapporttypen blijven gescheiden.
Kolommen met minder dan tien unieke ingevulde waarden gebruiken een keuzelijst.
Anders gebruiken getallen inclusieve grenzen en tekst een hoofdletterongevoelig
trefwoord. Datumvelden hebben van/tot-grenzen. De filtermodus blijft na opslaan
behouden tot de gebruiker de configuratie opnieuw opslaat. Ontbrekend is apart
selecteerbaar; nul telt als ingevuld. Filters combineren met EN tussen kolommen
en OF binnen een keuzelijst. Eén kolomsortering kan actief zijn; onbekend staat
altijd onderaan. Verwijderen wist de bijbehorende filter-/sorteerstatus.
Kopteksten tellen ingevulde waarden over alle gefilterde URLs, voor paginering.
De weergave begrenst titels/kopteksten tot twee regels, tekst tot 40 tekens,
badges tot 24 en getallen tot 12 met compacte notatie. Tooltips en Excel behouden
de volledige waarden. De URL-kolom blijft op desktop vaststaan bij horizontaal
scrollen. Verwijderde bronvelden blijven als niet beschikbaar herkenbaar totdat
de gebruiker de kolom vervangt of verwijdert.

Screaming Frog Internal → All ondersteunt Engelse en Nederlandse kolommen. Nederlandse
kolomnamen en bekende statuswaarden worden naar het Engelse interne schema vertaald;
de originele kolomnamen blijven in de importmetadata bewaard. Onbekende extra kolommen
blijven behouden. Naast de bestandschecksum vergelijkt de importer binnen hetzelfde
project de volledige genormaliseerde inhoud na hostfiltering. Kolomvolgorde, rijvolgorde,
scheidingsteken en CSV/XLS/XLSX-verpakking tellen daarbij niet mee. Crawltijdstempels
tellen wel mee: een latere crawl blijft een aparte meting. Zonder crawltijdstempel
kan identieke inhoud niet van een nieuwe meting worden onderscheiden. Ook bestaande
SF-imports zonder inhoudshash worden op hun opgeslagen rijen gecontroleerd. Andere
SF-rapporttypen en dbseospider-archieven vallen buiten deze ondersteuning.
