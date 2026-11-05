# R5 — expliciete browserfase, dynamische ontdekking en meetcontext

R5 omvat G22, G08 en G13. De browser blijft geïsoleerd zonder IP-netwerk, databasecredentials of mediatoegang. Alleen de vertrouwde GET-broker mag publieke DNS-gepinde verzoeken uitvoeren. R5 verandert de bestaande scorecatalogus report-v1 niet.

## Onderzoeksproces

De webinterface biedt standaard een baseline-browserfase na een geslaagde of gedeeltelijke HTML-crawl. De instelling kan vóór starten worden uitgezet. API-aanroepen moeten `browser_phase` expliciet aanzetten. Instellingen `browser_max_pages` (standaard 20, maximaal 100) en `browser_timeout_seconds` (standaard 30, maximaal 60) worden in de scan vastgelegd. De browserfase neemt `concurrency` en `requests_per_second` over van de broncrawl. De broker verwerkt bestanden parallel via genummerde verzoeken en antwoorden; de gedeelde bovengrens geldt voor alle actieve HTTP-verzoeken binnen hetzelfde onderzoek, inclusief robots.txt, redirects en herpogingen. Het verzoektempo blijft per host begrensd; robotskeuze en veiligheidscontroles blijven gelden. De oude aparte `browser_requests_per_second` wordt niet meer gebruikt voor nieuwe browserfasen. Sitemapimports en gerichte vervolgmetingen starten geen automatische browserfase. Zonder succesvolle HTML wordt de automatische fase expliciet overgeslagen.

De browserfase is een eigen batch gekoppeld aan de oorspronkelijke crawl. Herhaald inplannen maakt geen tweede automatische fase; een mislukte wachtrij kan vanuit Browseronderzoek worden hervat. De crawlpagina linkt naar voortgang en dekking. Handmatig browseronderzoek en afzonderlijke privacycontexten blijven mogelijk. Toestemmingsscenario’s worden niet automatisch in een gewone baseline opgenomen.

## Selectie en dynamische links

Een deterministische steekproef spreidt over hosts en URL-paden. Een begrensd deel bevat kandidaten met weinig servertekst, een ontbrekende H1, aanwijzingen voor client-rendering of een interactief URL-pad. Dit zijn heuristieken, geen bewezen templateclassificaties. Redenen staan bij de selectie. Historische scans zonder uitgebreide paginakenmerken gebruiken een expliciete fallback.

Binnen het totale browserbudget wordt standaard circa een kwart, met maximaal vijf pagina’s, gereserveerd voor nieuwe doelen. Het startformulier kan dit aanpassen. De reserve wordt niet boven op het totaal opgeteld. Privacyonderzoek gebruikt dezelfde initiële selectie per scenario en volgt geen scenario-afhankelijke links automatisch.

De browser voert maximaal tien scrollstappen en vijf native details-openingen uit, binnen maximaal acht seconden verkenningsbudget (standaard vier seconden). Alleen gewone details worden geopend; geen willekeurige knoppen, formuliersubmissies of navigatieklikken. Site-JavaScript kan op scroll/toggle reageren; de broker blijft niet-GET-verkeer en verboden hosts blokkeren. Aantal stappen, scrollposities, fasen en volledigheid blijven zichtbaar. Het bereiken van de ingestelde grens bewijst niet dat alle dynamische inhoud ontdekt is.

Nieuwe DOM-links krijgen een eigen bewaarde wachtrij met verwijzende pagina, scenario, fase en status: ingepland, al geselecteerd, buiten budget, bestand, infrastructuur of buiten hosttoestemming. Alleen baseline-doelen binnen de hostscope en de bestaande diepte/paginagrenzen krijgen automatisch een browserbezoek.

De gebruiker kan daarna via een POST-actie een afzonderlijke HTTP-vervolgmeting starten, maximaal 1.000 unieke doelen. Doelen die al in de oorspronkelijke URL-inventaris staan of al aan een vervolgmeting zijn gekoppeld worden overgeslagen. Het huidige hostbeleid wordt opnieuw toegepast. De vervolgmeting haalt alleen geselecteerde doelen op, breidt de linkcrawl niet uit en start geen nieuwe browserketen. Herkomst blijft aan het bronbezoek gekoppeld. De oorspronkelijke crawl wordt niet vervangen. De besloten JSONL-export bevat browsermetingen, server-/DOM-verschillen, geometrie, modelversies, bewijschecksums en de volledige bewaarde linkwachtrij.

