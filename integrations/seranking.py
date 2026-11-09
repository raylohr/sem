"""Read-only SE Ranking Project API. All unlisted endpoints are denied locally."""
import time
from datetime import date
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
        if endpoint not in ENDPOINTS:
            raise SERankingError('SE Ranking-endpoint geblokkeerd: uitsluitend toegestane Project API-methoden.')
        path, allowed = ENDPOINTS[endpoint]
        if set(params) - allowed:
            raise SERankingError('SE Ranking-verzoek bevat niet-toegestane parameters.')
        for key, value in params.items():
            if key in {'site_id', 'audit_id', 'limit'} and (type(value) is not int or value < 1):
                raise SERankingError('Ongeldige SE Ranking-identificatie of limiet.')
            if key == 'offset' and (type(value) is not int or value < 0):
                raise SERankingError('Ongeldige SE Ranking-offset.')
            if not isinstance(value, (str, int)) or isinstance(value, bool):
                raise SERankingError('Ongeldige SE Ranking-parameter.')
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
                    import json
                    payload = json.loads(b''.join(chunks))
                    if not isinstance(payload, (dict, list)) or isinstance(payload, dict) and ('error' in payload or 'errors' in payload):
                        raise SERankingError('SE Ranking gaf geen geldig gegevensantwoord.')
                    return payload
        except (requests.RequestException, ValueError):
            raise SERankingError('SE Ranking Project API is niet bereikbaar of gaf een ongeldig antwoord.') from None

    def pages(self, endpoint, *, check_access=lambda: None, **params):
        rows = []
        for index in range(MAX_PAGES):
            check_access()
            payload = self.get(endpoint, **params, limit=PAGE_SIZE, offset=index * PAGE_SIZE)
            items = payload.get('items') if isinstance(payload, dict) else payload
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise SERankingError('Onverwacht SE Ranking-paginatieschema.')
            rows.extend(items)
            if len(rows) > MAX_ROWS:
                raise SERankingError("SE Ranking-import bevat te veel records; splits de import op.")
            if isinstance(payload, dict) and type(payload.get('total')) is int:
                if len(rows) >= payload['total']:
                    return rows
                if not items:
                    raise SERankingError('SE Ranking-paginatie is onvolledig; er zijn geen gegevens gepubliceerd.')
            elif len(items) < PAGE_SIZE:
                return rows
        raise SERankingError('SE Ranking-importlimiet bereikt; er zijn geen onvolledige gegevens gepubliceerd.')


def object_list(payload):
    if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        raise SERankingError('Onverwacht SE Ranking-antwoordformaat.')
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
                landing = {row.get('date'): row.get('url') for row in object_list(keyword.get('landing_pages', []))}
                for position in object_list(keyword.get('positions', [])):
                    stamp = position.get('date')
                    try:
                        if not batch.period_start <= date.fromisoformat(stamp) <= batch.period_end:
                            raise ValueError
                    except (ValueError, TypeError):
                        raise SERankingError('SE Ranking gaf een ongeldige positie-datum.') from None
                    rows.append({'url': landing.get(stamp), 'date': stamp, 'keyword_id': keyword.get('id'),
                        'keyword': info.get('name'), 'target_url': info.get('link'),
                        'site_engine_id': engine.get('site_engine_id'), 'position': position.get('pos'),
                        'position_change': position.get('change'), 'search_volume': keyword.get('volume'),
                        'competition': keyword.get('competition'), 'suggested_bid': keyword.get('suggested_bid'),
                        'content_score': keyword.get('content_score'), 'serp_features': keyword.get('features'),
                        'raw': {'keyword': keyword, 'position': position, 'configuration': info}})
        warnings.append('Alleen posities met een waargenomen landings-URL binnen de projecthosts verschijnen in de URL-inventaris. Zoekvolume en CPC zijn zoekwoordmetrics; deze zijn niet optelbaar per pagina.')
    elif report == 'audit_pages':
        check_access()
        audits = client.pages('audits', search=connection.site_name, check_access=check_access)
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
