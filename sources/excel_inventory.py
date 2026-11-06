"""Export project URL evidence and empty editorial work fields as a workbook."""
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
    def __init__(self, book, name, headers):
        self.ws = book.create_sheet(name)
        self.headers = headers
        self.count = 0
        self.ws.freeze_panes = 'D2' if 'URL' in headers else 'A2'
        for index, header in enumerate(headers, 1):
            self.ws.column_dimensions[get_column_letter(index)].width = 64 if header == 'URL' else (42 if any(word in header.lower() for word in ('reden', 'titel', 'actie', 'notitie', 'bevinding', 'signaal', 'canonical', 'redirect')) else 24)
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
        self.ws.auto_filter.ref = f'A1:{get_column_letter(len(self.headers))}{self.count}'
        if work_fields:
            for header, choices in [('Prioriteit', 'Hoog,Middel,Laag'), ('Controlestatus', 'Nog te beoordelen,In onderzoek,Gecontroleerd,Afgerond')]:
                column = get_column_letter(self.headers.index(header) + 1)
                validation = DataValidation(type='list', formula1=f'"{choices}"', allow_blank=True)
                validation.errorTitle, validation.error = 'Kies een waarde', 'Gebruik een waarde uit de keuzelijst.'
                validation.showErrorMessage = True
                validation.add(f'{column}2:{column}{min(1048576, max(self.count + 100, 1000))}')
                self.ws.data_validations.append(validation)


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


