"""Download imported observations without adding audit judgements."""
import csv
import io
import json
import tempfile
import zipfile
from contextlib import ExitStack

from django.http import FileResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell


META = ['url_id', 'normalized_url', 'original_url', 'batch_id', 'source_record_id',
        'source_locator', 'scope', 'measured_date', 'measured_at', 'period_start', 'period_end']


def flatten(value, prefix=''):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            result.update(flatten(item, f'{prefix}.{key}' if prefix else str(key)))
        return result
    if isinstance(value, list):
        value = json.dumps(value, ensure_ascii=False)
    return {prefix: value}


def safe_csv(value):
    # Prevent source strings from being interpreted as spreadsheet formulas.
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def append_excel(sheet, values):
    cells = []
    for value in values:
        if value is not None and not isinstance(value, (str, int, float, bool)):
            value = str(value)
        cell = WriteOnlyCell(sheet, value=value)
        if isinstance(value, str):
            cell.data_type = 's'
        cells.append(cell)
    sheet.append(cells)


def export_archive(project, observations, filters):
    """Spool datasets to disk to keep large exports out of application memory."""
    workbook = Workbook(write_only=True)
    batches = {}
    urls = {}
    groups = {}
    excluded = 0
    with ExitStack() as stack:
        for obs in observations.select_related('url', 'batch', 'source_record').order_by('id').iterator(chunk_size=1000):
            batch = obs.batch
            # Contentaudit workbooks contain previous interpretations, not raw SEO datasets.
            # Preserve their original Ahrefs columns, excluding appended audit annotations.
            data = obs.source_record.data
            sheet = str(data.get('sheet', ''))
            if batch.source == 'audit':
                if not sheet.upper().startswith('AHREFS'):
                    excluded += 1
                    continue
                values = data.get('values', {})
                allowed = {'URL / pad uit bronexport', 'Pad zoals aangeleverd', 'Ref. pages', 'Ref. domains'}
                data = {'sheet': sheet, 'values': {k: v for k, v in values.items()
                        if k in allowed or k.startswith(('Previous ', 'Current ')) or k.endswith(' change')}}
            group = (batch.source, sheet, obs.scope)
            if group not in groups:
                groups[group] = {'file': stack.enter_context(tempfile.TemporaryFile(mode='w+t', encoding='utf-8')),
                                 'keys': {}, 'count': 0}
            item = groups[group]
            raw = flatten(data)
            item['keys'].update(dict.fromkeys(raw))
            meta = [str(obs.url_id), obs.url.normalized_url, obs.original_url, str(batch.pk),
                    obs.source_record_id, obs.source_record.locator, obs.scope,
                    obs.measured_date.isoformat() if obs.measured_date else None,
                    obs.measured_at.isoformat() if obs.measured_at else None,
                    batch.period_start.isoformat() if batch.period_start else None,
                    batch.period_end.isoformat() if batch.period_end else None]
            item['file'].write(json.dumps([meta, raw], ensure_ascii=False) + '\n')
            item['count'] += 1
            urls[str(obs.url_id)] = [str(obs.url_id), obs.url.normalized_url, obs.url.host]
            batches[str(batch.pk)] = [str(batch.pk), batch.source, batch.method,
                batch.label, batch.original_filename, batch.imported_at.isoformat(),
                batch.measured_at.isoformat() if batch.measured_at else None,
                batch.period_start, batch.period_end, batch.status, batch.record_count,
                json.dumps(batch.request_metadata, ensure_ascii=False, default=str),
                json.dumps(batch.warnings, ensure_ascii=False)]
        output = tempfile.TemporaryFile()
        try:
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
                def table(name, headers, rows):
                    excel = workbook.create_sheet(name)
                    excel.freeze_panes = 'A2'
                    append_excel(excel, headers)
                    with archive.open(name + '.csv', 'w') as binary:
                        with io.TextIOWrapper(binary, encoding='utf-8-sig', newline='') as stream:
                            writer = csv.writer(stream)
                            writer.writerow(headers)
                            for row in rows:
                                append_excel(excel, row)
                                writer.writerow([safe_csv(v) for v in row])
                table('URL-register', ['url_id', 'normalized_url', 'host'], urls.values())
                table('Imports', ['batch_id', 'source', 'method', 'label', 'original_filename',
                      'imported_at', 'measured_at', 'period_start', 'period_end', 'status',
                      'batch_record_count', 'import_parameters_json', 'warnings_json'], batches.values())
                manifest = []
                for number, (group, item) in enumerate(groups.items(), 1):
                    name = f'Data-{number:02d}'
                    keys = sorted(item['keys'])
                    item['file'].seek(0)
                    def rows():
                        for line in item['file']:
                            meta, raw = json.loads(line)
                            yield meta + [raw.get(key) for key in keys]
                    table(name, META + ['raw.' + key for key in keys], rows())
                    manifest.append([name, *group, item['count']])
                table('Datasets', ['dataset', 'source', 'source_sheet', 'scope', 'exported_records'], manifest)
                table('Exportinformatie', ['property', 'value'], [
                    ['project_id', str(project.pk)], ['exported_at', timezone.now().isoformat()],
                    ['filters', filters], ['selection', 'Alle metingen die aan de actieve filters voldoen; geen paginering.'],
                    ['excluded_audit_records', excluded],
                    ['audit_policy', 'Auditbeoordelingen uitgesloten; alleen oorspronkelijke Ahrefs-bronkolommen uit auditbestanden.'],
                    ['missing_values', 'Lege waarden blijven leeg; nul is een gemeten bronwaarde.'],
                    ['raw_columns', 'Oorspronkelijke veldnamen met JSON-pad; lijsten als JSON. Geen berekende SEO-scores.'],
                    ['csv_strings', 'Formuleachtige tekst krijgt in CSV een apostrof; Excel bewaart de oorspronkelijke tekst.'],
                    ['coverage', 'Alleen gekoppelde URL-bronrecords uit voltooide en onvolledige imports; geen niet-URL-bronrecords.'],
                    ['subtree', 'Scope subtree bevat paden/substructuren; geen individuele paginametingen.'],
                ])
                xlsx = stack.enter_context(tempfile.TemporaryFile())
                workbook.save(xlsx)
                xlsx.seek(0)
                with archive.open('SEO-brondata.xlsx', 'w') as dest:
                    import shutil
                    shutil.copyfileobj(xlsx, dest)
            output.seek(0)
            return FileResponse(output, as_attachment=True,
                filename=f'seo-brondata-{project.pk}-{timezone.localdate():%Y-%m-%d}.zip',
                content_type='application/zip')
        except Exception:
            output.close()
            raise
