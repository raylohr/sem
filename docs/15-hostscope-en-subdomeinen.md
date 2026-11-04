# Hostscope en subdomeinen

Een ingevoerde URL blijft het startadres, inclusief protocol, subdomein, pad en
query. Alleen het fragment vervalt. Het geregistreerde domein wordt afzonderlijk
bepaald met de meegeleverde Public Suffix List, inclusief private suffixen,
wildcards en uitzonderingen. Daardoor is `bedrijf.co.uk` een domein en blijven
verschillende `github.io`-gebruikers gescheiden. De lijst staat onder MPL-2.0 in
`scans/data/public_suffix_list.dat`, met upstream-versie en licentie in de header;
bij onderhoud de lijst vanaf de daarin vermelde officiële bron vernieuwen. Er is
geen externe suffixaanvraag tijdens een crawl.

Bij een hoofdadres worden domein en www standaard toegestaan, ook voor bestaande
scanconfiguraties. Starten op `blog.bedrijf.nl` geeft uitsluitend toestemming voor
die exacte host: geen impliciete toestemming voor andere subdomeinen. Expliciet
uitgesloten hosts gaan vóór standaard- en expliciete toestemmingen. Wildcards zijn
niet toegestaan in de hostinstellingen. Publieke IP-validatie, DNS-pinning en
controle per redirect blijven intact.

Projectbeheerders kunnen onder Websitescans bestaande websiteconfiguraties
bewerken. De navigatiehosts en browserbestandshosts worden afzonderlijk ingesteld.
`HostPolicy` bewaart per project en exacte host de keuze voor beide doeleinden,
wie de keuze opsloeg, het wijzigmoment en een eerste ontdekkingsvoorbeeld. Bij een
nieuwe configuratie worden bestaande projectbeslissingen niet overschreven.
Toestemming voor een subdomein geldt binnen hetzelfde geregistreerde domein voor
andere scanconfiguraties van dat project. Expliciete blokkades gelden projectbreed.

De crawler registreert onbekende eigen subdomeinen uit links, bestanden en
geblokkeerde redirects. Ook sitemapverwijzingen worden beoordeeld. Ontdekken
veroorzaakt geen netwerkverzoek naar die host. De interface toont Te beoordelen,
met bron- en voorbeeldadres. Ververs de pagina tijdens een lopende crawl voor
nieuwe ontdekkingen. Per slice worden maximaal 100 verschillende hosts verwerkt;
voorbeelden zijn begrensd tot één bron/URL per host. Geen antwoord geeft geen
extra navigatierecht. Andere geregistreerde domeinen worden niet als eigen
subdomeinen voorgesteld.

De huidige crawl houdt de toegestane scope van het startmoment. Nieuwe
toestemmingen worden bij een nieuwe crawl gebruikt; die ontdekt de links opnieuw.
Er is geen automatische voortzetting of stilzwijgende scopeverruiming. Intrekken
van toestemming stopt een lopende crawl bij de volgende toegangscontrole.

Browseronderzoek neemt expliciete projectbestandshosts mee naast per-run keuzes
en automatisch herkende eigen resourcehosts. Expliciete resourceblokkades gelden
ook voor automatisch herkende hosts. Een bestandshost wordt geen navigatiehost.
Het blokkeren van bestanden op een navigatiehost blokkeert niet de hoofdnavigatie
zelf. De bestaande browserisolatie en publieke netwerkcontrole blijven gelden.

Een geblokkeerde redirect krijgt een afzonderlijke bestemming in de brongegevens,
een onvolledige eindstatus en waarschuwing. Bij de startpagina staat expliciet dat
de eigenlijke website niet onderzocht is. Nieuwe runs bewaren ook een
hostscope-overzicht en melden subdomeinen die nog op beoordeling wachten.
Historische runs worden niet herschreven.

Schema: `scans/migrations/0003_hostpolicy.py`. Alle wijzigingen zijn via bestaande
projectrechten, POST en CSRF beschermd. De regressiesuite omvat tests voor suffixen,
www-redirects, projectisolatie, blacklistvoorrang, behouden URL-semantiek,
hostontdekking zonder fetch, bestaande configuraties en browserresources.
