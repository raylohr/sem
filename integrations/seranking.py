"""Read-only SE Ranking Project API. All unlisted endpoints are denied locally."""
import json
import time
from datetime import date
from hashlib import sha256
from types import MappingProxyType

import requests
from django.conf import settings

BASE_URL = 'https://api.seranking.com/v1/project-management/'
# Exact paths and query keys, rather than a prefix check. Never accept a caller URL.
ENDPOINTS = MappingProxyType({
    'projects': ('sites', frozenset()),
    'engines': ('sites/search-engines', frozenset({'site_id'})),
    'keywords': ('keywords', frozenset({'site_id'})),
    'summary': ('sites/summary', frozenset({'site_id'})),
    'rankings': ('sites/positions', frozenset({'site_id', 'date_from', 'date_to', 'with_landing_pages', 'with_serp_features', 'with_content_score'})),
    'competitors': ('competitors', frozenset({'site_id'})),
    'backlinks': ('backlinks', frozenset({'site_id', 'limit', 'offset'})),
    'gsc_queries': ('analytics/gsc/queries', frozenset({'site_id'})),
    'seo_potential': ('analytics/seo-potential', frozenset({'site_id'})),
    'audits': ('audits', frozenset({'search', 'limit', 'offset'})),
    'audit_pages': ('audits/pages', frozenset({'audit_id', 'limit', 'offset'})),
})
REPORTS = MappingProxyType({
    'rankings': ('Zoekwoordposities en landingspagina’s', 'url'),
    'keywords': ('Gevolgde zoekwoorden en doel-URL’s', 'summary'),
    'summary': ('Projectoverzicht', 'summary'),
    'engines': ('Zoekmachines en locaties', 'summary'),
    'competitors': ('Gevolgde concurrenten', 'summary'),
    'backlinks': ('Gemonitorde backlinks', 'url'),
    'gsc_queries': ('GSC-zoekopdrachten', 'summary'),
    'seo_potential': ('SEO-potentieel (schatting)', 'summary'),
    'audit_pages': ('Bestaande website-audit · pagina’s', 'url'),
})
REPORT_COLUMNS = MappingProxyType({
    'rankings': ('date', 'keyword', 'url', 'target_url', 'site_engine_id', 'position', 'position_change', 'search_volume', 'suggested_bid'),
    'keywords': ('id', 'name', 'link', 'group_id', 'tags', 'site_engine_ids'),
    'summary': ('name', 'today_avg', 'yesterday_avg', 'total_up', 'total_down', 'top5', 'top10', 'top30', 'visibility', 'visibility_percent'),
    'engines': ('site_engine_id', 'search_engine_id', 'region_name', 'lang_code', 'keyword_count'),
    'competitors': ('id', 'name', 'url', 'domain_trust'),
    'backlinks': ('to_url', 'from_url', 'anchor', 'is_nofollow', 'is_sponsored', 'is_link_not_found', 'date_lost'),
    'gsc_queries': ('query', 'impressions', 'clicks', 'ctr', 'avg'),
    'seo_potential': ('site_engine_id', 'traffic', 'traffic_value', 'leads_qty', 'leads_price'),
    'audit_pages': ('url', 'date', 'status', 'title', 'description', 'h1', 'words_count', 'indexable_status', 'canonical_url', 'errors', 'warnings'),
})
REPORT_DESCRIPTIONS = MappingProxyType({
    'rankings': 'Posities per zoekwoord, zoekmachine en datum, met de werkelijke landingspagina en de ingestelde doelpagina. URL-gegevens.',
    'keywords': 'Gevolgde zoekwoorden met doelpagina, groepen en tags. Zoekwoordrapport; voegt geen pagina’s toe aan de URL-inventaris.',
    'summary': 'Gemiddelde positie, stijgingen en dalingen, top-posities en zichtbaarheid. Projectrapport.',
    'engines': 'Ingestelde zoekmachines, regio’s, talen en aantallen zoekwoorden. Projectrapport.',
    'competitors': 'Concurrenten die in dit SE Ranking-project worden gevolgd. Projectrapport.',
    'backlinks': 'Gemonitorde links met verwijzende pagina, doelpagina, ankertekst en linkstatus. Verrijkt de doel-URL’s.',
    'gsc_queries': 'Klikken, vertoningen, CTR en gemiddelde positie per zoekopdracht, indien beschikbaar via SE Ranking. Geen paginametrics.',
    'seo_potential': 'Geschat verkeer, verkeerswaarde en potentiële leads. Projectrapport met schattingen.',
    'audit_pages': 'Pagina’s uit een bestaande voltooide audit, met metadata, indexeerbaarheid en probleemaantallen. URL-gegevens.',
})


