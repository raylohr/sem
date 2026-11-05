"""Public-only, DNS-pinned HTTP transport. Never uses proxy environment variables."""
import gzip
import io
import ipaddress
import re
import socket
import ssl
import time
import threading
import queue
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit, urljoin, quote
from urllib.robotparser import RobotFileParser
import certifi
import urllib3
from django.utils import timezone
from .work import ScanPause
from .diagnostics import describe_exception, log_failure, MESSAGES

USER_AGENT = "LohrSEM/0.1"
MAX_BODY = 5 * 1024 * 1024
MAX_TRANSFER = 25 * 1024 * 1024


class ScanError(ValueError):
    pass


class NetworkRequestError(ScanError):
    def __init__(self, code, details):
        self.code, self.details = code, details
        super().__init__(MESSAGES[code])


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
    blocked_destination: str = ""
    robots_allowed: bool | None = None
    googlebot_allowed: bool | None = None
    robots_checked: bool = False
    set_cookies: list = field(default_factory=list)
    header_values: dict = field(default_factory=dict)
    header_values_observed: bool = False
    measurements: dict = field(default_factory=dict)


class SafeClient:
    def __init__(self, allowed_hosts, *, check_access, duration=180, delay=1.0, document_sink=None, max_transfer=MAX_TRANSFER, slice_seconds=None, robot_cache=None, respect_robots=True, concurrency=1, shared_rate=False, batch_id=None,connect_timeout=5,read_timeout=5,progress_phase="pages"):
        self.batch_id = str(batch_id) if batch_id else None
        self.progress_phase=progress_phase
        if not 1<=connect_timeout<=60 or not 1<=read_timeout<=120:
            raise ScanError('Ongeldige netwerk-time-out.')
        self.connect_timeout,self.read_timeout=connect_timeout,read_timeout
        from .host_rules import HostRules
        self.allowed_hosts = allowed_hosts if isinstance(allowed_hosts, HostRules) else set(allowed_hosts)
        self.check_access = check_access
        self.deadline = time.monotonic() + duration
        self.slice_deadline = time.monotonic() + slice_seconds if slice_seconds else None
        self.max_transfer = max_transfer
        self.robot_cache = robot_cache if robot_cache is not None else {}
        self.concurrency = concurrency
        self.respect_robots = respect_robots
        self.owner = threading.get_ident()
        self.stopped = threading.Event()
        self.lock = threading.RLock()
        self.robot_lock = threading.RLock()
        self.local = threading.local()
        self.pending_documents = queue.Queue(maxsize=8)
        self.pools = []
        self.delay = max(0.05 if concurrency > 1 else 0.5, delay)
        self.cooldown = {}
        if shared_rate:
            from .throttle import HostThrottle
            self.throttle = HostThrottle()
        else:
            self.throttle = None
        self.last_request = {}
        self.robots = {}
        self.transferred = 0
        self.decoded_bytes = 0
        self.documents = document_sink if document_sink is not None else []

    def guard(self):
        if threading.get_ident() == self.owner:
            self.drain_documents()
            self.check_access()
        elif self.stopped.is_set():
            raise ScanPause
        if time.monotonic() >= self.deadline:
            raise ScanBudgetExceeded("Tijdslimiet bereikt; scanresultaat is onvolledig.")

        if self.slice_deadline and time.monotonic() >= self.slice_deadline:
            raise ScanPause

    def drain_documents(self):
        while True:
            try:
                self.documents.append(self.pending_documents.get_nowait())
            except queue.Empty:
                return

    def close(self):
        self.stopped.set()
        self.drain_documents()
        for pool in self.pools:
            pool.close()

    def _pace(self, host, delay):
        while True:
            self.guard()
            with self.lock:
                now = time.monotonic()
                remaining = max(self.last_request.get(host, 0) + delay,
                                self.cooldown.get(host, 0)) - now
                if remaining <= 0:
                    if self.throttle:
                        remaining = self.throttle.wait(host, delay)
                    if remaining <= 0:
                        self.last_request[host] = now
                        return
            time.sleep(min(remaining, 0.1))

    def _request(self, url, delay, cookie_header=""):
        from . import activity
        self.guard()
        identifier=activity.start(self.batch_id,self.progress_phase,url)
        try:
            result=self._request_impl(url,delay,cookie_header)
        except BaseException as error:
            activity.finish(self.batch_id,self.progress_phase,identifier,error=type(error).__name__)
            raise
        activity.finish(self.batch_id,self.progress_phase,identifier,status=result.status)
        return result

    def _request_impl(self, url, delay, cookie_header=""):
        parts = urlsplit(url)
        host = parts.hostname
        port = 443 if parts.scheme == "https" else 80
        self._pace(host, delay)
        try:
            address = resolve_public(host, port)
        except ScanError as exc:
            code, details = describe_exception(exc, 'dns')
            code = 'dns_error' if code == 'dns_error' else 'unsafe_dns'
            log_failure(url, self.batch_id, code, details, 0)
            raise NetworkRequestError(code, details) from exc
        # The TCP peer is the validated IP. DNS cannot be resolved again to a private peer.
        options = {"timeout": urllib3.Timeout(connect=self.connect_timeout, read=self.read_timeout), "retries": False}
        pools = getattr(self.local, 'pools', None)
        if pools is None:
            self.local.pools = pools = {}
        key = (parts.scheme, host, address, port)
        pool = pools.get(key)
        if pool is None:
            if parts.scheme == "https":
                pool = urllib3.HTTPSConnectionPool(address, port=port, server_hostname=host,
                    assert_hostname=host, cert_reqs=ssl.CERT_REQUIRED, ca_certs=certifi.where(), **options)
            else:
                pool = urllib3.HTTPConnectionPool(address, port=port, **options)
            pools[key] = pool
            with self.lock:
                self.pools.append(pool)
        response = None
        started = time.monotonic()
        stage = "connect_or_headers"
        try:
            request_headers = {"Host": parts.netloc, "User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
            if cookie_header:
                if len(cookie_header) > 16384 or any(char in cookie_header for char in ('\r', '\n')):
                    raise ScanError("Ongeldige tijdelijke cookieheader.")
                request_headers['Cookie'] = cookie_header
            response = pool.urlopen("GET", parts.path + ("?" + parts.query if parts.query else ""),
                headers=request_headers,
                redirect=False, retries=False, preload_content=False, assert_same_host=False)
            stage = "read_body"
            headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
            measurements={}
            if getattr(self,'resource_body_limit',None):
                from .resource_transfer import read_body
                body,measurements=read_body(self,response,headers)
            else:
                raw = bytearray()
                while True:
                    self.guard()
                    chunk = response.read(65536, decode_content=False)
                    if not chunk:
                        break
                    raw.extend(chunk)
                    with self.lock:
                        self.transferred += len(chunk)
                    if len(raw) > MAX_BODY:
                        raise ScanError("Document overschrijdt 5 MiB.")
                    if self.transferred > self.max_transfer:
                        raise ScanBudgetExceeded("Downloadlimiet bereikt; scanresultaat is onvolledig.")
                headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
                body = bytes(raw)
                stage = "decode_body"
                encoding = headers.get("content-encoding", "identity").lower()
                if encoding not in ("identity", "", "gzip"):
                    raise ScanError("Niet-ondersteunde HTTP-compressie.")
                if encoding == "gzip" or body.startswith(b"\x1f\x8b"):
                    with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                        body = stream.read(MAX_BODY + 1)
                    if len(body) > MAX_BODY:
                        raise ScanError("Uitgepakt document overschrijdt 5 MiB.")
                with self.lock:
                    self.decoded_bytes += len(body)
                if self.decoded_bytes > self.max_transfer:
                    raise ScanBudgetExceeded("Totale documentomvang bereikt; scanresultaat is onvolledig.")
                declared=headers.get('content-length','')
                measurements={'declared_bytes':int(declared) if declared.isascii() and declared.isdigit() and len(declared)<20 else None,
                    'wire_bytes':len(raw),'decoded_bytes':len(body),'wire_complete':True,'body_complete':True,'body_truncated':False}
            result = FetchResult(url=url, status=response.status, headers=headers, body=body,
                                 elapsed=round(time.monotonic() - started, 3),measurements=measurements)
            getlist = getattr(response.headers, 'getlist', None)
            from .header_evidence import SAFE_HEADERS
            if callable(getlist):
                result.header_values={key:list(getlist(key)) for key in SAFE_HEADERS}
                result.header_values_observed=True
            result.set_cookies = list(getlist('set-cookie')) if callable(getlist) else ([headers['set-cookie']] if headers.get('set-cookie') else [])
            stage = "record_document"
            sink = self.documents if threading.get_ident() == self.owner else self.pending_documents
            document = {"url": url, "status": result.status, "body": body,
                "content_type": headers.get("content-type", "")[:255], "fetched_at": timezone.now()}
            if getattr(self,'capture_documents',True) is False:
                pass
            elif sink is self.documents:
                sink.append(document)
            else:
                while True:
                    self.guard()
                    try:
                        sink.put(document, timeout=0.1)
                        break
                    except queue.Full:
                        continue
            if result.status in (429, 503) and getattr(self,'defer_http_retries',False) is not True:
                try:
                    backoff = min(120, max(2, int(headers.get('retry-after', '2'))))
                except ValueError:
                    backoff = 5
                with self.lock:
                    self.delay = min(5, max(self.delay * 2, 0.5))
                    self.cooldown[host] = time.monotonic() + backoff
                if self.throttle:
                    self.throttle.cooldown(host, backoff)
            elif result.elapsed > 2:
                with self.lock:
                    self.delay = min(2, max(self.delay, result.elapsed / max(1, self.concurrency)))
            return result
        except (ScanError, PermissionError):
            raise
        except Exception as exc:
            # Access/cancellation exceptions must pass through, not turn into HTTP errors.
            from django.core.exceptions import PermissionDenied
            if isinstance(exc, (PermissionDenied, ScanPause)):
                raise
            code, details = describe_exception(exc, stage)
            log_failure(url, self.batch_id, code, details, time.monotonic()-started)
            raise NetworkRequestError(code, details) from exc
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    def _robot_policy(self, url):
        with self.robot_lock:
            return self._load_robot_policy(url)

    def _load_robot_policy(self, url):
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        if origin not in self.robots:
            cached = self.robot_cache.get(origin)
            if cached:
                result = FetchResult(origin + "/robots.txt", cached['status'], {}, cached['body'].encode('utf-8'))
            else:
                result = self.fetch(origin + "/robots.txt", enforce_robots=False)
                self.robot_cache[origin] = {'status': result.status, 'body': result.body.decode('utf-8', errors='replace')}
            # Redis preserves admission/cooldown across process and slice changes.
            if not self.throttle:
                self.last_request.setdefault(parts.hostname, time.monotonic())
            parser = RobotFileParser()
            parser.sem_available = result.status in (200,404,410)
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
            if self.throttle and self.respect_robots and not cached:
                self.throttle.cooldown(parts.hostname, delay)
            self.robots[origin] = parser, delay
        return self.robots[origin]

    def fetch_once(self, url, enforce_robots=True, cookie_header=""):
        """One validated hop; browser redirects re-enter this transport individually."""
        self.guard()
        url = normalize_url(url, self.allowed_hosts)
        delay = self.delay
        allowed = google_allowed = None
        if enforce_robots:
            try:
                parser, robot_delay = self._robot_policy(url)
                allowed = parser.can_fetch(USER_AGENT, url)
                google_allowed = parser.can_fetch('Googlebot', url) if parser.sem_available else None
                if not parser.sem_available and not self.respect_robots:
                    allowed = None
                if self.respect_robots:
                    delay = max(robot_delay, self.delay)
                    if not allowed:
                        denied = RobotsDenied("robots.txt staat deze URL niet toe of kon niet worden vertrouwd.")
                        denied.googlebot_allowed = google_allowed
                        raise denied
            except ScanBudgetExceeded:
                raise
            except ScanError:
                if self.respect_robots:
                    raise
        result = self._request(url, delay, cookie_header=cookie_header) if cookie_header else self._request(url, delay)
        result.robots_checked = enforce_robots
        result.robots_allowed = allowed
        result.googlebot_allowed = google_allowed
        return result

    def fetch(self, url, enforce_robots=True):
        redirects = []
        for hop in range(6):
            result = self.fetch_once(url, enforce_robots=enforce_robots)
            url = result.url
            if result.status not in (301, 302, 303, 307, 308) or not result.headers.get("location"):
                result.redirects = redirects
                return result
            destination = urljoin(url, result.headers["location"])
            try:
                destination = normalize_url(destination, self.allowed_hosts)
            except ScanError:
                result.redirects = redirects
                result.blocked_redirect = "Redirect naar niet-toegestane URL is niet gevolgd."
                try:
                    result.blocked_destination = normalize_url(destination)
                except ScanError:
                    result.blocked_destination = ''
                return result
            redirects.append({"url": url, "status": result.status, "destination": destination,
                              "robots_allowed": result.robots_allowed, "googlebot_allowed": result.googlebot_allowed})
            url = destination
        raise ScanError("Te veel redirects.")
