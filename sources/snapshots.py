"""Snapshot classification: which import of a dataset feeds the analyses.

A dataset is project + source + report type (+ a segment where one report type
is delivered in independent slices, such as GSC indexing categories). Per
dataset at most one import is active (enforced by a partial unique constraint).

Report types that are time series, samples or summaries (GSC performance,
URL inspection, SE Ranking rankings, ...) are not snapshots: they are
classified "independent" and keep counting as before.
"""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.db import transaction
from django.utils import timezone

from .models import ImportBatch
from .url_inventory import URL_FIELDS, as_date

S = ImportBatch.Snapshot

# (source, report type) pairs that are complete point-in-time exports.
SNAPSHOT_REPORTS = {
    ('ahrefs', 'ahrefs_site_audit_pages'), ('ahrefs', 'ahrefs_site_audit_missing_titles'),
    ('sf', 'internal_all'), ('sf', 'response_codes'), ('sf', 'hreflang'), ('sf', 'page_titles'),
    ('sf', 'sitemap_inlinks'), ('sf', 'crawl_archive'),
    ('gsc', 'gsc_page_indexing'),
    ('seranking', 'audit_pages'),
}
# Columns that differ between two exports of the same crawl.
VOLATILE_COLUMNS = {'Crawl Timestamp', 'Response Time', 'id', 'date', 'measured_at'} | set(URL_FIELDS)
TRACKING_PARAMETERS = {'gclid', 'fbclid', 'msclkid', 'dclid', 'yclid', '_ga', 'mc_cid', 'mc_eid'}
STATUS_COLUMNS = ('HTTP status code', 'Status Code', 'status')
SAMPLE_LIMIT = 3


def report_type(batch):
    metadata = batch.request_metadata or {}
    return metadata.get('type') or metadata.get('kind') or (
        'search_analytics' if batch.source == 'gsc' else 'internal_all' if batch.source == 'sf' else 'records')


def dataset_key(batch):
    key = f'{batch.source}:{report_type(batch)}'
    if report_type(batch) == 'gsc_page_indexing':
        # Every GSC indexing export is one category/reason; they complement each other.
        metadata = batch.request_metadata or {}
        segment = f"{metadata.get('report_status', '')}|{metadata.get('indexing_reason', '')}"
        key += ':' + hashlib.sha256(segment.encode()).hexdigest()[:16]
    return key[:255]


def is_snapshot(batch):
    scope = (batch.request_metadata or {}).get('record_scope')
    return (batch.source, report_type(batch)) in SNAPSHOT_REPORTS and scope not in ('summary', 'external')


def url_key(url):
    """Lowercase host, no fragment, consistent trailing slash, tracking parameters removed."""
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.hostname or '').rstrip('.').lower()
    if not host:
        return None
    scheme = parts.scheme.lower()
    port = parts.port if parts.port not in (None, 80 if scheme == 'http' else 443) else None
    path = (parts.path or '/').rstrip('/') or '/'
    query = urlencode(sorted((key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                             if not key.lower().startswith('utm_') and key.lower() not in TRACKING_PARAMETERS))
    return urlunsplit((scheme, host + (f':{port}' if port else ''), path, query, ''))


def measured_day(batch):
    for stamp in (batch.measured_at, batch.period_end):
        if stamp:
            return as_date(stamp)
    return timezone.localtime(batch.imported_at).date() if batch.imported_at else date.today()


def normalized(value):
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value).strip()


def record_url(data):
    return next((data.get(key) for key in URL_FIELDS if data.get(key)), None)


def row_key(data):
    key = url_key(record_url(data))
    # Reports without a URL column (e.g. link tables) compare as sets of rows.
    return key or 'row:' + hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def positive(value):
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number > 0 if number.is_finite() else None


@dataclass
class Profile:
    rows: int = 0
    columns: set = field(default_factory=set)
    positive: dict = field(default_factory=dict)     # column -> rows with a value > 0
    statuses: dict = field(default_factory=dict)     # status column -> distinct values (bounded)


def records(batch):
    return batch.records.order_by('id').values_list('data', flat=True).iterator(chunk_size=1000)


