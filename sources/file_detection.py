import io
import zipfile
from .excel_export import parse_excel, XLS_SIGNATURE
from .screamingfrog import parse_export, MAX_BYTES
from .gsc_export import parse_gsc_zip


def detect_export(upload):
    raw = upload.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Het bestand is te groot. Maximaal 20 MB.')
    if raw.startswith(XLS_SIGNATURE):
        return parse_excel(raw, legacy=True)
    if raw.startswith(b'PK'):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                is_excel = 'xl/workbook.xml' in archive.namelist()
        except zipfile.BadZipFile:
            raise ValueError('ZIP- of Excel-bestand kan niet worden gelezen.') from None
        if is_excel:
            return parse_excel(raw)
        return parse_gsc_zip(raw, upload.name)
    result = parse_export(io.BytesIO(raw))
    return dict(result, source='sf', label='Screaming Frog · Internal URLs', parser_version='sf-internal-all-v1')
