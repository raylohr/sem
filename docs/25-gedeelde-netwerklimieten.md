# Gedeelde netwerklimieten

`concurrency` begrenst actieve HTTP-verzoeken binnen één onderzoek (standaard 1, maximaal 16). `requests_per_second` begrenst nieuwe verzoeken per host (standaard 1, maximaal 15). Het zijn bovengrenzen, geen gegarandeerde snelheid. Wachttijd, robotsbeleid, time-outs en automatische vertraging kunnen het tempo verlagen.

Dezelfde configuratie geldt voor HTML-pagina’s, sitemaps, robots.txt, bestanden, CSS-afhankelijkheden, externe linkbestemmingen, browserdownloads en het ophalen van beleidsdocumenten. Redirects en herpogingen komen opnieuw langs dezelfde begrenzers. Gegevensanalyse op reeds opgeslagen pagina’s en DNS-verrijking zijn geen HTTP-downloads en gebruiken geen requests/s-instelling. Browserpagina’s worden nog steeds één voor één onderzocht; bestanden binnen die pagina laden parallel.

Het onderzoek deelt Redis-verzoekslots met zijn browserfase, herdownloads en gerichte HTML-vervolgmetingen via `network_scope`. Elke actieve aanvraag heeft een lease die wordt vernieuwd. Vrijgeven gebeurt ook bij een fout; na een workercrash vervalt een lease binnen drie minuten. Bij een onbereikbare begrenzer worden geen nieuwe aanvragen toegelaten. Hostpacing en overbelastingspauzes gelden ook over onderzoeken heen. Een trage host blokkeert het ophalen van robotsbeleid van andere hosts niet.

Bestanden en externe bestemmingen hebben begrensde threadpools. De eigenaar verwerkt resultaten, CSS-ontdekking en SQLite-checkpoints; netwerkthreads schrijven niet naar die checkpointdatabase. Dode/in-flight verzoeken blijven expliciet onbekend in plaats van onzichtbaar opnieuw te worden opgehaald. Bodybudgetten worden bij parallel lezen gereserveerd en ongebruikte bytes teruggegeven.

Chromium gebruikt de async Playwright-API en genummerde IPC-verzoeken. De broker correleert antwoorden, ook als ze in een andere volgorde terugkomen. Chromium behoudt zijn native sandbox en `network=none`; publieke DNS-validatie, IP-pinning, GET-beleid, privacyredactie en byte-/tijdlimieten blijven van toepassing.

Nieuwe browserfasen nemen beide netwerkwaarden over uit de broncrawl. Het voormalige aparte browsertempo is uit de crawlinterface verwijderd. Bestaande opgeslagen batches en browserruns worden niet herschreven. Nieuwe crawls blijven de opgeslagen projectinstellingen gebruiken; browserpagina-aantal en rendertime-out blijven apart instelbaar.

Validatie: `scans.test_network_limits` controleert gedeelde slots, per-hostpacing, parallelle bestanden/externe doelen en bytebudgetten. `ops/checks/network_limits_browser_probe.py` gebruikt echt Chromium en synthetische HTTP-antwoorden om concurrency 1/3, hostpacing, uit-volgorde-antwoorden en veiligheidsblokkades te controleren.
