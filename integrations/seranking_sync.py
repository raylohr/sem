"""Compare complete report contents independently of response row ordering."""
import json
from hashlib import sha256


def content_digest(rows):
    values = sorted(json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=False) for row in rows)
    digest = sha256()
    for value in values:
        digest.update(value.encode())
        digest.update(b'\n')
    return digest.hexdigest()
