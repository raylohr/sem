# Standaard hostbeleid: domein, gebruikte bestanden en externe links

Nieuwe scans bewaren `host_policy_version=discovered-hosts-v2`. Voor bestaande websites is geen lijst leveranciers of toestemming per gevonden subdomein meer nodig. De instellingen staan bij Websitescans onder **Uitsluitingen (optioneel)**; gevonden hosts zijn een inventaris, geen verplichte vragenlijst.

## Pagina’s

Het geregistreerde domein van het scandoel en alle werkelijk gevonden subdomeinen zijn toegestaan. Links, sitemaps en redirects mogen naar die subdomeinen leiden. Er worden geen subdomeinen geraden of DNS-lijsten afgezocht. Externe redirects worden vastgelegd, maar geven geen verdere paginacrawltoestemming.

De domeingrens gebruikt de meegeleverde Public Suffix List inclusief private suffixen: `tenant.github.io` omvat niet `other.github.io`. Vergelijking gebeurt op volledige domeinlabels, zodat `example.com.evil.net` geen subdomein van `example.com` is. Een IP-adres of publiek suffix geeft geen algemeen subdomeinrecht.

Alle gevonden pagina’s delen de bestaande URL-, diepte-, duur- en transferbudgetten. Er is geen afzonderlijk onbeperkt subdomeinbudget.

## Bestanden en browser

Bestanden waarnaar de onderzochte pagina’s verwijzen mogen van publieke externe hosts komen. Dit geldt ook voor daadwerkelijk gevonden afhankelijkheden: CSS kan een font of andere stylesheet laden en browser-JavaScript kan een script van een nieuwe leverancier aanvragen. Er is geen ingebouwde lijst die alleen Google Fonts/jsDelivr/Tailwind toestaat.

De aparte HTTP-bestandsfase blijft beperkt tot ondersteunde HTML/CSS-verwijzingen en haar budgetten; zij voert zelf geen willekeurig JavaScript uit. Playwright behandelt de werkelijk uitgevoerde GET-aanvragen binnen het bezoekbudget. Een bestandstoestemming geeft geen recht om pagina’s op de leverancierwebsite verder te crawlen.

## Externe links

Nieuwe volledige crawls schakelen externe-linkmetingen standaard in; een verouderde API-waarde `check_external_links=false` zet dat niet uit. Gebruik de afzonderlijke hostuitsluitingen als een doel niet gemeten moet worden. De externe bestemming en redirects worden gecontroleerd, zonder de pagina-links van die externe site verder uit te breiden. Herdownload- en gerichte vervolgmetingen blijven begrensde aanvullende metingen.

Niet-standaard antwoorden zoals HTTP 999 krijgen unknown, geen pass. Hiervoor is `resource-controls-v2` vastgelegd; de oude resourcecontroles blijven als versie v1 herkenbaar in historische dossiers en verificatie.

## Uitsluitingen en veiligheid

Er zijn afzonderlijke uitsluitingen voor pagina’s, bestanden en externe-linkmetingen. Elke uitsluiting omvat de exacte host en onderliggende subdomeinen, en gaat voor op het standaardgedrag. De start-URL mag niet uitgesloten worden. Een nieuwe relevante uitsluiting tijdens een lopende scan kan de scan stoppen; een nieuwe scan gebruikt de vastgelegde uitzonderingen.

Elke aanvraag en iedere redirect blijft onderworpen aan URL-/poortvalidatie, uitsluitend publieke DNS-resultaten, IP-pinning, limieten en toegang tot het project. De browser heeft zelf geen IP-netwerk. Formulier-/POST-verzoeken, WebSockets en serviceworkers krijgen geen nieuwe toestemming door dit beleid. Bestaande privacycontexten en hun afzonderlijke toestemmingsinteracties blijven behouden. Een bestand laden is geen uitspraak over privacyconformiteit.

## Compatibiliteit en verificatie

Oudere opgeslagen runconfiguraties zonder de nieuwe vlaggen houden hun exacte-hostgedrag. Nieuwe scans en nieuw browseronderzoek vanuit oude crawlbronnen gebruiken het nieuwe beleid. Bestaande metingen, rapporten en blokkades worden niet stilzwijgend herschreven. De oude resourcehosts-invoer blijft als verborgen compatibiliteitsveld bestaan, maar is niet nodig om een leverancier toe te laten.

Gerichte tests dekken domeingrenzen, private suffixen, gevonden redirects, onafhankelijke uitsluitingen, nieuwe leverancierafhankelijkheden, DNS naar private IP’s, intrekking tijdens onderzoek en onbekende statuscodes. De echte geïsoleerde Chromium-probe levert synthetische externe CSS→font-, script→script- en afbeeldingsantwoorden aan en bevestigt dat privé-adressen en POST geblokkeerd blijven. Deze probe doet geen aanvragen naar externe websites.

Verificatie: 526 tests geslaagd, plus 28 gerichte controles na de integratie met beleidsdocumenten. De echte Chromium-afhankelijkheidsproef is geslaagd. Modellen en migraties zijn synchroon; geen nieuwe databasemigratie nodig.
