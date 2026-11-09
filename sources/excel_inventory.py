"""Export project URL evidence and empty editorial work fields as a workbook."""
import json
from collections import defaultdict
from datetime import datetime
from tempfile import SpooledTemporaryFile

from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from projects.hosts import allows_url
from .models import SourceRecord
from .url_inventory import project_inventory, observation_date, as_date, URL_FIELDS, DEFAULT_SOURCES

WORK_FIELDS = ['Expertise / cluster', 'Paginatype', 'Bevinding', 'Concrete actie', 'Prioriteit', 'Controlestatus', 'Notitie']
GSC_FIELDS = ['GSC indexeringswaarneming', 'GSC rapportcategorie', 'GSC indexeringsreden', 'GSC laatste crawl']
SF_FIELDS = ['SF HTTP-status', 'SF indexeerbaarheid', 'SF canonical', 'SF redirectdoel', 'SF titel', 'SF H1']
AHREFS_FIELDS = ['Ahrefs HTTP-status', 'Ahrefs indexeerbaarheid', 'Ahrefs titel', 'Ahrefs PR', 'Ahrefs organisch verkeer (schatting)']
HEADER_FILL = PatternFill('solid', fgColor='0B2D4D')


class Sheet:
    def __init__(self, book, name, headers, *, groups=None):
        self.ws = book.create_sheet(name)
        self.headers = headers
        self.count = 0
        self.ws.freeze_panes = ('B4' if groups else 'D2') if 'URL' in headers else 'A2'
        self.header_row = 2 if groups else 1
        for index, header in enumerate(headers, 1):
            self.ws.column_dimensions[get_column_letter(index)].width = 64 if header == 'URL' else (42 if any(word in header.lower() for word in ('reden', 'titel', 'actie', 'notitie', 'bevinding', 'signaal', 'canonical', 'redirect')) else 24)
        if groups:
            self.ws.sheet_properties.outlinePr.summaryRight = False
            for start, end in groups['ranges']:
                self.ws.column_dimensions.group(get_column_letter(start), get_column_letter(end), outline_level=1, hidden=False)
            self.append(groups['labels'], header=True)
        self.append(headers, header=True)

    def append(self, values, *, header=False, links=None):
        if self.count >= 1048576:
            raise ValueError('De export overschrijdt de Excel-limiet van 1.048.576 rijen per werkblad.')
        cells = []
        for index, value in enumerate(values):
            cell = WriteOnlyCell(self.ws)
            if isinstance(value, datetime):
                value = timezone.localtime(value).replace(tzinfo=None) if timezone.is_aware(value) else value
            if isinstance(value, str):
                value = ILLEGAL_CHARACTERS_RE.sub('', value)
                if len(value) > 32767:
                    raise ValueError('Een tekstveld is langer dan de Excel-limiet van 32.767 tekens; de export is niet afgekapt.')
                cell.value = value
                cell.data_type = 's'  # Source text, including leading '=', must never become a formula.
            else:
                cell.value = value
            cell.alignment = Alignment(vertical='top', wrap_text=True)
            if header:
                cell.font = Font(color='FFFFFF', bold=True)
                cell.fill = HEADER_FILL
            elif hasattr(value, 'year'):
                cell.number_format = 'dd.mm.yyyy hh:mm' if isinstance(value, datetime) else 'dd.mm.yyyy'
            if links and index in links:
                cell.hyperlink = links[index]
                cell.font = Font(color='0563C1', underline='single')
            cells.append(cell)
        self.ws.append(cells)
        self.count += 1

    def finish(self, *, work_fields=False):
        self.ws.auto_filter.ref = f'A{self.header_row}:{get_column_letter(len(self.headers))}{self.count}'
        if work_fields:
            for header, choices in [('Prioriteit', 'Hoog,Middel,Laag'), ('Controlestatus', 'Nog te beoordelen,In onderzoek,Gecontroleerd,Afgerond')]:
                column = get_column_letter(self.headers.index(header) + 1)
                validation = DataValidation(type='list', formula1=f'"{choices}"', allow_blank=True)
                validation.errorTitle, validation.error = 'Kies een waarde', 'Gebruik een waarde uit de keuzelijst.'
                validation.showErrorMessage = True
                validation.add(f'{column}2:{column}{min(1048576, max(self.count + 100, 1000))}')
                self.ws.data_validations.append(validation)