def report_options():
    return [{'key': key, 'label': label, 'scope': scope, 'description': REPORT_DESCRIPTIONS[key]}
            for key, (label, scope) in REPORTS.items() if key != 'audit_pages']


PAGE_SIZE = 100
MAX_PAGES = 1000
MAX_ROWS = 100000
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class SERankingError(Exception):
    """Only fixed, safe messages; never include provider payloads or credentials."""


def configured():
    return bool(settings.SERANKING_API_KEY)


class ProjectClient:
    def __init__(self):
        self._last_request = 0

    def get(self, endpoint, **params):
        if not isinstance(endpoint, str) or endpoint not in ENDPOINTS:
            raise SERankingError('SE Ranking-endpoint geblokkeerd: uitsluitend toegestane Project API-methoden.')
        path, allowed = ENDPOINTS[endpoint]
        required = {'audit_id'} if endpoint == 'audit_pages' else set() if endpoint in ('projects', 'audits') else {'site_id'}
        if set(params) - allowed or required - set(params):
            raise SERankingError('SE Ranking-verzoek bevat ontbrekende of niet-toegestane parameters.')
        for key, value in params.items():
            if key in {'site_id', 'audit_id', 'limit', 'offset'}:
                minimum = 0 if key == 'offset' else 1
                maximum = 1000 if key == 'limit' else MAX_ROWS if key == 'offset' else 2**63 - 1
                if type(value) is not int or not minimum <= value <= maximum:
                    raise SERankingError('Ongeldige SE Ranking-identificatie, limiet of offset.')
            elif key.startswith('with_'):
                if type(value) is not int or value not in (0, 1):
                    raise SERankingError('Ongeldige SE Ranking-optie.')
            elif key in ('date_from', 'date_to'):
                try:
                    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                        raise ValueError
                except (ValueError, TypeError):
                    raise SERankingError('Ongeldige SE Ranking-datum.') from None
            elif not isinstance(value, str) or len(value) > 2048 or any(ord(char) < 32 for char in value):
                raise SERankingError('Ongeldige SE Ranking-parameter.')
        if endpoint == 'rankings':
            if not {'date_from', 'date_to'} <= set(params):
                raise SERankingError('Geef een periode voor SE Ranking-posities op.')
            days = (date.fromisoformat(params['date_to']) - date.fromisoformat(params['date_from'])).days
            if not 0 <= days <= 30:
                raise SERankingError('Kies maximaal 31 dagen voor SE Ranking-posities.')
        if not configured():
            raise SERankingError('SERANKING_API_KEY is nog niet ingesteld.')
        delay = .15 - (time.monotonic() - self._last_request)
        if delay > 0:
            time.sleep(delay)
        try:
            # Fresh session: no persistent auth, cookies, proxy or netrc credentials.
            with requests.Session() as session:
                session.trust_env = False
                self._last_request = time.monotonic()
                with session.get(BASE_URL + path, params=params,
                                 headers={'Authorization': 'Token ' + settings.SERANKING_API_KEY,
                                          'Accept': 'application/json'},
                                 timeout=(10, 60), allow_redirects=False, stream=True) as response:
                    if response.status_code != 200:
                        messages = {401: 'SE Ranking weigert de API-sleutel.',
                                    403: 'Geen toegang tot deze SE Ranking Project API-gegevens.',
                                    429: 'SE Ranking-aanvraaglimiet bereikt. Probeer later opnieuw.'}
                        raise SERankingError(messages.get(response.status_code, 'SE Ranking Project API-verzoek mislukt.'))
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            raise SERankingError('SE Ranking-antwoord overschrijdt de veilige importlimiet.')
                        chunks.append(chunk)
                    payload = json.loads(b''.join(chunks))
                    if not isinstance(payload, (dict, list)) or isinstance(payload, dict) and ('error' in payload or 'errors' in payload):
                        raise SERankingError('SE Ranking gaf geen geldig gegevensantwoord.')
                    return payload
        except (requests.RequestException, ValueError):
            raise SERankingError('SE Ranking Project API is niet bereikbaar of gaf een ongeldig antwoord.') from None

    def pages(self, endpoint, *, check_access=lambda: None, **params):
        if endpoint not in ('backlinks', 'audits', 'audit_pages') or {'limit', 'offset'} & set(params):
            raise SERankingError('Paginatie is niet toegestaan voor dit SE Ranking-verzoek.')
        rows, seen, expected_total = [], set(), None
        for _ in range(MAX_PAGES):
            check_access()
            payload = self.get(endpoint, **params, limit=PAGE_SIZE, offset=len(rows))
            items = payload.get('items') if isinstance(payload, dict) else payload
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise SERankingError('Onverwacht SE Ranking-paginatieschema.')
            total = payload.get('total') if isinstance(payload, dict) else None
            if total is not None:
                if type(total) is not int or not 0 <= total <= MAX_ROWS:
                    raise SERankingError('SE Ranking gaf een ongeldig of te groot rapporttotaal.')
                if expected_total is not None and expected_total != total:
                    raise SERankingError('Het SE Ranking-rapport is tijdens ophalen gewijzigd. Probeer opnieuw.')
                expected_total = total
            fingerprint = sha256(json.dumps(items, sort_keys=True, separators=(',', ':')).encode()).digest()
            if items and fingerprint in seen:
                raise SERankingError('SE Ranking herhaalt dezelfde rapportpagina; import gestopt.')
            seen.add(fingerprint)
            rows.extend(items)
            if len(rows) > MAX_ROWS or expected_total is not None and len(rows) > expected_total:
                raise SERankingError('SE Ranking gaf meer records dan het toegestane rapporttotaal.')
            if expected_total is not None:
                if len(rows) == expected_total:
                    return rows
                if not items:
                    raise SERankingError('SE Ranking-paginatie is onvolledig; er zijn geen gegevens gepubliceerd.')
            elif len(items) < PAGE_SIZE:
                return rows
        raise SERankingError('SE Ranking-importlimiet bereikt; er zijn geen onvolledige gegevens gepubliceerd.')


