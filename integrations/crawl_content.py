"""Bounded, compressed transport for private crawl documents."""
import base64
import gzip
from hashlib import sha256
import zlib

MAX_DOCUMENT = 5 * 1024 * 1024
MAX_RENDER_RESPONSE = 24 * 1024 * 1024
VARIANTS = ('server_html', 'rendered_html', 'text')


def pack_content(capture):
    values = {'server_html':capture.server_html, 'rendered_html':capture.rendered_html.encode('utf-8'),
              'text':capture.text.encode('utf-8')}
    result = {}
    for key, value in values.items():
        if len(value)>MAX_DOCUMENT:
            raise ValueError('Document too large')
        result[key] = {'gzip':base64.b64encode(gzip.compress(value,mtime=0)).decode('ascii'),
                       'bytes':len(value),'sha256':sha256(value).hexdigest()}
    return result


def decompress_document(value):
    decoder = zlib.decompressobj(16+zlib.MAX_WBITS)
    try:
        raw = decoder.decompress(bytes(value),MAX_DOCUMENT+1)
    except zlib.error as error:
        raise ValueError('Invalid compressed document') from error
    if len(raw)>MAX_DOCUMENT or not decoder.eof or decoder.unused_data:
        raise ValueError('Invalid or oversized document')
    return raw


def unpack_content(payload,metadata):
    if not isinstance(payload,dict) or set(payload)!=set(VARIANTS):
        raise ValueError('Missing document payload')
    result = {}
    for key in VARIANTS:
        entry=payload[key]
        value=base64.b64decode(entry['gzip'],validate=True)
        if len(value)>MAX_DOCUMENT+4096:
            raise ValueError('Compressed document too large')
        raw=decompress_document(value)
        digest=sha256(raw).hexdigest()
        if len(raw)!=entry['bytes'] or digest!=entry['sha256']:
            raise ValueError('Document checksum mismatch')
        if len(raw)!=metadata.get(key+'_bytes') or digest!=metadata.get(key+'_sha256'):
            raise ValueError('Metadata mismatch')
        if key!='server_html':
            raw.decode('utf-8')
        result[key]=value
    return result