## HTML en DOM

Een browserbezoek bewaart het initiële HTTP-document, de gerenderde DOM en een screenshot. De vergelijking betreft het HTTP-antwoord tijdens dat browserbezoek; de oorspronkelijke crawl heeft een eigen meetmoment en bronrecord. De browserdetailpagina toont beide herkomsten. Heranalyse of browseronderzoek overschrijft geen oudere paginaresultaten.

## Afbeeldingen

Per waargenomen afbeelding worden currentSrc, src, srcset/sizes, opgegeven afmetingen, intrinsieke afmetingen, gerenderde CSS-afmetingen, viewport, DPR, scrollpositie en relevante CSS-eigenschappen bewaard. Geometrie wordt in de uiteindelijke scrollpositie gemeten. Een onzichtbaar of niet geladen element levert geen gefingeerde intrinsieke nul op. Een werkelijk nul CSS-formaat is wel een meetwaarde.

Een snapshot vóór scrollen en de eindinventaris ondersteunen lazy-loadonderzoek. De dekking noemt geladen aantallen, de inventarisgrens (500) en of de scroll het documenteinde bereikte. Niet-geladen afbeeldingen zijn niet automatisch kapot: budgetten, lazy loading of blokkades kunnen de oorzaak zijn. Afmetingen alleen bewijzen geen besparing of layoutshift.

## Titelbreedte als schatting

De vaste schatting `sem-title-width-v1` meet de gerenderde documenttitel in een afzonderlijke schone browsercontext met de meegeleverde DejaVu Sans op 20 CSS-px. De font-SHA256 en Chromium-versie worden vastgelegd. Body-CSS en door de website geladen fonts beïnvloeden dit model niet. De referentiegrens van 600 px is laag advies, geen garantie van Google-weergave of een rankingvoorwaarde. Ontbrekende/mislukte metingen blijven onbekend; er is geen fictieve pixelwaarde. Deze nieuwe controle wordt niet automatisch in de numerieke scoreformule opgenomen.

## Verificatie

De echte networkless Chromium-probe toont ontdekking na scrollen en details, geladen lazy-content met currentSrc/intrinsieke en gerenderde afmetingen/DPR, en reproduceerbare titelbreedte ondanks andere body-fontgroottes. Private adressen en POST blijven geblokkeerd. De workflowtests controleren automatische start, idempotentie, selectieredenen, budgetten, gescheiden scenario’s, bronbehoud en projectscope.

De aangeleverde Screaming Frog-run gebruikte Text Only. Die levert geen vergelijkingsbewijs voor deze browsermetingen of voor de pixelschatting. Een browsersteekproef is geen volledige JavaScript-crawl, toegankelijkheidsaudit, Lighthouse-score of veldmeting.

De live referentiemeting omvat drie geselecteerde pagina’s van vandersteenhoven.nl (homepage, algemene voorwaarden en contact) met een expliciet vastgelegd desktopprofiel. Alle drie gaven HTTP 200. De titelbreedteschattingen waren respectievelijk 379, 574 en 415 px. Per pagina bleven twee niet-toegestane resourceverzoeken geblokkeerd; de context is dus gedeeltelijk. Afbeeldingsgeometrie en screenshots beschrijven deze doorgelaten context, niet een volledig onbeperkte bezoekersweergave. De oorspronkelijke SF Text Only-run wordt niet als browserreferentie gebruikt. Resultaten en screenshots staan in reports/implementation/r5-verificatie.json en reports/benchmarks/2026-11-05-browser/.

Afronding: 497 tests geslaagd, de native Chromium-probe en de echte crawl → browser → dossier-wachtrijcontrole geslaagd. Browserexport en dossierexport gecontroleerd, tijdelijke testgegevens verwijderd. R5 is actief op de gezonde testomgeving. Productie blijft ongewijzigd tot de gevraagde uiteindelijke release.