def profile(batch, cache):
    if batch.pk in cache:
        return cache[batch.pk]
    result = Profile()
    for data in records(batch):
        result.rows += 1
        result.columns.update(data)
        for column, value in data.items():
            if positive(value):
                result.positive[column] = result.positive.get(column, 0) + 1
        for column in STATUS_COLUMNS:
            if column in data and len(result.statuses.setdefault(column, set())) < 20:
                result.statuses[column].add(normalized(data[column]))
    cache[batch.pk] = result
    return result


def fingerprints(batch, columns):
    """URL key -> digest of its rows over the given columns (duplicate rows kept as a multiset)."""
    rows = {}
    for data in records(batch):
        digest = hashlib.sha256(json.dumps([normalized(data.get(column)) for column in columns]).encode()).hexdigest()
        rows.setdefault(row_key(data), []).append(digest)
    return {key: '|'.join(sorted(values)) for key, values in rows.items()}


@dataclass
class Comparison:
    new_only: int
    other_only: int
    new_extra_columns: list
    other_extra_columns: list
    conflicts: int
    examples: list


def compare(new, other, cache):
    new_profile, other_profile = profile(new, cache), profile(other, cache)
    shared = sorted((new_profile.columns & other_profile.columns) - VOLATILE_COLUMNS)
    theirs = fingerprints(other, shared)
    conflicts, examples, new_only, seen = 0, [], 0, set()
    for key, digest in fingerprints(new, shared).items():
        if key not in theirs:
            new_only += 1
            continue
        seen.add(key)
        if digest != theirs[key]:
            conflicts += 1
            if len(examples) < SAMPLE_LIMIT:
                examples.append(key)
    return Comparison(
        new_only=new_only, other_only=len(theirs.keys() - seen),
        new_extra_columns=sorted(new_profile.columns - other_profile.columns - VOLATILE_COLUMNS),
        other_extra_columns=sorted(other_profile.columns - new_profile.columns - VOLATILE_COLUMNS),
        conflicts=conflicts, examples=examples)


def describe(batch):
    return f"{batch.label} ({timezone.localtime(batch.imported_at):%d-%m-%Y %H:%M}, {batch.record_count} records)"


def columns_text(columns):
    return ', '.join(columns[:5]) + (f' en {len(columns) - 5} meer' if len(columns) > 5 else '')


