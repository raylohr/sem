"""URL validation for Search Console property boundaries."""
import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit


def normalize_url(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 8192 or "\\" in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return None
        host = parsed.hostname.rstrip(".").lower()
        try:
            host = str(ipaddress.ip_address(host))
        except ValueError:
            host = host.encode("idna").decode("ascii")
            if len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
                return None
        authority = f"[{host}]" if ":" in host else host
        port = parsed.port
        scheme = parsed.scheme.lower()
        if port is not None and port != (443 if scheme == "https" else 80):
            authority += f":{port}"
        normalized = urlunsplit((scheme, authority, parsed.path or "/", parsed.query, ""))
        return normalized, host
    except (ValueError, UnicodeError):
        return None

