# Benchmarkbestanden na R6

Begin bij [rapport.md](rapport.md), [gap-matrix.md](gap-matrix.md) en [aanbevelingen.md](aanbevelingen.md). CSV-bestanden zijn geschikt voor sorteren/filteren. De snapshot bevat geselecteerde openbare websitewaarnemingen en interne meetreferenties, geen HTML-bodies, authenticatie- of cookiewaarden. Het oorspronkelijke SF-archief wordt niet opnieuw meegeleverd.

Offline opnieuw uitvoeren vanuit de repository, met Python 3 en alleen de standaardbibliotheek:

```bash
python3 reports/benchmarks/2026-11-05-post-r6/benchmark.py \
  --snapshot reports/benchmarks/2026-11-05-post-r6/measurement-snapshot.json.gz \
  --sf-dir /tmp/sem-sf-benchmark/sf-exports/2026.11.04.12.08.48 \
  --sf-archive /home/raymond/data/sf-exports.zip \
  --baseline reports/benchmarks/2026-11-05-post-r6/reference-cohort.csv \
  --output /tmp/sem-post-r6-reproduced
```

Pas de SF-paden aan wanneer het archief elders is uitgepakt. De baseline-CSV bepaalt de vaste cohort van 59 pagina’s; het is geen volledige SF Internal:All-export. Het script maakt geen netwerkaanvragen en wijzigt geen database. Het reproduceert de kwantitatieve tabellen/sets, niet zelfstandig de expertbeoordeling of code-inspectie uit het rapport.

Bewijslagen:

- Inventarisoverlap: doel bestaat in het register. Dit is geen succesvolle meting.
- HTTP-meting: daadwerkelijk antwoord/eindstatus of bewaarde fout/blokkade.
- Controle: interpretatie door de geversioneerde analyser.
- Voorstel: conceptopdracht met context; oorzaak en zakelijke prioriteit vragen beoordeling.

Het rapport benoemt expliciet tegenstrijdigheden tussen deze lagen, waaronder status 999 en voorstelversies. De snapshot bewaart de oorspronkelijke uitkomsten; de aanbevelingen herschrijven die niet.

De bronchecksums staan in comparison-summary.json. De nieuwe crawl is zichtbaar in het bestaande productieproject. Het gecombineerde dossier heeft ID `7397270d-7d11-4f10-ba28-d34732412f07`; toegang vereist bestaande projectrechten.