def add_import_sheets(book, project, *, selected_urls=None):
    """Preserve every stored record and field, with independent file column groups."""
    from .column_fields import scalar_fields, present
    from .inventory_index import url_expression
    from django.db.models import Count, Subquery

    batches = list(project.import_batches.annotate(imported_rows=Count("records")).order_by('source', 'imported_at', 'id'))
    published = {batch.pk: batch for batch in batches if batch.status in ('complete', 'incomplete')}
    records = SourceRecord.objects.filter(batch_id__in=published).order_by('id')
    if selected_urls is not None:
        records = records.annotate(export_url=url_expression()).filter(
            export_url__in=Subquery(selected_urls.order_by().values('url')))
    fields = defaultdict(dict)
    counts = defaultdict(lambda: defaultdict(int))
    stats = defaultdict(lambda: {'rows': 0, 'urls': set(), 'missing': 0})
    for batch_id, data in records.values_list('batch_id', 'data').iterator(chunk_size=1000):
        url = next((data.get(key) for key in URL_FIELDS if data.get(key)), None)
        stat = stats[batch_id]
        stat['rows'] += 1
        if isinstance(url, str) and url.strip():
            stat['urls'].add(url)
        else:
            stat['missing'] += 1
        for path, value in scalar_fields(data):
            fields[batch_id].setdefault(path, len(fields[batch_id]))
            if present(value):
                counts[batch_id][path] += 1

    for source in sorted({batch.source for batch in published.values()}):
        imports = [batch for batch in batches if batch.pk in published and batch.source == source]
        headers, groups, counters = ['URL'], ['Importbestand'], [len(set().union(*(stats[b.pk]['urls'] for b in imports)))]
        offsets = {}
        for batch in imports:
            paths = fields[batch.pk]
            if not paths:
                continue
            offsets[batch.pk] = len(headers)
            headers.extend(' → '.join(path) for path in paths)
            groups.extend([f'{batch.label} [{batch.pk}]'] + [''] * (len(paths) - 1))
            counters.extend(counts[batch.pk][path] for path in paths)
        if len(headers) > 16384:
            raise ValueError('Een brontab overschrijdt de Excel-limiet van 16.384 kolommen.')
        label = {'sf': 'Screaming Frog', 'gsc': 'Google Search Console', 'ahrefs': 'Ahrefs'}.get(source, source)
        ranges = [(offsets[b.pk] + 1, offsets[b.pk] + len(fields[b.pk])) for b in imports if b.pk in offsets]
        sheet = Sheet(book, label, headers, groups={'labels': groups, 'ranges': ranges})
        sheet.append(counters)
        for batch_id, data in records.filter(batch__source=source).values_list('batch_id', 'data').iterator(chunk_size=1000):
            url = next((data.get(key) for key in URL_FIELDS if data.get(key)), '')
            values = [''] * len(headers)
            values[0] = url if isinstance(url, str) else ''
            for path, value in scalar_fields(data):
                values[offsets[batch_id] + fields[batch_id][path]] = value
            sheet.append(values)
        sheet.finish()

    summary = Sheet(book, 'Import Summary', ['Bron', 'Bestand / importlabel', 'Import-ID', 'Importdatum',
        'Rijen in export', 'Velden', 'Unieke URLs', 'Ontbrekende URLs', 'Status', 'Waarschuwingen', 'Geïmporteerde rijen'])
    for batch in batches:
        stat = stats[batch.pk]
        summary.append([batch.get_source_display(), batch.label, str(batch.pk), batch.imported_at,
            stat['rows'], len(fields[batch.pk]), len(stat['urls']), stat['missing'], batch.get_status_display(),
            json.dumps(batch.warnings, ensure_ascii=False) if batch.warnings else batch.error, batch.imported_rows])
    summary.finish()
    return summary.ws