def object_list(payload):
    if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        raise SERankingError('Onverwacht SE Ranking-antwoordformaat.')
    if len(payload) > MAX_ROWS:
        raise SERankingError('SE Ranking-import bevat te veel records; splits de import op.')
    return payload


def matching_projects(client, project):
    from projects.hosts import allows_url
    matches = []
    for item in object_list(client.get('projects')):
        name = item.get('name')
        if not isinstance(name, str):
            continue
        url = name if '://' in name else 'https://' + name
        if allows_url(project, url) and str(item.get('id', '')).isdigit():
            matches.append({'id': int(item['id']), 'name': name, 'title': item.get('title', name)})
    return matches


def verify_connection(client, connection):
    if connection.site_id not in {item['id'] for item in matching_projects(client, connection.project)}:
        raise SERankingError('Het gekoppelde SE Ranking-project valt niet meer binnen de projecthosts of is niet toegankelijk.')


def site_audits(client, connection, check_access=lambda: None):
    from urllib.parse import urlsplit
    name = connection.site_name
    host = urlsplit(name if '://' in name else 'https://' + name).hostname
    if not host:
        raise SERankingError('Ongeldig SE Ranking-projectdomein.')
    return [audit for audit in client.pages('audits', search=host, check_access=check_access)
            if str(audit.get('site_id')) == str(connection.site_id) and audit.get('status') == 'finished']