def quality_warnings(batch, active, cache):
    warnings = []
    metadata = batch.request_metadata or {}
    if metadata.get('missing_expected_columns'):
        warnings.append({'code': 'missing_columns', 'message': 'Verwachte kolommen ontbreken: '
                         + ', '.join(metadata['missing_expected_columns']) + '. Mogelijk een ander exporttype of een beperkte kolomselectie.'})
    if active is not None and active.record_count and batch.record_count < active.record_count / 2:
        drop = round(100 * (1 - batch.record_count / active.record_count))
        warnings.append({'code': 'row_drop', 'message': f'{batch.record_count} rijen: {drop}% minder dan de actieve import ({active.record_count}).'})
    if is_snapshot(batch) and batch.record_count:
        mine = profile(batch, cache)
        theirs = profile(active, cache) if active is not None else None
        filtered = []
        for column in sorted(mine.columns - VOLATILE_COLUMNS):
            if mine.positive.get(column, 0) != mine.rows:
                continue
            if theirs is not None:
                # Only suspicious when the active snapshot does contain zero/empty values.
                if column in theirs.columns and theirs.rows - theirs.positive.get(column, 0) >= max(1, theirs.rows // 10):
                    filtered.append(f'{column} > 0')
            elif 'traffic' in column.lower():
                filtered.append(f'{column} > 0')
        if theirs is not None:
            for column in STATUS_COLUMNS:
                values = mine.statuses.get(column)
                if values and len(values) == 1 and len(theirs.statuses.get(column, ())) > 1:
                    filtered.append(f'{column} = {next(iter(values))}')
        if filtered:
            warnings.append({'code': 'filtered', 'message': 'Alle rijen voldoen aan ' + ', '.join(filtered[:SAMPLE_LIMIT])
                             + '. Vermoedelijk een gefilterde export.'})
    host = metadata.get('host_filter') or {}
    excluded = metadata.get('excluded_rows', host.get('excluded_rows')) or 0
    total = metadata.get('input_rows') or excluded + (host.get('accepted_rows') or batch.record_count)
    if total and excluded / total > 0.2:
        warnings.append({'code': 'foreign_hosts', 'message': f'{excluded} van {total} rijen ({round(100 * excluded / total)}%) lagen buiten de projecthosts.'})
    return warnings


@dataclass
class Decision:
    batch: ImportBatch
    status: str
    reason: str
    superseded_by: ImportBatch = None


def decide(new, active, supplements, cache):
    """Classify `new` against the dataset's current active import and supplements.

    Returns decisions for `new` and for any import whose status changes because of it.
    """
    if not is_snapshot(new):
        return [Decision(new, S.INDEPENDENT, 'Dit rapporttype is een tijdreeks, steekproef of samenvatting en geen '
                         'volledige momentopname; de import telt zelfstandig mee.')]
    if active is None:
        return [Decision(new, S.ACTIVE, 'Eerste import van deze dataset.')]
    new_day, active_day = measured_day(new), measured_day(active)
    if new_day < active_day:
        return [Decision(new, S.HISTORICAL, f'Meetmoment {new_day:%d-%m-%Y} is ouder dan dat van de actieve import '
                         f'{describe(active)} op {active_day:%d-%m-%Y}. Bewaard voor trends.')]
    if new_day > active_day:
        reason = f'Nieuwer meetmoment ({new_day:%d-%m-%Y}) dan de actieve import {describe(active)} op {active_day:%d-%m-%Y}.'
        return [Decision(active, S.SUPERSEDED, f'Vervangen door {describe(new)} met een nieuwer meetmoment.', new),
                *(Decision(item, S.SUPERSEDED, f'Vervangen door {describe(new)} met een nieuwer meetmoment.', new) for item in supplements),
                Decision(new, S.ACTIVE, reason)]
    result = compare(new, active, cache)
    if result.conflicts:
        return [Decision(new, S.CONFLICT, f'Zelfde meetmoment als de actieve import {describe(active)}, maar {result.conflicts} '
                         f'gedeelde URLs hebben afwijkende waarden (bijv. {", ".join(result.examples)}). Kies handmatig welke import actief is.')]
    if not result.new_only and not result.new_extra_columns:
        return [Decision(new, S.REDUNDANT, f'Alle URLs en kolommen zitten met dezelfde waarden al in de actieve import {describe(active)}.')]
    if not result.new_only:
        return [Decision(new, S.SUPPLEMENT, f'Deelverzameling van de URLs van de actieve import {describe(active)} met gelijke waarden, '
                         f'maar met extra kolommen: {columns_text(result.new_extra_columns)}. Telt mee voor die kolommen.')]
    decisions = []
    if result.other_extra_columns:
        decisions.append(Decision(active, S.SUPPLEMENT, f'Als actieve import vervangen door completere {describe(new)}, maar blijft '
                                  f'meetellen voor de kolommen {columns_text(result.other_extra_columns)}.', new))
    else:
        decisions.append(Decision(active, S.SUPERSEDED, f'Vervangen door completere {describe(new)} met hetzelfde meetmoment.', new))
    for item in supplements:
        check = compare(item, new, cache)
        if not check.conflicts and not check.new_only and not check.new_extra_columns:
            decisions.append(Decision(item, S.REDUNDANT, f'Volledig gedekt door de nieuwe actieve import {describe(new)}.'))
    reason = f'Completer dan {describe(active)} met hetzelfde meetmoment: {result.new_only} extra URLs'
    reason += f' en {len(result.new_extra_columns)} extra kolommen.' if result.new_extra_columns else '.'
    decisions.append(Decision(new, S.ACTIVE, reason))
    return decisions


def write(decisions):
    # Demote before promoting so the one-active constraint holds at every statement.
    for decision in sorted(decisions, key=lambda item: item.status == S.ACTIVE):
        batch = decision.batch
        batch.snapshot_status, batch.snapshot_reason = decision.status, decision.reason
        if decision.superseded_by is not None:
            batch.superseded_by = decision.superseded_by
        elif decision.status == S.ACTIVE:
            batch.superseded_by = None
        batch.save(update_fields=['snapshot_status', 'snapshot_reason', 'superseded_by', 'dataset_key'])


@transaction.atomic
def classify(batch):
    """Classify a just-published import against its dataset and store the outcome."""
    from projects.models import Project
    Project.objects.select_for_update().get(pk=batch.project_id)
    batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
    batch.dataset_key = dataset_key(batch)
    dataset = ImportBatch.objects.published().filter(project_id=batch.project_id, dataset_key=batch.dataset_key).exclude(pk=batch.pk)
    active = dataset.filter(snapshot_status=S.ACTIVE).first()
    supplements = list(dataset.filter(snapshot_status=S.SUPPLEMENT))
    cache = {}
    decisions = decide(batch, active, supplements, cache)
    batch.quality_warnings = quality_warnings(batch, active, cache)
    batch.save(update_fields=['dataset_key', 'quality_warnings'])
    for decision in decisions:
        decision.batch.dataset_key = batch.dataset_key
    write(decisions)
    batch.refresh_from_db()
    return batch


def plan_project(project):
    """Replay all published imports per dataset in measurement order; nothing is written."""
    by_dataset = {}
    for batch in ImportBatch.objects.published().filter(project=project):
        batch.dataset_key = dataset_key(batch)
        by_dataset.setdefault(batch.dataset_key, []).append(batch)
    outcome = {}
    for key, batches in sorted(by_dataset.items()):
        batches.sort(key=lambda item: (measured_day(item), item.imported_at, str(item.pk)))
        cache, active, supplements = {}, None, []
        for batch in batches:
            warnings = quality_warnings(batch, active, cache)
            for decision in decide(batch, active, supplements, cache):
                outcome[decision.batch.pk] = {'batch': decision.batch, 'status': decision.status, 'reason': decision.reason,
                    'superseded_by': decision.superseded_by, 'warnings': outcome.get(decision.batch.pk, {}).get('warnings', [])}
                if decision.status == S.ACTIVE:
                    active = decision.batch
                supplements = [item for item in supplements if item.pk != decision.batch.pk]
                if decision.status == S.SUPPLEMENT:
                    supplements.append(decision.batch)
            outcome[batch.pk]['warnings'] = warnings
    return outcome


@transaction.atomic
def apply_plan(project, outcome):
    from projects.models import Project
    Project.objects.select_for_update().get(pk=project.pk)
    # Clear first: re-classification may move "active" to another import.
    ImportBatch.objects.filter(project=project).update(snapshot_status=None)
    decisions = []
    for item in outcome.values():
        batch = item['batch']
        batch.quality_warnings = item['warnings']
        batch.save(update_fields=['quality_warnings', 'dataset_key'])
        decisions.append(Decision(batch, item['status'], item['reason'], item['superseded_by']))
    write(decisions)
    from .inventory_index import invalidate
    invalidate(project.pk)


@transaction.atomic
def activate(batch, actor):
    """Human decision for a conflict: make `batch` the active snapshot of its dataset."""
    from projects.models import Project
    Project.objects.select_for_update().get(pk=batch.project_id)
    batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.snapshot_status != S.CONFLICT:
        raise ValueError('Alleen een import met een conflict kan handmatig actief worden gemaakt.')
    stamp = f'{actor.get_username()} op {timezone.localtime():%d-%m-%Y %H:%M}'
    decisions = [Decision(batch, S.ACTIVE, f'Handmatig actief gemaakt door {stamp}. Eerder: {batch.snapshot_reason}')]
    current = ImportBatch.objects.filter(project_id=batch.project_id, dataset_key=batch.dataset_key,
                                         snapshot_status=S.ACTIVE).exclude(pk=batch.pk).first()
    if current is not None:
        decisions.insert(0, Decision(current, S.SUPERSEDED, f'Handmatig vervangen door {describe(batch)} ({stamp}).', batch))
    write(decisions)
    return batch
