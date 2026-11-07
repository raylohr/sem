import io
import zipfile
from .excel_export import parse_excel, XLS_SIGNATURE
from .screamingfrog import MAX_BYTES
from .sf_reports import parse_csv
from .sf_archive import is_crawl_archive, parse_archive
from .gsc_export import parse_gsc_zip, parse_gsc_csv
from .ahrefs import parse_ahrefs


def detect_export(upload):
    raw = upload.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Het bestand is te groot. Maximaal 20 MB.')
    if raw.startswith(XLS_SIGNATURE):
        return parse_excel(raw, legacy=True, filename=upload.name)
    if raw.startswith(b'PK'):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                names = archive.namelist()
                is_excel = 'xl/workbook.xml' in names
                is_crawl = is_crawl_archive(names)
        except zipfile.BadZipFile:
            raise ValueError('ZIP- of Excel-bestand kan niet worden gelezen.') from None
        if is_excel:
            return parse_excel(raw, filename=upload.name)
        if is_crawl:
            return parse_archive(raw)
        return parse_gsc_zip(raw, upload.name)
    gsc = parse_gsc_csv(raw, upload.name)
    if gsc is not None:
        return gsc
    ahrefs = parse_ahrefs(raw, upload.name)
    if ahrefs is not None:
        return ahrefs
    return parse_csv(raw)