def fetch_report(client, batch, check_access):
    """Return normalized observations; retain unmodified source rows under raw."""
    connection = batch.seranking_connection
    report = batch.request_metadata['type']
    if report not in REPORTS:
        raise SERankingError('Onbekend SE Ranking-rapport.')
    check_access()
    verify_connection(client, connection)
    params = {'site_id': connection.site_id}
    rows = []
    warnings = []
    if report == 'rankings':
        check_access()
        keywords = {str(row['id']): row for row in object_list(client.get('keywords', **params))}
        check_access()
        payload = object_list(client.get('rankings', **params, date_from=batch.period_start.isoformat(),
            date_to=batch.period_end.isoformat(), with_landing_pages=1, with_serp_features=1, with_content_score=1))
        for engine in payload:
            for keyword in object_list(engine.get('keywords')):
                info = keywords.get(str(keyword.get('id')), {})
                landing_rows = object_list(keyword.get('landing_pages') or [])
                landing = {row.get('date'): row for row in landing_rows}
                keyword_metrics = {key: value for key, value in keyword.items() if key not in ('positions', 'landing_pages')}
                for position in object_list(keyword.get('positions') or []):
                    if len(rows) >= MAX_ROWS:
                        raise SERankingError('SE Ranking-import bevat te veel records; splits de import op.')
                    stamp = position.get('date')
                    try:
                        if not batch.period_start <= date.fromisoformat(stamp) <= batch.period_end:
                            raise ValueError
                    except (ValueError, TypeError):
                        raise SERankingError('SE Ranking gaf een ongeldige positie-datum.') from None
                    rows.append({'url': landing.get(stamp, {}).get('url'), 'date': stamp, 'keyword_id': keyword.get('id'),
                        'keyword': info.get('name') or keyword.get('name'), 'target_url': info.get('link'),
                        'site_engine_id': engine.get('site_engine_id'), 'position': position.get('pos'),
                        'position_change': position.get('change'), 'search_volume': keyword.get('volume'),
                        'competition': keyword.get('competition'), 'suggested_bid': keyword.get('suggested_bid'),
                        'content_score': keyword.get('content_score'), 'serp_features': keyword.get('features'),
                        'raw': {'keyword': keyword_metrics, 'position': position,
                                'landing_page': landing.get(stamp), 'configuration': info}})
        warnings.append('Alleen posities met een waargenomen landings-URL binnen de projecthosts verschijnen in de URL-inventaris. Zoekvolume en CPC zijn zoekwoordmetrics; deze zijn niet optelbaar per pagina.')
    elif report == 'audit_pages':
        check_access()
        audits = site_audits(client, connection, check_access)
        audit_id = batch.request_metadata['audit_id']
        audit = next((row for row in audits if str(row.get('id')) == str(audit_id)
                      and str(row.get('site_id')) == str(connection.site_id)), None)
        if not audit or audit.get('status') != 'finished':
            raise SERankingError('Kies een voltooide audit van het gekoppelde SE Ranking-project.')
        items = client.pages('audit_pages', audit_id=audit_id, check_access=check_access)
        rows = [{**item, 'date': audit.get('last_update'), 'raw': item} for item in items]
    elif report == 'backlinks':
        items = client.pages('backlinks', **params, check_access=check_access)
        rows = [{**item, 'url': item.get('to_url'), 'raw': item} for item in items]
        warnings.append('Dit rapport bevat gemonitorde projectbacklinks; het is geen volledige backlinkontdekking.')
    else:
        check_access()
        payload = client.get(report, **params)
        items = [payload] if report == 'summary' and isinstance(payload, dict) else object_list(payload)
        rows = [{'raw': item, **item} for item in items]
    if len(rows) > MAX_ROWS:
        raise SERankingError('SE Ranking-import bevat te veel records; splits de import op.')
    return [{'locator': str(index), 'data': row} for index, row in enumerate(rows, 1)], warnings
