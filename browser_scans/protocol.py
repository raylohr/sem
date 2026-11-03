"""Bounded line-framed protocol over a private Unix-domain socket."""
import json
MAX_FRAME = 32 * 1024 * 1024


def send(stream, value):
    data = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode() + b'\n'
    if len(data) > MAX_FRAME:
        raise ValueError('Browserprotocol overschrijdt de berichtlimiet.')
    remaining = memoryview(data)
    while remaining:
        sent = stream.write(remaining)
        if not sent:
            raise ValueError("Browserverbinding onderbroken.")
        remaining = remaining[sent:]
    stream.flush()


def receive(stream):
    data = stream.readline(MAX_FRAME + 1)
    if not data or len(data) > MAX_FRAME or not data.endswith(b'\n'):
        raise ValueError('Ongeldig of te groot browserbericht.')
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError('Ongeldig browserbericht.')
    return value