def gsc_evidence(data, metadata):
    inspection = data.get('raw', {}).get('indexStatusResult', {}) if data.get('kind') == 'inspection' else {}
    reason = data.get('gsc_indexing_reason') or metadata.get('indexing_reason') or inspection.get('coverageState', '')
    status = data.get('gsc_report_status') or metadata.get('report_status') or inspection.get('verdict', '')
    stamp = as_date(data.get('Last crawled') or inspection.get('lastCrawlTime'))
    if stamp and stamp.year == 1970:
        stamp = None
    return status, reason, stamp


def source_values(source, observation, indexing):
    data = observation['data'] if observation else {}
    if source == 'gsc':
        if not indexing:
            return [None, '', '', None]
        return [indexing['date'], *gsc_evidence(indexing['data'], indexing['metadata'])]
    if source == 'sf':
        return [data.get(key, '') for key in ('Status Code', 'Indexability', 'Canonical Link Element 1', 'Redirect URL', 'Title 1', 'H1-1')]
    if source == 'ahrefs':
        return [data.get(key, '') for key in ('HTTP status code', 'Is indexable page', 'Title', 'PR', 'Organic traffic')]
    return []


def source_signals(source, observation, indexing):
    evidence = indexing if source == 'gsc' else observation
    if not evidence:
        return []
    data = evidence['data']
    if source == 'gsc':
        status, reason, _ = gsc_evidence(data, evidence['metadata'])
        return ['GSC: ' + reason] if reason and status not in ('indexed', 'PASS') else []
    status = data.get('Status Code' if source == 'sf' else 'HTTP status code', '')
    try:
        code = int(float(status))
    except (TypeError, ValueError):
        code = None
    label = DEFAULT_SOURCES.get(source, source)
    signals = [f'{label}: HTTP {status}'] if code is not None and (code >= 400 or code == 0) else []
    title_key = 'Title 1' if source == 'sf' else 'Title'
    content_type = data.get('Content Type') or data.get('Content type', '')
    if content_type.lower().startswith(('text/html', 'application/xhtml+xml')) and title_key in data and not data[title_key].strip():
        signals.append(f'{label}: ontbrekende titel')
    return signals


