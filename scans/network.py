"""Public-only, DNS-pinned HTTP transport. Never uses proxy environment variables."""
import gzip
import io
import ipaddress
import re
import socket
import ssl
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit, urljoin, quote
from urllib.robotparser import RobotFileParser
import certifi
import urllib3
from django.utils import timezone

USER_AGENT = "LohrSEM/0.1"
MAX_BODY = 5 * 1024 * 1024
MAX_TRANSFER = 25 * 1024 * 1024


class ScanError(ValueError):
    pass


class ScanBudgetExceeded(ScanError):
    pass


class RobotsDenied(ScanError):
    pass


def public_ip(value):
    ip = ipaddress.ip_address(value)
    if ip.version == 6 and (ip.is_site_local or ip.ipv4_mapped or ip.sixtofour or ip.teredo):
        return False
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local)


def normalize_host(host):
    if not isinstance(host, str) or not host or any(c in host for c in "%/@:*[]\\"):
        # IPv6 literals are handled separately below; host lists use bare hostnames.
        try:
            if public_ip(host):
                return str(ipaddress.ip_address(host))
        except (ValueError, TypeError):
            pass
        raise ScanError("Ongeldige of niet-publieke host.")
    host = host.rstrip(".").lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ScanError("Ongeldige hostnaam.") from exc
        if len(host) > 253 or "." not in host or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
            raise ScanError("Gebruik een volledige publieke hostnaam.")
        if host.endswith((".localhost", ".local", ".internal", ".home", ".test", ".invalid")):
            raise ScanError("Interne hostnamen zijn niet toegestaan.")
    else:
        if not public_ip(host):
            raise ScanError("Interne netwerkadressen zijn niet toegestaan.")
    return host


def normalize_url(value, allowed_hosts=None):
    if not isinstance(value, str) or len(value) > 4096 or "\\" in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ScanError("Ongeldige URL.")
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ScanError("Gebruik HTTP(S) zonder gebruikersnaam of wachtwoord.")
        host = normalize_host(parts.hostname)
        default_port = 443 if parts.scheme == "https" else 80
        if parts.port not in (None, default_port):
            raise ScanError("Alleen standaard HTTP(S)-poorten zijn toegestaan.")
    except (ValueError, UnicodeError) as exc:
        raise ScanError("Ongeldige URL of poort.") from exc
    if allowed_hosts is not None and host not in allowed_hosts:
        raise ScanError("Host staat niet op de toegestane lijst.")
    authority = f"[{host}]" if ":" in host else host
    # Preserve query semantics and meaningful escaping; discard only the fragment.
    return urlunsplit((parts.scheme, authority, quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                       quote(parts.query, safe="%=&?/:@!$'()*+,;~-._"), ""))


def resolve_public(host, port):
    try:
        addresses = list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
        if not addresses or not all(public_ip(address) for address in addresses):
            raise ScanError("DNS verwijst naar een niet-publiek netwerkadres.")
        return addresses[0]
    except ScanError:
        raise
    except (OSError, ValueError) as exc:
        raise ScanError("Hostnaam kan niet veilig worden opgezocht.") from exc


@dataclass
class FetchResult:
    url: str
    status: int
    headers: dict
    body: bytes
    elapsed: float = 0
    redirects: list = field(default_factory=list)
    blocked_redirect: str = ""


