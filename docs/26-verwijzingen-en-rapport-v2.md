# Verwijzingen en rapportversie 2

Iedere bewaarde verwijzing krijgt twee onafhankelijke kenmerken:

- Bestemming: eigen website, extern, binnen dezelfde pagina, of andere actie.
- Functie: navigatie, download, geladen bestand, technische instructie, paginaonderdeel of contactactie.

Onderzoeksbeleid staat daar los van: toegestaan, uitgesloten, lokaal of ongeldig/niet ondersteund. Uitgesloten subdomeinen blijven eigen website. De classifier verleent geen netwerktoestemming; publieke DNS-validatie, IP-pinning en hostuitsluitingen blijven in de bestaande netwerklaag.

`ReferenceObservation` bevat de bronpagina, beide geïndexeerde assen, beleid, uitkomst, reden, vindplaats en het gebruikte meetbewijs. HTML bewaart ook de aanwezigheid van het download-attribuut en begrensde id/name-doelen voor lokale fragmentcontrole. Contactcontrole beoordeelt alleen syntaxis; er wordt geen bericht verzonden of telefoonverbinding gemaakt. Canonical/taalvariantverwijzingen blijven bij hun gespecialiseerde paginacontroles beoordeeld.

Bestandsextensie en HTML-element leveren een aanwijzing. Het ontvangen Content-Type levert het waargenomen bestandstype. Een extensieloze URL met een PDF-antwoord wordt als download behandeld. Een PDF-adres met een HTML-antwoord blijft een download maar krijgt een bestandstypeprobleem. Dezelfde URL kan meerdere verwijzingen/functies hebben; de analyse hergebruikt bestaande pagina-, bestand- of externe-linkmetingen en haalt niets opnieuw op.

Interne HTTP-/redirect-/indexeerbaarheidscontroles beoordelen alleen eigen navigatiebestemmingen. Externe bestemmingen, downloads, geladen bestanden en lokale acties vullen die paginacontroles niet meer met onbekende uitkomsten. Uitgesloten eigen navigatiebestemmingen blijven als ontbrekende dekking herkenbaar. Beschikbare externe/bestandsmetingen worden voor die verwijzingen daadwerkelijk gekoppeld.

Uitkomsten zijn geslaagd, probleem, onbekend, niet onderzocht en niet van toepassing. Een mislukt/geblokkeerd netwerkantwoord is onbekend; een uitsluiting, ontbrekende meting of scanbudget is niet onderzocht. Beide leveren geen scorepunten op en blijven in de toepasselijke dekkingsnoemer. Niet van toepassing valt erbuiten. Een verwijzing naar een ontbrekend lokaal paginaonderdeel of een ongeldig contactadres levert ook een bevinding met bronbewijs op.

Nieuwe analyses gebruiken `assessment-v8`, controleversie `8`; nieuwe HTML-extracties `html-v9`. Nieuwe dossiers gebruiken `report-v2` en vijf onderdelen: techniek, content, bestanden/externe links, gebruikerservaring/toegankelijkheid en privacy/technologie. Bestanden en externe links hebben een eigen score/dekking en aparte subdekking. Het ontbreken van veldmetingen blijft expliciet onbekend. Privacy houdt een scenario-dekking en technologie een afzonderlijk afhankelijkheidsprofiel binnen dezelfde kaart; geen juridisch cijfer.

Het dossier bevriest verwijzingen in `DossierReference`. Filters op bestemming, functie en uitkomst, redenen, bewijs en JSONL-export staan bij 'Verwijzingen'. Ook ontdekte maar niet gemeten bestands-/externe doelen worden in de eigen dekking meegenomen. Bronnen kunnen later opnieuw worden geanalyseerd zonder de oude dossiers te herschrijven. Bij ontbrekende historische fragmentinformatie volgt onbekend, geen verzonnen geslaagde controle.

Grenzen: 6.000 verwijzingen per bronpagina, 500.000 per dossier en 50.000 bestandsdoelen. Afkapping wordt expliciet geregistreerd. Er worden geen oude dossiers of productiebronnen automatisch gewijzigd. Voor bestaande crawls: heranalyseer de opgeslagen gegevens (zo nodig met herextractie uit opgeslagen HTML) en stel daarna een nieuw dossier samen.
