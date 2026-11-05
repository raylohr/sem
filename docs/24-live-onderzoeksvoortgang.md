# Live onderzoeksvoortgang

De bronpagina volgt voortaan de gehele onderzoeksworkflow. Een lege paginawachtrij betekent niet dat bestanden, externe links, analyse of browseronderzoek klaar zijn. De bronstatus blijft de status van die bron; de voortgangssectie toont daarnaast expliciet de actieve vervolgfase.

## Presentatie

Per fase zijn afgeronde eenheden, de bekende omvang, status, uitkomsten en een voortgangsbalk zichtbaar. Pagina’s, bestandsdoelen, externe doelen en browserbezoeken zijn verschillende eenheden. Herpogingen en redirects zijn wel afzonderlijke activiteiten, maar worden niet als extra afgeronde doelen geteld.

De actieve host en wachttijd zijn zichtbaar zodra een aanvraag begint, ook vóór het eerste antwoord. Alleen de hostnaam wordt bewaard: geen pad, querystring, authenticatie- of cookiewaarden. De grafiek kan op netwerkfase en uitkomst worden gefilterd en wisselen tussen uitkomsten en HTTP-statuscodes. Herpogingen, beleidsblokkades en onbekende uitkomsten blijven apart. De activiteit is per seconde gegroepeerd over de laatste vijf minuten; de legenda bevat de totalen van de gekozen selectie over de hele meting.

Een tijdschatting gebruikt het tempo en de starttijd van de huidige fase. Het betreft alleen de momenteel bekende werklijst, geen voorspelling van nog niet ontdekte doelen of alle volgende fasen. Bij onvoldoende gegevens blijft de schatting onbekend. Analyse kan nog controles uitvoeren terwijl de pagina-extractie haar totale aantal bereikt heeft.

De pagina blijft iedere seconde controleren zolang de crawl, analyse, browserfase of geplande technologie-/privacyverrijking actief is. Gedeeltelijke browsermetingen blijven gedeeltelijk; het afronden van de werkstroom maakt onbekende meetuitkomsten niet succesvol.

## Opslag en belasting

Een afzonderlijk SQLite-WAL-bestand per batch bevat kleine fasecounters, actieve aanvragen, tijdvakaggregaten en totalen. De webrequest leest dit bestand read-only zonder bronbodies of de workerlock te openen. Korte schrijfacties per thread staan los van de lange crawl- en analysetransacties. Het bestand blijft bestaan na publicatie en checkpointopruiming, zodat vervolgonderzoek en historische totalen zichtbaar blijven.

De tijdreeks wordt op vijf minuten begrensd; uitlezen is begrensd op 10.000 aggregaten en twintig actieve aanvragen. Langdurige ontbrekende activiteit wordt als waarschuwing getoond, niet als verzonnen vooruitgang. Telemetrieproblemen stoppen de crawl niet. Telemetriebestanden worden via dezelfde verwijderwachtrij opgeruimd als de bron. Een laat antwoord maakt een verwijderd bestand niet opnieuw aan.

Oudere scans gebruiken beschikbare checkpoint-/databasecounters als fallback. Ontbrekende historische tijdstempels worden niet gereconstrueerd als fictieve netwerkactiviteiten. De pagina vermeldt wanneer de uitgebreide registratie ontbreekt. Een nieuwe of hervatte worker registreert nieuwe activiteiten.

## Controle

Regressiegevallen omvatten een lege paginawachtrij met actieve externe aanvragen, zichtbaarheid vóór de response, hostredactie, fasegebonden ETA, herpogingen en onbekende HTTP-statussen, behoud na checkpointopruiming en een actieve browserfase na afronding van de hoofdcrawl. De echte Chromium-proef controleert de fasekaartjes, host/wachttijd, grafiekfilters, fasewissel en het stoppen van polling pas na het eind van de workflow. Een synthetische test start geen echte crawl.

Releaseverificatie: 545 tests geslaagd; native Chromium-proeven voor workflowfilters/fasewissels en blijvende instellingen geslaagd. De echte workerketen met een tijdelijke testwebsite is geslaagd; testgegevens zijn verwijderd. Modelcontrole is schoon; de release bevat migratie scans.0007 voor opgeslagen instellingen.
