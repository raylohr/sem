"""Conservative URL identities and explicitly source-specific observations."""
import datetime
import hashlib
import ipaddress
import math
from decimal import Decimal,InvalidOperation
import re
from urllib.parse import urlsplit, urlunsplit
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_date

URL_COLUMNS = ["Address", "URL", "Page URL", "URL zoals gebruikt in audit", "URL / pad uit bronexport", "Archief-URL"]


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
        return normalized, host, hashlib.sha256(normalized.encode()).hexdigest()
    except (ValueError, UnicodeError):
        return None


def first(values, *keys):
    for key in keys:
        value = values.get(key)
        if value is not None and value != "":
            return value
    return None


def number(value, integer=False):
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, str) and not re.fullmatch(r"-?\d+(?:\.\d+)?", value.strip()):
            return None
        result = Decimal(str(value))
        if not result.is_finite():
            return None
        if result == result.to_integral_value():
            return int(result)
        if integer:
            return None
        floating = float(result)
        return floating if math.isfinite(floating) else None
    except (TypeError, ValueError, OverflowError, InvalidOperation):
        return None


def boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        value = value.strip().lower()
        if value in ("true", "yes", "ja", "indexable"):
            return True
        if value in ("false", "no", "nee", "non-indexable", "nonindexable"):
            return False
    return None


def date_value(value):
    if not value or str(value).startswith("1970-01-01"):
        return None
    try:
        return parse_date(str(value)[:10])
    except (ValueError, TypeError):
        return None


def adapt_record(batch, data, context=None):
    if not isinstance(data, dict):
        return None
    context = context or {}
    values = data.get("values") if isinstance(data.get("values"), dict) else data
    original = data.get("url") or first(values, *URL_COLUMNS)
    identity = normalize_url(original)
    if identity is None:
        return None
    facts = {}
    source = batch.source
    scope = "subtree" if "site structure" in str(data.get("sheet", "")).lower() else "page"
    def add(key, value):
        if value is not None:
            facts[key] = value
    measured_at = batch.measured_at
    measured_date = measured_at.date() if measured_at else None
    stamp = data.get("measured_at") or (values.get("Crawl Timestamp") if source == "screaming_frog" else None)
    if stamp and not str(stamp).startswith("1970-01-01"):
        try:
            parsed = parse_datetime(str(stamp))
        except (ValueError, TypeError):
            parsed = None
        if parsed:
            measured_date = parsed.date()
            if timezone.is_aware(parsed):
                measured_at = parsed
            else:
                measured_at = None
                facts["source_timestamp_without_timezone"] = str(stamp)
    if source == "gsc" and batch.method == "api" and data.get("kind") == "inspection":
        raw = data.get("raw", {}).get("indexStatusResult", {})
        mapping = {"verdict": "gsc_index_verdict", "coverageState": "gsc_indexing_reason",
            "robotsTxtState": "gsc_robots_state", "indexingState": "gsc_indexing_state",
            "pageFetchState": "gsc_fetch_state", "lastCrawlTime": "gsc_last_crawled",
            "googleCanonical": "gsc_google_canonical", "userCanonical": "gsc_user_canonical"}
        for key, target in mapping.items():
            add(target, raw.get(key))
    elif source == "gsc" and batch.method == "api":
        measured_at = None
        measured_date = date_value(data.get("date"))
        raw = data.get("raw", {})
        if isinstance(raw, dict):
            for key in ("clicks", "impressions", "ctr", "position"):
                add("gsc_"+key, number(raw.get(key)))
        facts["gsc_date_timezone"] = "America/Los_Angeles"
    elif source == "gsc":
        add("gsc_indexing_reason", context.get("gsc_problem"))
        add("gsc_last_crawled", date_value(values.get("Laatst gecrawld")).isoformat() if date_value(values.get("Laatst gecrawld")) else None)
    if source in ("screaming_frog", "ahrefs"):
        status = number(first(values, "Status Code", "HTTP status code", "Page HTTP code"), integer=True)
        if status is not None and 100 <= status <= 599:
            facts["http_status"] = status
        content_type = first(values, "Content Type", "Content type")
        add("content_type", content_type)
        title = next((str(values.get(key) or "") for key in ("Title 1", "Title", "Page title") if key in values), None)
        add("title", title)
        if title is not None and (not content_type or "html" in str(content_type).lower()):
            facts["missing_title"] = not bool(title.strip())
        add("is_indexable", boolean(first(values, "Indexability", "Is indexable page")))
        add("canonical", first(values, "Canonical Link Element 1"))
        add("language", first(values, "Language"))
        add("word_count", number(values.get("Word Count"), integer=True))
    if source == "ahrefs":
        mapping = {"organic_traffic_estimate": ["Organic traffic"], "referring_domains": ["No. of referring domains", "Referring domains"],
            "backlinks": ["No. of backlinks", "Links to target"], "dofollow_backlinks": ["No. of dofollow backlinks", "Dofollow"],
            "keywords_top3": ["No. of keywords with Top 3 position"], "keywords_top10": ["No. of keywords with Top 10 position"],
            "page_rating": ["PR"], "url_rating": ["UR"]}
        for key, columns in mapping.items():
            add("ahrefs_"+key, number(first(values, *columns)))
    if source == "audit":
        mapping = {"audit_title": ["Paginatitel uit crawl", "Title"], "audit_page_type": ["Paginatype", "Type URL"],
            "audit_finding": ["Bevinding / aandachtspunt", "Wat mist / aandachtspunt", "Bevinding uit bronregister"],
            "audit_action": ["Concrete actie / aanbeveling", "Concrete actie", "Aanbevolen actie", "Actie uit bronregister"],
            "audit_priority": ["Prioriteit", "Prioriteit uit bronregister"],
            "audit_evidence": ["Controle-/bewijsstatus", "Bewijsstatus", "Controle-uitkomst", "Status controle", "Inhoudelijke controle"],
            "audit_gsc_status": ["GSC-status"], "audit_reported_http": ["HTTP-status Screaming Frog"]}
        for key, columns in mapping.items():
            value = first(values, *columns)
            if value is not None:
                facts[key] = str(value)[:2000]
        if scope == "subtree":
            for key, column in {"organic_traffic_estimate":"Current organic traffic", "referring_domains":"Ref. domains", "organic_keywords":"Current # of organic keywords"}.items():
                add("ahrefs_subtree_"+key, number(values.get(column)))
    return {"normalized_url": identity[0], "host": identity[1], "url_hash": identity[2],
            "original_url": original, "scope": scope, "measured_date": measured_date,
            "measured_at": measured_at, "facts": facts}
