# R3 — inhoudelijke interpretatie van HTML

R3 implementeert G06, G10, G11 en G16 uit het gapplan. Nieuwe crawls gebruiken `html-v6`; een nieuwe analyse gebruikt `assessment-v5` en regelversie 5. De oorspronkelijke bronrecords, meetmomenten en historische dossiers worden niet overschreven.

## Afbeeldingen en linknamen

Afbeeldingen behouden alt-aanwezigheid, oorspronkelijke tekst en bronlocatie. De nieuwe interpretatie onderscheidt:

- ontbrekend alt-attribuut: de bestaande controle;
- tekst aanwezig, inhoudelijke geschiktheid nog onbeoordeeld;
- expliciet decoratief gemarkeerd met lege alt: geen automatische alt-fout;
- lege alt zonder voldoende aanwijzingen: laag reviewadvies.

Een lege alt is toegestaan voor decoratie. Zonder inhoudelijk oordeel kan de crawler niet bewijzen dat een afbeelding informatief of decoratief is. Een reviewadvies is dus geen instructie om iedere lege alt te vullen. Herhaalde afbeeldingen leveren maximaal één bevinding per regel per pagina op, met een totaal en hoogstens twintig voorbeelden. Het dossier groepeert dezelfde controles over pagina’s; een gedeelde oorzaak moet nog worden beoordeeld.

Bij een klikbare afbeelding wordt de linknaam apart onderzocht. Een expliciet benoemde link met een decoratieve afbeelding is toegestaan. Een afbeeldingslink zonder afleidbare naam krijgt een concreet signaal. Linktekst en afgeleide naam staan afzonderlijk op de linkpagina; de oude ankerwaarde blijft bewaard voor compatibiliteit.

De begrensde naamafleiding gebruikt eerst eenduidige aria-labelledby-verwijzingen, dan aria-label, daarna tekst/afbeeldings-alt en als fallback title. Een ARIA-label op een afbeelding kan aan de naam bijdragen. Ontbrekende of dubbele ID’s, complexe verwezen subbomen, SVG, afgekorte waarden en andere onzekere gevallen blijven onbekend. Sommige simpele labelverwijzingen naar verborgen tekst zijn uitleesbaar. Dit is geen volledige implementatie van de accessible-name-specificatie: CSS, JavaScript, complexe recursieve ARIA-verwijzingen en de accessibility tree vragen browsercontrole. Algemene namen zoals ‘Lees meer’ leveren alleen in de inhoud een laag, contextafhankelijk advies op.

## Koppen

De bestaande H1-missingcontrole blijft bestaan. Nieuwe afzonderlijke signalen beoordelen:

- de eerste inhoudskop is geen H1;
- een H1 volgt op een H2;
- een volgend kopniveau slaat een niveau over;
- dezelfde H2-tekst komt vaker terug.

Expliciete header-, navigatie- en footergebieden en expliciet verborgen koppen zijn uitgesloten van deze inhoudscontroles. Gewone meerdere H2’s met verschillende teksten zijn toegestaan. CSS-klassen, visuele hiërarchie en de kwaliteit van kopteksten worden hiermee niet bewezen. De oude ruwe heading_jump blijft in de vaste scorecatalogus; bij volledige R3-invoer krijgt de gebruiker de specifiekere bevinding in plaats van een dubbel generiek signaal.

## Open Graph en X Cards

De parser bewaart og:* en twitter:* met waarde, elementpad en bronpositie. Controles tonen ontbrekende profielvelden, lege of ongeldige waarden, verschillende waarden voor enkelvoudige velden en plaatsing buiten de head. Meerdere Open Graph-afbeeldingen/video’s/audiofragmenten zijn toegestaan. X-profielen kunnen titel, omschrijving en afbeelding uit Open Graph afleiden. Het gekozen previewprofiel is een advies voor voorspelbaar delen, geen rankingvoorwaarde.

Previewafbeeldingen worden nog niet gedownload en previews worden niet gerenderd. Bereikbaarheid valt onder R4. Afgekorte of historisch niet aanwezige inventarissen geven geen geslaagde controle voor ontbrekende gegevens.

## Rapporten en heranalyse

Alle nieuwe signalen hebben verklarende aanbevelingen, bronbewijs en dekking. De linkpagina toont tekst en naam afzonderlijk; koppen en social metadata hebben eigen uitklapbare tabellen. Export en onderzoeksdossiers nemen de nieuwe regels en bewijssnapshots mee. `report-v1` behoudt zijn oorspronkelijke regelset en gewichten; de aanvullende regels tellen niet automatisch mee in die numerieke scores.

De bestaande heranalysefunctie kan opgeslagen HTML zonder nieuwe webverzoeken opnieuw uitlezen. Nieuwe analyses bevatten R3; historische analyses en rapporten blijven beschikbaar. Heranalyse verandert geen meetdatum en bewijst geen herstel.

## Referentiecontrole

De 59 gemeenschappelijke succesvolle benchmarkpagina’s zijn opnieuw uit opgeslagen HTML geëxtraheerd. Dat reproduceert 965 afbeeldingsverwijzingen met lege alt en ontbrekende Open Graph- en X Card-profielen op alle 59 pagina’s.

Het verschil met de 37 Screaming Frog H1 Non-Sequential-meldingen is verklaarbaar: in de ruwe bron volgt op 37 pagina’s een H1 op een eerdere H2. De overige 22 hebben geen H1. Alle 59 beginnen in de ruwe bron met een andere kop dan H1, mede door de gedeelde taalnavigatie. Na uitsluiting van expliciete navigatie/header/footer meldt R3 op 22 pagina’s een eerste inhoudskop zonder H1 en één sprong in de inhoud. De oude sprongregel meldde ook één pagina. Dit zijn verschillende controles, geen bewijs voor 36 gemiste niveausprongen.

De reproduceerbare aantallen staan in `reports/implementation/r3-verificatie.json`. Volledige toolpariteit wordt hiermee niet geclaimd.

De volledige suite slaagde met 457 tests. De runtimecontrole `ops/checks/content_runtime_smoke.py` verifieerde op sem_test de echte heranalysewachtrij, herstel uit opgeslagen documenten, ongewijzigde bronrecords, linknamen, bewijspagina’s, groepering in het dossier en exports. De tijdelijke testgegevens zijn verwijderd. R3 is geactiveerd op test; productie is niet gewijzigd.
