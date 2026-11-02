# Labelregister en contentaudit

Labels hebben een afzonderlijk `label_type`: expertise, onderwerp, doelgroep of
functioneel. De bestaande labels krijgen type expertise. De eerdere `kind` blijft
als bronindeling behouden en is niet bepalend voor het labeltype. Het URL-overzicht
toont onder Meer filters een Expertise-dropdown met uitsluitend expertiselabels
van het huidige project. Deze selectie geldt ook voor tellingen, paginering en export.

Per project bevat het labelregister een beheerde lijst expertises, oorspronkelijke
bronlabels en functionele labels. Iedere URL-labelkoppeling is uniek en kan meerdere
bronregels als bewijs hebben. De oorspronkelijke celtekst, URL, tabblad, rij,
importerende gebruiker en importdatum blijven beschikbaar. Samenstellingen worden
niet automatisch gesplitst of vertaald naar primaire/aanvullende expertises.

De GMW-import maakt de vijftien afgesproken expertises aan en verwerkt expliciete
labelkolommen op de zeven relevante auditbladen. Alleen herkenbare juridische of
functionele aanduidingen worden overgenomen; artikeltitels, toelichtingen, placeholders
en tabbladnamen worden niet als classificatie gebruikt. Samengevoegde aanduidingen
uit het bevindingenblad worden op lege regels gescheiden, met behoud van de volledige
oorspronkelijke cel als bewijs. Labels houden status ‘Volgens bron’; dit is geen
inhoudelijke validatie. Een onduidelijke bronwaarde vraagt latere beoordeling.

Gebruik eerst een droge controle en daarna dezelfde opdracht zonder `--dry-run`:

```sh
python manage.py import_audit_labels BATCH_UUID --actor GEBRUIKERSNAAM --dry-run
```

De gebruiker moet bewerkrechten op het project hebben; de bron moet een voltooide
contentaudit zijn. De import koppelt uitsluitend aan exacte genormaliseerde URL’s
uit de projectcatalogus. Geen redirect-, eindslash- of hostvarianten worden geraden.
Niet-gematchte URL’s staan in het resultaat. Herhaald uitvoeren voegt geen dubbele
labels, koppelingen of bewijzen toe en overschrijft bestaande beoordelingen niet.

Het projecttabblad Labels toont het register, gekoppelde URL’s en bronverwijzingen.
URL-dossiers tonen dezelfde koppelingen. Het verwijderen van een bron verwijdert
haar labelbewijzen en alleen onbeoordeelde bronkoppelingen zonder overig bewijs;
beoordeelde koppelingen en het labelregister blijven behouden.
