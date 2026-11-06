"""Bounded, read-only parsers. Original values and sheet/row provenance survive."""
import csv
import datetime
import hashlib
import io
import json
import math
from pathlib import Path
from zipfile import ZipFile, BadZipFile

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 100_000
MAX_CELLS = 500_000
MAX_COLUMNS = 256
PARSER_VERSION = "tabular-v1"


class ImportFormatError(ValueError):
    pass


def clean_value(value):
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def load_sheets(content, filename):
    extension = Path(filename).suffix.lower()
    if not content or len(content) > MAX_BYTES:
        raise ImportFormatError("Bestand is leeg of groter dan 20 MiB.")
    if extension == ".xlsx":
        import openpyxl
        try:
            with ZipFile(io.BytesIO(content)) as archive:
                if sum(item.file_size for item in archive.infolist()) > 80 * 1024 * 1024:
                    raise ImportFormatError("Uitgepakte werkmap is te groot.")
                if len(archive.infolist()) > 1000:
                    raise ImportFormatError("Te veel onderdelen in werkmap.")
            book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True, keep_links=False)
        except ImportFormatError:
            raise
        except Exception as exc:
            raise ImportFormatError("Ongeldig XLSX-bestand.") from exc
        try:
            yield from ((sheet.title, sheet.iter_rows(values_only=True)) for sheet in book)
        finally:
            book.close()
    elif extension == ".xls":
        import xlrd
        try:
            book = xlrd.open_workbook(file_contents=content, on_demand=True)
        except Exception as exc:
            raise ImportFormatError("Ongeldig XLS-bestand.") from exc
        try:
            for sheet in book.sheets():
                def rows(sheet=sheet):
                    for i in range(sheet.nrows):
                        values = []
                        for cell in sheet.row(i):
                            if cell.ctype == xlrd.XL_CELL_DATE:
                                values.append(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode))
                            else:
                                values.append(cell.value)
                        yield values
                yield sheet.name, rows()
        finally:
            book.release_resources()
    elif extension in {".csv", ".tsv"}:
        try:
            text = content.decode("utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
        except UnicodeError as exc:
            raise ImportFormatError("Gebruik UTF-8 of UTF-16 tekstcodering.") from exc
        if "\x00" in text:
            raise ImportFormatError("Geen geldig tekstbestand.")
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",\t;")
            reader = csv.reader(io.StringIO(text, newline=""), dialect)
        except csv.Error:
            reader = csv.reader(io.StringIO(text, newline=""), delimiter="\t" if extension == ".tsv" else ",")
        yield "CSV", reader
    else:
        raise ImportFormatError("Ondersteund: XLS, XLSX, CSV en TSV.")


def parse_file(content, filename):
    records, sheets, warnings = [], [], []
    cell_count = 0
    signature = hashlib.sha256()
    source = None
    gsc_problem = None
    has_epoch = False
    try:
        for title, iterator in load_sheets(content, filename):
            if len(sheets) >= 100:
                raise ImportFormatError("Maximaal 100 tabbladen toegestaan.")
            iterator = iter(iterator)
            raw_header = next(iterator, None)
            if raw_header is None:
                sheets.append({"name": title, "columns": [], "rows": 0})
                continue
            headers = [str(clean_value(v) or "").strip() for v in raw_header]
            if len(headers) > MAX_COLUMNS:
                raise ImportFormatError("Te veel kolommen.")
            if not any(headers):
                raise ImportFormatError("De eerste rij bevat geen kolomkoppen.")
            keys = [f"{h or 'kolom'} [{i+1}]" if not h or headers.count(h) > 1 else h for i, h in enumerate(headers)]
            header_set = set(headers)
            if {"Address", "Status Code"} <= header_set or "HTML hreflang 1" in header_set or "Unique JS Inlinks" in header_set:
                source = source or "screaming_frog"
            if {"PR", "URL", "HTTP status code"} <= header_set or {"Page URL", "Links to target"} <= header_set:
                source = source or "ahrefs"
            if title == "Metadata" and headers == ["Property", "Waarde"]:
                source = "gsc"
            summary = {"name": title, "columns": headers, "rows": 0}
            sheets.append(summary)
            signature.update(json.dumps([title, headers], ensure_ascii=False).encode())
            for rownum, raw in enumerate(iterator, start=2):
                if rownum > MAX_ROWS + 1:
                    raise ImportFormatError("Tabblad overschrijdt de rijlimiet.")
                values = [clean_value(v) for v in raw]
                if not any(v is not None and v != "" for v in values):
                    continue
                cell_count += len(values)
                if len(records) >= MAX_ROWS or cell_count > MAX_CELLS or len(values) > MAX_COLUMNS:
                    raise ImportFormatError("Bestand overschrijdt de rij- of kolomlimiet.")
                if len(values) > len(keys) and any(v is not None and v != "" for v in values[len(keys):]):
                    raise ImportFormatError(f"Meer waarden dan kolommen in {title}, rij {rownum}.")
                data = dict(zip(keys, values))
                # Raw source values remain unchanged; unknown dates are only normalized in a separate field.
                unknown_dates = [key for key, value in data.items() if isinstance(value, str) and value.startswith("1970-01-01")]
                has_epoch = has_epoch or bool(unknown_dates)
                record = {"locator": f"sheet:{len(sheets)}:row:{rownum}", "data": {"sheet": title, "row": rownum, "values": data}}
                if unknown_dates:
                    record["data"]["unknown_date_columns"] = unknown_dates
                records.append(record)
                summary["rows"] += 1
                signature.update(json.dumps(record, sort_keys=True, ensure_ascii=False, default=str).encode())
                if title == "Metadata" and data.get("Property") == "Probleem":
                    gsc_problem = data.get("Waarde")
    except ImportFormatError:
        raise
    except Exception as exc:
        raise ImportFormatError("Het bestand kan niet veilig als tabel worden gelezen.") from exc
    if source is None:
        raise ImportFormatError("Rapporttype niet herkend. Gebruik een GSC-, Ahrefs- of Screaming Frog-export.")
    if not records:
        warnings.append("Lege export: nul records is geen bewijs dat de hele website probleemvrij is.")
    if has_epoch:
        warnings.append("Datums op 1970-01-01 worden als onbekend gemarkeerd; oorspronkelijke waarden blijven behouden.")
    if source == "gsc":
        table = next((s for s in sheets if s["name"] == "Tabel"), None)
        chart = [r["data"]["values"] for r in records if r["data"]["sheet"] == "Diagram"]
        if table and chart:
            latest = chart[-1].get("Betreffende pagina's")
            if isinstance(latest, (float, int)) and latest > table["rows"]:
                warnings.append(f"Onvolledige URL-lijst: {table['rows']} detailregels tegenover {int(latest)} pagina's in het laatste diagrammeetpunt.")
    warnings.append("Bronmetingen zijn historische observaties; geen actuele websitecontrole.")
    return {"source": source, "records": records, "sheets": sheets, "warnings": warnings,
            "fingerprint": signature.hexdigest(), "problem": gsc_problem}
