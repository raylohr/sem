"""Google Search Console read-only connector. No credentials in batch metadata."""
import datetime
from urllib.parse import quote
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.utils import timezone
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import AuthorizedSession
from google_auth_oauthlib.flow import Flow

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
TOKEN_URI = "https://oauth2.googleapis.com/token"
API_BASE = "https://www.googleapis.com/webmasters/v3/sites/"
PAGE_SIZE = 25_000
MAX_DAILY_ROWS = 50_000
MAX_TOTAL_ROWS = 100_000
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class ConnectorError(ValueError):
    pass


def configured():
    return bool(settings.GSC_CLIENT_ID and settings.GSC_CLIENT_SECRET and settings.INTEGRATION_TOKEN_KEY)


def encrypt_token(token):
    return Fernet(settings.INTEGRATION_TOKEN_KEY.encode()).encrypt(token.encode()).decode()


def decrypt_token(value):
    try:
        return Fernet(settings.INTEGRATION_TOKEN_KEY.encode()).decrypt(value.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise ConnectorError("Token kan niet worden gelezen; koppel Google opnieuw.") from exc


def oauth_flow(*, state=None, verifier=None):
    if not configured():
        raise ConnectorError("Google OAuth is nog niet geconfigureerd.")
    config = {"web": {"client_id": settings.GSC_CLIENT_ID, "client_secret": settings.GSC_CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": TOKEN_URI,
        "redirect_uris": [settings.GSC_REDIRECT_URI]}}
    flow = Flow.from_client_config(config, scopes=[SCOPE], state=state,
        code_verifier=verifier, autogenerate_code_verifier=verifier is None)
    flow.redirect_uri = settings.GSC_REDIRECT_URI
    return flow


def authorized_session(connection):
    if not configured():
        raise ConnectorError("Google OAuth is nog niet geconfigureerd.")
    credentials = Credentials(token=None, refresh_token=decrypt_token(connection.encrypted_refresh_token),
        token_uri=TOKEN_URI, client_id=settings.GSC_CLIENT_ID, client_secret=settings.GSC_CLIENT_SECRET,
        scopes=[SCOPE])
    return AuthorizedSession(credentials, refresh_timeout=30)


def request_json(session, method, url, **kwargs):
    try:
        with session.request(method, url, timeout=30, allow_redirects=False, stream=True, **kwargs) as response:
            if response.status_code != 200:
                if response.status_code in (401, 403):
                    raise ConnectorError("Google-toegang geweigerd; controleer propertyrechten of koppel opnieuw.")
                if response.status_code == 429:
                    raise ConnectorError("Google API-limiet bereikt; probeer later opnieuw.")
                raise ConnectorError("Google API-opvraag mislukt; probeer later opnieuw.")
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise ConnectorError("Google API-antwoord overschrijdt de ingestelde grootte.")
            import json
            payload = json.loads(content)
            if not isinstance(payload, dict):
                raise ValueError
            return payload
    except ConnectorError:
        raise
    except Exception as exc:
        raise ConnectorError("Google API niet bereikbaar of antwoord ongeldig.") from exc


def list_properties(session):
    payload = request_json(session, "GET", API_BASE)
    return [item["siteUrl"] for item in payload.get("siteEntry", []) if item.get("permissionLevel") != "siteUnverifiedUser" and item.get("siteUrl")]


def fetch_search_analytics(session, *, property_url, start, end, check_access):
    records, pages = [], []
    warnings = ["GSC retourneert topresultaten en garandeert geen volledige dataset.",
                "Meetdatums volgen America/Los_Angeles; alleen definitieve data (dataState=final)."]
    day = start
    capped = False
    while day <= end and not capped:
        offset = 0
        while True:
            check_access()
            body = {"startDate": day.isoformat(), "endDate": day.isoformat(),
                    "dimensions": ["date", "page"], "type": "web", "dataState": "final",
                    "rowLimit": min(PAGE_SIZE, MAX_TOTAL_ROWS - len(records)), "startRow": offset}
            requested_at = timezone.now()
            payload = request_json(session, "POST", API_BASE + quote(property_url, safe="") + "/searchAnalytics/query", json=body)
            rows = payload.get("rows", [])
            if not isinstance(rows, list) or len(rows) > body["rowLimit"]:
                raise ConnectorError("Onverwachte GSC-resultaatstructuur.")
            page_index = len(pages)
            pages.append({"page_index": page_index, "requested_at": requested_at,
                          "request_body": body, "payload": payload})
            for index, row in enumerate(rows):
                if not isinstance(row, dict) or not isinstance(row.get("keys"), list) or len(row["keys"]) != 2:
                    raise ConnectorError("Onverwachte GSC-rijstructuur.")
                records.append({"locator": f"api:page:{page_index}:row:{index}", "data": {
                    "property": property_url, "date": row["keys"][0], "url": row["keys"][1],
                    "api_page": page_index, "raw": row}})
            if len(records) >= MAX_TOTAL_ROWS:
                warnings.append("Importgrens bereikt; verklein de periode voor meer detail.")
                capped = True
                break
            if not rows or len(rows) < body["rowLimit"]:
                break
            offset += len(rows)
            if offset >= MAX_DAILY_ROWS:
                warnings.append(f"Daglimiet van 50.000 rijen bereikt op {day.isoformat()}; resultaten kunnen onvolledig zijn.")
                break
        day += datetime.timedelta(days=1)
    return records, pages, warnings


def property_contains(property_url, url):
    from catalog.adapters import normalize_url
    from urllib.parse import urlsplit
    identity = normalize_url(url)
    if not identity:
        return False
    if property_url.startswith("sc-domain:"):
        domain = property_url[10:].lower()
        return identity[1] == domain or identity[1].endswith("." + domain)
    # URL-prefix properties include scheme, host and path prefix.
    return url.startswith(property_url) and urlsplit(url).netloc == urlsplit(property_url).netloc


def fetch_inspections(session, *, property_url, urls, check_access):
    records, pages = [], []
    for url in urls:
        check_access()
        if not property_contains(property_url, url):
            raise ConnectorError("URL valt buiten de gekoppelde property.")
        body = {"inspectionUrl": url, "siteUrl": property_url, "languageCode": "nl-NL"}
        requested_at = timezone.now()
        payload = request_json(session, "POST", "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect", json=body)
        result = payload.get("inspectionResult")
        if not isinstance(result, dict) or not isinstance(result.get("indexStatusResult"), dict):
            raise ConnectorError("Onverwacht URL Inspection-antwoord.")
        index = len(pages)
        pages.append({"page_index": index, "requested_at": requested_at, "request_body": body, "payload": payload})
        records.append({"locator": f"inspection:{index}", "data": {"url": url,
            "kind": "inspection", "measured_at": requested_at.isoformat(), "raw": result}})
    return records, pages, ["Google-indexsnapshot; geen live test of indexeringsaanvraag."]
