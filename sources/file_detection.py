import io
from .screamingfrog import parse_export, MAX_BYTES
from .gsc_export import parse_gsc_zip


def detect_export(upload):
    raw = upload.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Het bestand is te groot. Maximaal 20 MB.')
    if raw.startswith(b'PK'):
        return parse_gsc_zip(raw, upload.name)
    result = parse_export(io.BytesIO(raw))
    return dict(result, source='sf', label='Screaming Frog · Internal URLs', parser_version='sf-internal-all-v1')