def build_inventory_workbook(project, user, *, absolute_url, progress=None):
    report = progress or (lambda **kwargs: None)
    report(percent=5, message='URLs verzamelen…')
    columns, rows = project_inventory(project, include_data=True)
    report(percent=20, message=f'{len(rows)} unieke URLs verzameld.')
    sources = [column['key'] for column in columns]
    book = Workbook(write_only=True)
    overview = Sheet(book, '00 - HOOFDOVERZICHT', ['Onderdeel', 'Waarde'])
    headers = ['URL', 'Host', 'Pad'] + [f"{column['label']} laatste waarneming" for column in columns]
    headers += ['Importdatum gebruikt voor bronnen']
    for source, fields in [('gsc', GSC_FIELDS), ('sf', SF_FIELDS), ('ahrefs', AHREFS_FIELDS)]:
        if source in sources:
            headers += fields
    inventory = Sheet(book, '01 - URL INVENTARIS', headers + WORK_FIELDS)
    actions = Sheet(book, '02 - URL ACTIEPLAN', ['URL', 'Host', 'Pad', 'Bronsignalen', *WORK_FIELDS])
    evidence_sheet = Sheet(book, '03 - BRONWAARNEMINGEN', ['URL', 'Bron', 'Rapporttype', 'Waarnemingsdatum', 'Datumgrondslag', 'Importtijd', 'Toegevoegd door', 'Import-ID', 'Importlabel', 'Record-ID', 'Bronrecord', 'GSC rapportcategorie', 'GSC indexeringsreden', 'GSC laatste crawl', 'HTTP-status', 'Indexeerbaarheid', 'Titel', 'Canonical', 'Redirectdoel', 'Ahrefs PR', 'Ahrefs organisch verkeer (schatting)'])
    readme = Sheet(book, 'LEESMIJ', ['Onderwerp', 'Toelichting'])
    batches = {batch.pk: batch for batch in project.import_batches.filter(status__in=('complete', 'incomplete')).select_related('created_by')}
    latest_indexing = {}
    observations = SourceRecord.objects.filter(batch_id__in=batches).order_by('id').values_list('id', 'batch_id', 'data')
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
        if batch.source == 'gsc' and (status or reason):
            rank = (stamp, not fallback, batch.imported_at, record_id)
            if url not in latest_indexing or rank > latest_indexing[url]['rank']:
                latest_indexing[url] = {'date': stamp, 'rank': rank, 'data': data, 'metadata': metadata}
        link = absolute_url(reverse('ui:record', args=[project.pk, batch_id, record_id]))
        evidence_sheet.append([url, DEFAULT_SOURCES.get(batch.source, batch.get_source_display()), metadata.get('type') or metadata.get('kind', 'search_analytics' if batch.source == 'gsc' else ''), stamp,
            'Importdatum; brondatum ontbreekt' if fallback else 'Brondatum', batch.imported_at, batch.created_by.get_username(), str(batch_id), batch.label, record_id, link,
            status, reason, crawl_date, data.get('Status Code', data.get('HTTP status code', '')),
            data.get('Indexability', data.get('Is indexable page', '')), data.get('Title 1', data.get('Title', '')),
            data.get('Canonical Link Element 1', ''), data.get('Redirect URL', ''), data.get('PR', ''), data.get('Organic traffic', '')], links={0: url, 10: link})
    report(percent=65, message=f'URL-inventaris en actieplan vullen: 0 van {len(rows)}…')
    for processed, row in enumerate(rows, 1):
        if processed % 250 == 0 or processed == len(rows):
            report(percent=65 + int(25 * processed / max(len(rows), 1)),
                   message=f'URL-inventaris en actieplan vullen: {processed} van {len(rows)}…')
        values = [row['url'], row['host'], row['display_path']]
        values += [row['observations'].get(source, {}).get('date') for source in sources]
        values += [', '.join(column['label'] for column in columns if row['observations'].get(column['key'], {}).get('fallback'))]
        for source in ('gsc', 'sf', 'ahrefs'):
            if source in sources:
                values += source_values(source, row['observations'].get(source), latest_indexing.get(row['url']))
        inventory.append(values + [''] * len(WORK_FIELDS), links={0: row['url']})
        signals = []
        for source in ('gsc', 'sf', 'ahrefs'):
            signals.extend(source_signals(source, row['observations'].get(source), latest_indexing.get(row['url'])))
        if signals:
            actions.append([row['url'], row['host'], row['display_path'], '\n'.join(signals), *([''] * len(WORK_FIELDS))], links={0: row['url']})
    exported_at = timezone.now()
    for pair in [('Project', project.name), ('Website', project.website), ('Toegestane hosts', ', '.join(project.host_rules['allowed_hosts'])), ('Overige subdomeinen toegestaan', 'Ja' if project.include_subdomains else 'Nee'), ('Exporttijd (Europe/Amsterdam)', exported_at), ('Geëxporteerd door', user.get_username()), ('Unieke URLs', len(rows)), ('Bronnen', ', '.join(column['label'] for column in columns)), ('Bronwaarnemingen', evidence_sheet.count - 1), ('URLs met bronsignalen', actions.count - 1), ('Bereik', 'Alle gepubliceerde URL-records binnen de huidige projecthosts; onafhankelijk van zoekfilter en paginering.')]:
        overview.append(pair)
    for pair in [
        ('Inventaris', 'Eén rij per volledige URL, gesorteerd op pad. Hosts, protocollen en querystrings blijven onderscheiden. Alleen bronnen met data krijgen kolommen.'),
        ('Bronmetingen', 'SF- en Ahrefs-velden komen uit het laatste URL-record van die bron. Er worden geen ontbrekende velden uit oudere records aangevuld. Alle waarnemingen zijn afzonderlijk beschikbaar in tab 03.'),
        ('GSC', 'De algemene GSC-datum kan zoekprestaties betreffen. Indexeringsvelden komen apart uit de laatste indexeringswaarneming. Een GSC-404 is geen actuele HTTP-meting.'),
        ('Datums', 'Echte Excel-datums met dd.mm.jjjj. Brondagen behouden hun brondatum. Importtijden worden weergegeven in Europe/Amsterdam. Ontbrekende brondatums vallen terug op de importdatum; dit staat expliciet vermeld.'),
        ('Actieplan', 'Bevat signalen uit de laatste bronwaarnemingen: HTTP-fouten, ontbrekende HTML-titels en GSC-indexeringsredenen. Een signaal is geen bewezen probleem of automatisch advies; redirects en alternatieve canonicals kunnen correct zijn.'),
        ('Werkvelden', 'Expertise, paginatype, bevinding, actie, prioriteit, controlestatus en notitie zijn leeg voor menselijke beoordeling. Excel-bewerkingen worden niet teruggeschreven naar TrafficStack.'),
        ('Volledigheid', 'Dit is een verzameling aangetroffen URLs, geen bewijs van alle URLs op de website of in Google. GSC-rapporten kunnen voorbeeldlijsten zijn.'),
        ('Herleidbaarheid', 'Tab 03 vermeldt per record de import, gebruiker, datums en een link naar het oorspronkelijke bronrecord. Bronnen kunnen elkaar op verschillende meetmomenten tegenspreken.'),
    ]:
        readme.append(pair)
    for sheet in (overview, inventory, actions, evidence_sheet, readme):
        sheet.finish(work_fields=sheet in (inventory, actions))
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