def build_inventory_workbook(project, user, *, absolute_url, progress=None, filter_params=None):
    report = progress or (lambda **kwargs: None)
    report(percent=5, message='URLs verzamelen…')
    from .inventory_index import Inventory, InventoryRows, url_expression
    from .inventory_query import DatabaseRows
    from .configurable_inventory import selected_columns, build_dataset_table, legacy_params
    from .models import InventoryPreference
    inventory_index = Inventory(project)
    catalog = inventory_index.catalog
    database_rows = DatabaseRows(inventory_index)
    columns = inventory_index.source_columns()
    sources = [column['key'] for column in columns]
    configured = selected_columns(project, user, catalog, database_rows) if InventoryPreference.objects.filter(project=project, user=user).exists() else []
    query = inventory_index.query
    if filter_params is not None:
        visible = selected_columns(project, user, catalog, database_rows)
        filtered, controls = build_dataset_table(database_rows, visible, legacy_params(filter_params, visible, database_rows), catalog)
        if controls['table_errors']: raise ValueError('; '.join(controls['table_errors']))
        query = filtered.query
    rows = InventoryRows(query, {column['field_id'] for column in configured}, include_data=True)
    total_rows = rows.count()
    report(percent=20, message=f'{total_rows} unieke URLs verzameld.')
    from .column_fields import typed_value
    book = Workbook(write_only=True)
    overview = Sheet(book, '00 - HOOFDOVERZICHT', ['Onderdeel', 'Waarde'])
    headers = ['URL', 'Host', 'Pad'] + [f"{column['label']} laatste waarneming" for column in columns]
    headers += ['Importdatum gebruikt voor bronnen']
    for source, fields in [('gsc', GSC_FIELDS), ('sf', SF_FIELDS), ('ahrefs', AHREFS_FIELDS)]:
        if source in sources:
            headers += fields
    headers += [f"{column['name']} ({column['source_label']} / {column['report_label']})" for column in configured]
    inventory = Sheet(book, '01 - URL INVENTARIS', headers + WORK_FIELDS)
    actions = Sheet(book, '02 - URL ACTIEPLAN', ['URL', 'Host', 'Pad', 'Bronsignalen', *WORK_FIELDS])
    evidence_sheet = Sheet(book, '03 - BRONWAARNEMINGEN', ['URL', 'Bron', 'Rapporttype', 'Waarnemingsdatum', 'Datumgrondslag', 'Importtijd', 'Toegevoegd door', 'Import-ID', 'Importlabel', 'Record-ID', 'Bronrecord', 'GSC rapportcategorie', 'GSC indexeringsreden', 'GSC laatste crawl', 'HTTP-status', 'Indexeerbaarheid', 'Titel', 'Canonical', 'Redirectdoel', 'Ahrefs PR', 'Ahrefs organisch verkeer (schatting)'])
    readme = Sheet(book, 'LEESMIJ', ['Onderwerp', 'Toelichting'])
    batches = {batch.pk: batch for batch in project.import_batches.filter(status__in=('complete', 'incomplete')).select_related('created_by')}
    from django.db.models import Subquery
    observations = SourceRecord.objects.filter(batch_id__in=batches).annotate(inventory_address=url_expression()).filter(
        inventory_address__in=Subquery(query.order_by().values('url'))).order_by('id').values_list('id', 'batch_id', 'data')
    total_records = observations.count()
    report(percent=25, message=f'Bronwaarnemingen verwerken: 0 van {total_records}…')
    for processed, (record_id, batch_id, data) in enumerate(observations.iterator(chunk_size=1000), 1):
        if processed % 250 == 0 or processed == total_records:
            report(percent=25 + int(35 * processed / max(total_records, 1)),
                   message=f'Bronwaarnemingen verwerken: {processed} van {total_records}…')
        url = next((data.get(key) for key in URL_FIELDS if data.get(key)), None)
        if not isinstance(url, str) or not allows_url(project, url):
            continue
        batch = batches[batch_id]
        stamp, fallback = observation_date(data, batch)
        metadata = batch.request_metadata
        status, reason, crawl_date = gsc_evidence(data, metadata) if batch.source == 'gsc' else ('', '', None)
        link = absolute_url(reverse('ui:record', args=[project.pk, batch_id, record_id]))
        evidence_sheet.append([url, DEFAULT_SOURCES.get(batch.source, batch.get_source_display()), metadata.get('type') or metadata.get('kind', 'search_analytics' if batch.source == 'gsc' else ''), stamp,
            'Importdatum; brondatum ontbreekt' if fallback else 'Brondatum', batch.imported_at, batch.created_by.get_username(), str(batch_id), batch.label, record_id, link,
            status, reason, crawl_date, data.get('Status Code', data.get('HTTP status code', '')),
            data.get('Indexability', data.get('Is indexable page', '')), data.get('Title 1', data.get('Title', '')),
            data.get('Canonical Link Element 1', ''), data.get('Redirect URL', ''), data.get('PR', ''), data.get('Organic traffic', '')], links={0: url, 10: link})
    report(percent=65, message=f'URL-inventaris en actieplan vullen: 0 van {total_rows}…')
    for processed, row in enumerate(rows, 1):
        if processed % 250 == 0 or processed == total_rows:
            report(percent=65 + int(25 * processed / max(total_rows, 1)),
                   message=f'URL-inventaris en actieplan vullen: {processed} van {total_rows}…')
        values = [row['url'], row['host'], row['display_path']]
        values += [row['observations'].get(source, {}).get('date') for source in sources]
        values += [', '.join(column['label'] for column in columns if row['observations'].get(column['key'], {}).get('fallback'))]
        for source in ('gsc', 'sf', 'ahrefs'):
            if source in sources:
                values += source_values(source, row['observations'].get(source), row.get('_indexing'))
        values += [typed_value(row.get('field_values', {}).get(column['field_id'], {}).get('value'), column['type']) for column in configured]
        inventory.append(values + [''] * len(WORK_FIELDS), links={0: row['url']})
        signals = []
        for source in ('gsc', 'sf', 'ahrefs'):
            signals.extend(source_signals(source, row['observations'].get(source), row.get('_indexing')))
        if signals:
            actions.append([row['url'], row['host'], row['display_path'], '\n'.join(signals), *([''] * len(WORK_FIELDS))], links={0: row['url']})
    exported_at = timezone.now()
    for pair in [('Project', project.name), ('Website', project.website), ('Toegestane hosts', ', '.join(project.host_rules['allowed_hosts'])), ('Overige subdomeinen toegestaan', 'Ja' if project.include_subdomains else 'Nee'), ('Exporttijd (Europe/Amsterdam)', exported_at), ('Geëxporteerd door', user.get_username()), ('Unieke URLs', total_rows), ('Bronnen', ', '.join(column['label'] for column in columns)), ('Bronwaarnemingen', evidence_sheet.count - 1), ('URLs met bronsignalen', actions.count - 1), ('Bereik', 'Huidige zoek- en filterselectie, over alle tabelpagina’s.' if filter_params is not None else 'Alle gepubliceerde URL-records binnen de huidige projecthosts; onafhankelijk van zoekfilter en paginering.')]:
        overview.append(pair)
    for pair in [
        ('Inventaris', 'Eén rij per volledige URL, gesorteerd op pad. Hosts, protocollen en querystrings blijven onderscheiden. Alleen bronnen met data krijgen kolommen.'),
        ('Bronmetingen', 'SF- en Ahrefs-velden komen uit het laatste URL-record van die bron. Er worden geen ontbrekende velden uit oudere records aangevuld. Alle waarnemingen zijn afzonderlijk beschikbaar in tab 03.'),
        ('GSC', 'De algemene GSC-datum kan zoekprestaties betreffen. Indexeringsvelden komen apart uit de laatste indexeringswaarneming. Een GSC-404 is geen actuele HTTP-meting.'),
        ('Datums', 'Echte Excel-datums met dd.mm.jjjj. Brondagen behouden hun brondatum. Importtijden worden weergegeven in Europe/Amsterdam. Ontbrekende brondatums vallen terug op de importdatum; dit staat expliciet vermeld.'),
        ('Actieplan', 'Bevat signalen uit de laatste bronwaarnemingen: HTTP-fouten, ontbrekende HTML-titels en GSC-indexeringsredenen. Een signaal is geen bewezen probleem of automatisch advies; redirects en alternatieve canonicals kunnen correct zijn.'),
        ('Werkvelden', 'Expertise, paginatype, bevinding, actie, prioriteit, controlestatus en notitie zijn leeg voor menselijke beoordeling. Excel-bewerkingen worden niet teruggeschreven naar TrafficStack.'),
        ('Brontabs', 'Elke bron heeft een eigen tab met alle opgeslagen importvelden, inclusief geneste velden. Elke import heeft een inklapbare kolomgroep; afzonderlijke records blijven afzonderlijke rijen. Rij 3 telt gevulde waarden (ook 0 en false); de URL-teller telt unieke URLs. Bij een volledige export bevatten deze tabs ook records zonder URL of buiten de huidige projecthosts.'),
        ('Import Summary', 'De laatste tab toont alle imports en hun status. Rijen, velden en URL-tellingen beschrijven de exportselectie; geïmporteerde rijen telt alle opgeslagen records per import. Bij een gefilterde export bevatten brontabs alleen records met een geselecteerde URL.'),
        ('Volledigheid', 'Dit is een verzameling aangetroffen URLs, geen bewijs van alle URLs op de website of in Google. GSC-rapporten kunnen voorbeeldlijsten zijn.'),
        ('Herleidbaarheid', 'Tab 03 vermeldt per record de import, gebruiker, datums en een link naar het oorspronkelijke bronrecord. Bronnen kunnen elkaar op verschillende meetmomenten tegenspreken.'),
    ]:
        readme.append(pair)
    for sheet in (overview, inventory, actions, evidence_sheet, readme):
        sheet.finish(work_fields=sheet in (inventory, actions))
    summary = add_import_sheets(book, project, selected_urls=query if filter_params is not None else None)
    first = overview.ws
    book._sheets = [first] + sorted([ws for ws in book.worksheets if ws not in (first, summary)], key=lambda ws: ws.title.casefold()) + [summary]
    report(percent=95, message='Excelbestand opslaan…')
    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode='w+b')
    try:
        book.save(output)
        output.seek(0)
        report(percent=100, message='Excelbestand gereed.', state='ready')
        return output
    except Exception:
        output.close()
        raise