class SafeClient:
    def __init__(self, allowed_hosts, *, check_access, duration=180, delay=1.0):
        self.allowed_hosts = set(allowed_hosts)
        self.check_access = check_access
        self.deadline = time.monotonic() + duration
        self.delay = max(0.5, delay)
        self.last_request = {}
        self.robots = {}
        self.transferred = 0
        self.decoded_bytes = 0
        self.documents = []

    def guard(self):
        self.check_access()
        if time.monotonic() >= self.deadline:
            raise ScanBudgetExceeded("Tijdslimiet bereikt; scanresultaat is onvolledig.")

    def _pace(self, host, delay):
        remaining = self.last_request.get(host, 0) + delay - time.monotonic()
        while remaining > 0:
            self.guard()
            time.sleep(min(remaining, 0.25))
            remaining = self.last_request.get(host, 0) + delay - time.monotonic()
        self.last_request[host] = time.monotonic()

    def _request(self, url, delay):
        self.guard()
        parts = urlsplit(url)
        host = parts.hostname
        port = 443 if parts.scheme == "https" else 80
        self._pace(host, delay)
        address = resolve_public(host, port)
        # The TCP peer is the validated IP. DNS cannot be resolved again to a private peer.
        options = {"timeout": urllib3.Timeout(connect=5, read=5), "retries": False}
        if parts.scheme == "https":
            pool = urllib3.HTTPSConnectionPool(address, port=port, server_hostname=host,
                assert_hostname=host, cert_reqs=ssl.CERT_REQUIRED, ca_certs=certifi.where(), **options)
        else:
            pool = urllib3.HTTPConnectionPool(address, port=port, **options)
        response = None
        started = time.monotonic()
        try:
            response = pool.urlopen("GET", parts.path + ("?" + parts.query if parts.query else ""),
                headers={"Host": parts.netloc, "User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
                redirect=False, retries=False, preload_content=False, assert_same_host=False)
            raw = bytearray()
            while True:
                self.guard()
                chunk = response.read(65536, decode_content=False)
                if not chunk:
                    break
                raw.extend(chunk)
                self.transferred += len(chunk)
                if len(raw) > MAX_BODY:
                    raise ScanError("Document overschrijdt 5 MiB.")
                if self.transferred > MAX_TRANSFER:
                    raise ScanBudgetExceeded("Downloadlimiet bereikt; scanresultaat is onvolledig.")
            headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
            body = bytes(raw)
            encoding = headers.get("content-encoding", "identity").lower()
            if encoding not in ("identity", "", "gzip"):
                raise ScanError("Niet-ondersteunde HTTP-compressie.")
            if encoding == "gzip" or body.startswith(b"\x1f\x8b"):
                with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                    body = stream.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    raise ScanError("Uitgepakt document overschrijdt 5 MiB.")
            self.decoded_bytes += len(body)
            if self.decoded_bytes > MAX_TRANSFER:
                raise ScanBudgetExceeded("Totale documentomvang bereikt; scanresultaat is onvolledig.")
            result = FetchResult(url=url, status=response.status, headers=headers, body=body,
                                 elapsed=round(time.monotonic() - started, 3))
            self.documents.append({"url": url, "status": result.status, "body": body,
                "content_type": headers.get("content-type", "")[:255], "fetched_at": timezone.now()})
            return result
        except (ScanError, PermissionError):
            raise
        except Exception as exc:
            # Access/cancellation exceptions must pass through, not turn into HTTP errors.
            from django.core.exceptions import PermissionDenied
            if isinstance(exc, PermissionDenied):
                raise
            raise ScanError("HTTP-opvraag mislukt (netwerk, certificaat of antwoord).") from exc
        finally:
            if response is not None:
                response.close()
            pool.close()

    def _robot_policy(self, url):
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        if origin not in self.robots:
            result = self.fetch(origin + "/robots.txt", enforce_robots=False)
            parser = RobotFileParser()
            if result.status in (404, 410):
                parser.allow_all = True
            elif result.status == 200:
                parser.parse(result.body.decode("utf-8", errors="replace").splitlines())
            else:
                parser.disallow_all = True
            delay = max(self.delay, parser.crawl_delay(USER_AGENT) or 0)
            rate = parser.request_rate(USER_AGENT)
            if rate and rate.requests:
                delay = max(delay, rate.seconds / rate.requests)
            self.robots[origin] = parser, delay
        return self.robots[origin]

    def fetch(self, url, enforce_robots=True):
        redirects = []
        for hop in range(6):
            self.guard()
            url = normalize_url(url, self.allowed_hosts)
            delay = self.delay
            if enforce_robots:
                parser, delay = self._robot_policy(url)
                if not parser.can_fetch(USER_AGENT, url):
                    raise RobotsDenied("robots.txt staat deze URL niet toe of kon niet worden vertrouwd.")
            result = self._request(url, delay)
            if result.status not in (301, 302, 303, 307, 308) or not result.headers.get("location"):
                result.redirects = redirects
                return result
            destination = urljoin(url, result.headers["location"])
            try:
                destination = normalize_url(destination, self.allowed_hosts)
            except ScanError:
                result.redirects = redirects
                result.blocked_redirect = "Redirect naar niet-toegestane URL is niet gevolgd."
                return result
            redirects.append({"url": url, "status": result.status, "destination": destination})
            url = destination
        raise ScanError("Te veel redirects.")
