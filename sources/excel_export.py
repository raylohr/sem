"""Read Screaming Frog Excel exports as values, using the existing row validator."""
import csv
import hashlib
import io
from datetime import date, datetime, time
import zipfile

from .screamingfrog import MAX_BYTES, MAX_ROWS, REQUIRED, parse_export

XLS_SIGNATURE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
MAX_COLUMNS = 512
MAX_SHEETS = 20


def cell_text(value):
    if value is None:
        return ''
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def parse_excel(raw, *, legacy=False):
    workbook = None
    try:
        if legacy:
            import xlrd
            workbook = xlrd.open_workbook(file_contents=raw, on_demand=True)
            if workbook.nsheets > MAX_SHEETS:
                raise ValueError('Maximaal 20 werkbladen per bestand.')

            def sheets():
                for name in workbook.sheet_names():
                    sheet = workbook.sheet_by_name(name)
                    if sheet.ncols > MAX_COLUMNS or sheet.nrows > MAX_ROWS + 1:
                        raise ValueError('Excel-bestand overschrijdt de limiet van 100.000 rijen of 512 kolommen.')
                    def rows(sheet=sheet):
                        for row in sheet.get_rows():
                            yield [cell_text(xlrd.xldate_as_datetime(cell.value, workbook.datemode))
                                   if cell.ctype == xlrd.XL_CELL_DATE else cell_text(cell.value) for cell in row]
                    yield name, rows()
        else:
            import openpyxl
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if len(archive.infolist()) > 1000 or sum(e.file_size for e in archive.infolist()) > MAX_BYTES:
                    raise ValueError('Excel-bestand bevat te veel uitgepakte data (maximaal 20 MB).')
            workbook = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True, keep_links=False)
            if len(workbook.worksheets) > MAX_SHEETS:
                raise ValueError('Maximaal 20 werkbladen per bestand.')

            def sheets():
                for sheet in workbook.worksheets:
                    if (sheet.max_column or 0) > MAX_COLUMNS or (sheet.max_row or 0) > MAX_ROWS + 1:
                        raise ValueError('Excel-bestand overschrijdt de limiet van 100.000 rijen of 512 kolommen.')
                    yield sheet.title, ([cell_text(value) for value in row] for row in sheet.iter_rows(values_only=True))
        result = None
        for name, rows in sheets():
            headers = next(rows, [])
            if not REQUIRED.issubset(headers):
                continue
            if result is not None:
                raise ValueError('Meerdere Screaming Frog URL-werkbladen gevonden. Exporteer één Internal → All-werkblad per bestand.')
            stream = io.StringIO(newline='')
            writer = csv.writer(stream)
            writer.writerow(headers)
            count = 0
            for row in rows:
                if not any(row):
                    continue
                count += 1
                if count > MAX_ROWS:
                    raise ValueError('Maximaal 100.000 URLs per import.')
                writer.writerow(row)
                if stream.tell() > MAX_BYTES:
                    raise ValueError('Excel bevat te veel tabeldata (maximaal 20 MB).')
            result = parse_export(io.BytesIO(stream.getvalue().encode('utf-8')))
            result['summary'].update(worksheet=name, file_format='xls' if legacy else 'xlsx')
        if result is None:
            raise ValueError('Geen Screaming Frog Internal → All-werkblad herkend. Controleer de exportkolommen.')
        result['checksum'] = hashlib.sha256(raw).hexdigest()
        return dict(result, source='sf', label='Screaming Frog · Internal URLs', parser_version='sf-excel-internal-all-v1')
    except ValueError:
        raise
    except Exception:
        raise ValueError('Excel-bestand kan niet worden gelezen. Gebruik een onbeschermde Screaming Frog-export als XLS of XLSX.') from None
    finally:
        if workbook is not None:
            if legacy:
                workbook.release_resources()
            else:
                workbook.close()
