# Blijvende scaninstellingen

De knop Opslaan in het crawlvenster bewaart instellingen nu in de database bij de website binnen het project. Na vernieuwen, opnieuw inloggen of een volgende crawl blijven die waarden beschikbaar voor projectbeheerders en bewerkers. Iedere website heeft afzonderlijke instellingen voor Website crawlen en Sitemap inlezen.

Instellingen worden met dezelfde ScanForm-grenzen en domeinuitsluitingen gevalideerd als een scan. De aparte POST-actie vereist projectbewerkrechten en CSRF-bescherming. Een mislukte opslag laat het venster open met een foutmelding en wijzigt de actieve kaart niet. Annuleren bewaart niets. Opslaan start geen crawl.

Een nieuwe crawl gebruikt de opgeslagen instellingen, tenzij een expliciete startaanroep een waarde overschrijft. Elke batch bewaart zijn eigen configuratiesnapshot: latere wijzigingen veranderen bestaande metingen niet. Externe-linkcontrole blijft standaard ingeschakeld conform het hostbeleid; uitsluitingen blijven apart beheerd.

Het nieuwe JSON-veld ScanTarget.scan_settings krijgt voor bestaande websites een lege waarde. De bestaande standaardwaarden gelden totdat instellingen zijn opgeslagen. Browser-limieten, time-outs, bestandsbudgetten en geselecteerde schema-profielen worden eveneens bewaard. Website- en onderzoekstype-isolatie voorkomen dat bijvoorbeeld een sitemap-URL op een andere website wordt gebruikt.

De regressiecontrole omvat databaseopslag, opnieuw renderen van de pagina, gebruik bij een volgende crawl, behoud van oude batchconfiguratie, validatiefouten, rol-/projectgrenzen en CSRF. De native Chromium-proef controleert de echte save-handler, numerieke waarden en multiselect, annuleren en een geweigerde serveropslag.

Releaseverificatie: 545 tests geslaagd; native Chromium-proeven voor workflowfilters/fasewissels en blijvende instellingen geslaagd. De echte workerketen met een tijdelijke testwebsite is geslaagd; testgegevens zijn verwijderd. Modelcontrole is schoon; de release bevat migratie scans.0007 voor opgeslagen instellingen.
