"""Bounded response-body measurements, distinct encoded/decoded byte semantics."""
import zlib

SAFE_HEADERS=('content-type','content-range','content-length','content-encoding','transfer-encoding','cache-control','expires','etag','last-modified','vary','content-disposition','location','x-content-type-options')


def read_body(client,response,headers):
    limit=min(client.resource_body_limit,max(0,client.max_transfer-client.decoded_bytes))
    declared=headers.get('content-length','')
    declared=int(declared) if declared.isascii() and declared.isdigit() and len(declared)<20 else None
    raw=bytearray();complete=False
    while len(raw)<limit and client.transferred<client.max_transfer:
        client.guard()
        chunk=response.read(min(65536,limit-len(raw),client.max_transfer-client.transferred),decode_content=False)
        if not chunk:complete=True;break
        raw.extend(chunk);client.transferred+=len(chunk)
    if declared is not None and declared==len(raw):complete=True
    encoding=headers.get('content-encoding','identity').strip().lower()
    body=bytes(raw);decoded_complete=complete;error=''
    if encoding=='gzip' or body.startswith(b'\x1f\x8b'):
        try:
            decoder=zlib.decompressobj(16+zlib.MAX_WBITS)
            body=decoder.decompress(body,limit+1)
            decoded_complete=complete and decoder.eof and not decoder.unused_data and len(body)<=limit
            if not decoded_complete:error='incomplete_gzip'
        except zlib.error:
            body=b'';decoded_complete=False;error='invalid_gzip'
    elif encoding not in ('','identity'):
        body=b'';decoded_complete=False;error='unsupported_encoding'
    decoded=len(body);body=body[:limit]
    client.decoded_bytes+=len(body)
    return body,{'declared_bytes':declared,'wire_bytes':len(raw),'decoded_bytes':len(body),
        'wire_complete':complete,'representation_complete':complete and getattr(response,'status',200)==200,'body_complete':decoded_complete and decoded<=limit,
        'body_truncated':not complete or decoded>limit,'body_error':error}
