"""Rebuildable database read model. No full-project Python lists in HTTP requests."""
import json
from datetime import date
from decimal import Decimal
from itertools import groupby
from uuid import uuid4

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.db.models import F, Value, TextField
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Coalesce, NullIf, Collate
from django.utils.dateparse import parse_datetime

from projects.models import Project
from .models import InventoryState, InventoryURL, InventoryField, InventoryValue, SourceRecord
from .column_fields import field_catalog, number, calendar_date, present, typed_value, value_key
from .url_inventory import project_inventory, URL_FIELDS, DEFAULT_SOURCES


class InventoryPending(ValueError):
    pass


class InventoryChanged(Exception):
    pass


def json_value(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def restore(value):
    """Restore the existing presentation contract after JSON storage."""
    if isinstance(value, list):
        return [restore(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in ('rank', 'title_rank', 'index_rank', 'sf_response_rank') and item:
            # Source rank: (day, explicit day, import time, record id).
            offset = 1 if key == 'title_rank' else 0
            item = list(item)
            item[offset] = date.fromisoformat(item[offset])
            item[offset + 2] = parse_datetime(item[offset + 2])
            result[key] = tuple(item)
        elif key in ('date', 'title_date', 'index_date') and item:
            result[key] = date.fromisoformat(item)
        elif key == 'traffic' and item is not None:
            result[key] = Decimal(item)
        else:
            result[key] = restore(item)
    return result


def invalidate(project_id):
    state, _ = InventoryState.objects.get_or_create(project_id=project_id)
    InventoryState.objects.filter(pk=state.pk).update(revision=F('revision') + 1)


def url_expression():
    expressions = []
    for key in URL_FIELDS:
        expression = KeyTextTransform(key, 'data')
        # Python's primary-URL lookup skips false/zero/empty containers too.
        # The same strings cannot be valid URLs; rejecting those strings later
        # still follows the original lookup, so grouping remains exact.
        for empty in ('', 'false', '0', '0.0', '[]', '{}'):
            expression = NullIf(expression, Value(empty), output_field=TextField())
        expressions.append(expression)
    return Coalesce(*expressions, output_field=TextField())


def rebuild(project_id):
    """Serialize builders; publish atomically only if inputs did not change.

    PostgreSQL sorts source records on disk as necessary; Python retains a single
    URL's observations, 25 projected URLs and at most 250 insert objects. Readers never build this.
    """
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))', [str(project_id)])
            if not cursor.fetchone()[0]:
                return False
        project = Project.objects.get(pk=project_id)
        state, _ = InventoryState.objects.get_or_create(project=project)
        revision = state.revision
        generation = uuid4()
        batches = {batch.pk: batch for batch in project.import_batches.analysable()}
        records = SourceRecord.objects.filter(batch_id__in=batches).annotate(
            inventory_address=url_expression()).order_by('inventory_address', 'id').values_list(
                'inventory_address', 'id', 'batch_id', 'data')
        summaries = {}
        buffered = []
        processed_urls = 0
        metric_totals = {key: {'known': 0, 'positive': 0} for key in ('top3', 'top10', 'not_found')}

        def flush():
            if not buffered: return
            InventoryURL.objects.bulk_create([entry for entry, _ in buffered], batch_size=25)
            pending = []
            for entry, observations in buffered:
                for key, observation in observations.items():
                    value = observation['value']
                    text = str(value) if present(value) else None
                    pending.append(InventoryValue(url=entry, field_key=key, observation=json_value(observation),
                        text=text, folded=text.casefold() if text is not None else None,
                        number=number(value), date=calendar_date(value), choice_key=value_key(value, 'text')))
                    if len(pending) == 250:
                        InventoryValue.objects.bulk_create(pending, batch_size=250)
                        pending = []
            if pending: InventoryValue.objects.bulk_create(pending, batch_size=250)
            buffered.clear()
        for _, group in groupby(records.iterator(chunk_size=250), key=lambda item: item[0]):
            indexing = {}

            def observations():
                from .excel_inventory import gsc_evidence
                from .url_inventory import observation_date
                for _, rid, bid, data in group:
                    batch = batches[bid]
                    if batch.source == 'gsc':
                        status, reason, _ = gsc_evidence(data, batch.request_metadata)
                        if status or reason:
                            stamp, fallback = observation_date(data, batch)
                            rank = (stamp, not fallback, batch.imported_at, rid)
                            if not indexing or rank > indexing['rank']:
                                indexing.update(rank=rank, record_id=rid, date=stamp)
                    yield rid, bid, data

            _, rows = project_inventory(project, include_fields=True,
                _records=observations(), _batches=batches)
            for row in rows:
                if indexing: row['indexing_record_id'] = indexing['record_id']
                values = row.pop('field_values', {})
                row.pop('cells', None)
                from .configurable_inventory import inventory_metrics
                metrics = inventory_metrics([{**row, 'field_values': values}])
                for key, value in metrics.items():
                    metric_totals[key]['known'] += value is not None
                    metric_totals[key]['positive'] += bool(value)
                entry = InventoryURL(project=project, generation=generation,
                    url=row['url'], path=row['path'], url_folded=row['url'].casefold(), payload=json_value(row),
                    **{key: None if value is None else bool(value) for key, value in metrics.items()})
                for key, observation in values.items():
                    value = observation['value']
                    summary = summaries.setdefault(key, {'definition': observation['field'], 'numeric': True,
                        'dates': True, 'present': False,
                        'known': {kind: 0 for kind in ('text', 'number', 'date')},
                        'samples': {kind: {} for kind in ('text', 'number', 'date')}})
                    for kind in ('text', 'number', 'date'):
                        typed = typed_value(value, kind)
                        if typed is not None:
                            summary['known'][kind] += 1
                            samples = summary['samples'][kind]
                            if typed in samples or len(samples) < 10:
                                samples[typed] = samples.get(typed, 0) + 1
                    if present(value):
                        summary['present'] = True
                        summary['numeric'] &= number(value) is not None
                        summary['dates'] &= (isinstance(value, date) or isinstance(value, str) and len(value) == 10) and calendar_date(value) is not None
                processed_urls += 1
                if processed_urls % 1000 == 0 and InventoryState.objects.get(project=project).revision != revision:
                    raise InventoryChanged()
                buffered.append((entry, values))
                if len(buffered) == 25: flush()
        flush()
        definitions = {}
        for key, summary in summaries.items():
            # Reuse the existing name rules; infer unknown field types from all
            # latest values, without retaining those values in memory.
            definition = summary['definition']
            probe = field_catalog([{'field_values': {key: {'field': definition, 'value': 'not numeric'}}}])[key]
            name_type = probe['type']
            label = definition['label'].lower()
            force_text = any(word in label for word in ('title', 'description', 'address', 'url', 'canonical', 'name')) and not any(word in label for word in ('length', 'width', 'count', 'score'))
            kind = 'number' if name_type == 'number' or not force_text and summary['present'] and summary['numeric'] else 'date' if not force_text and summary['present'] and summary['dates'] else 'text'
            definitions[key] = {**definition, 'type': kind, 'known_counts': summary['known'],
                'choice_samples': {kind: [{'value': value, 'count': count} for value, count in samples.items()]
                                   for kind, samples in summary['samples'].items()}}
        InventoryField.objects.bulk_create([InventoryField(project=project, generation=generation,
            key=key, definition=json_value(definition)) for key, definition in definitions.items()], batch_size=250)
        current = InventoryState.objects.select_for_update().get(project=project)
        if current.revision != revision:
            raise InventoryChanged()
        # Old generations are removed in this transaction. HTTP readers use a
        # repeatable-read snapshot so an in-flight page remains consistent.
        current.generation = generation
        current.built_revision = revision
        current.url_count = processed_urls
        current.metrics = {key: value['positive'] if value['known'] else None for key, value in metric_totals.items()}
        current.save(update_fields=['generation', 'built_revision', 'url_count', 'metrics', 'updated_at'])
        old_urls = InventoryURL.objects.filter(project=project).exclude(generation=generation)
        # These derived tables have no deletion hooks. Delete children first,
        # then URLs directly: Django's cascade collector materializes every old
        # URL payload, which can exceed the worker's memory limit on a rebuild.
        InventoryValue.objects.filter(url__in=old_urls.values('pk'))._raw_delete('default')
        old_urls._raw_delete('default')
        InventoryField.objects.filter(project=project).exclude(generation=generation).delete()
        return True


def ensure(project):
    state, _ = InventoryState.objects.get_or_create(project=project)
    if state.generation is None or state.revision != state.built_revision:
        if getattr(settings, 'INVENTORY_BUILD_INLINE', False):
            rebuild(project.pk)
            state.refresh_from_db()
        else:
            # Beat retries pending projects, even after a broker/worker restart.
            raise InventoryPending('Het URL-overzicht wordt bijgewerkt. Probeer over enkele ogenblikken opnieuw.')
    return state


class InventoryRows:
    """A sliceable, bounded loader for Django's Paginator and streaming exports."""
    def __init__(self, query, field_keys=None, include_data=False):
        self.query = query
        self.field_keys = field_keys
        self.include_data = include_data

    def count(self):
        return self.total_count if hasattr(self, 'total_count') else self.query.count()

    def __len__(self):
        return self.count()

    def __getitem__(self, key):
        if isinstance(key, slice):
            return self.load(list(self.query[key]))
        return self.load([self.query[key]])[0]

    def load(self, entries):
        by_id = {entry.pk: restore(entry.payload) for entry in entries}
        for row in by_id.values(): row['field_values'] = {}
        values = InventoryValue.objects.filter(url_id__in=by_id)
        if self.field_keys is not None: values = values.filter(field_key__in=self.field_keys)
        for item in values.iterator(chunk_size=250):
            by_id[item.url_id]['field_values'][item.field_key] = restore(item.observation)
        if self.include_data:
            record_ids = {obs['record_id'] for row in by_id.values() for obs in row['observations'].values()}
            record_ids.update(row['indexing_record_id'] for row in by_id.values() if row.get('indexing_record_id'))
            records = {record.pk: record for record in SourceRecord.objects.filter(pk__in=record_ids).select_related('batch')}
            for row in by_id.values():
                if row.get('indexing_record_id'):
                    record = records[row['indexing_record_id']]
                    from .url_inventory import observation_date
                    row['_indexing'] = {'data': record.data, 'metadata': record.batch.request_metadata,
                        'date': observation_date(record.data, record.batch)[0]}
                for observation in row['observations'].values():
                    record = records[observation['record_id']]
                    observation.update(data=record.data, metadata=record.batch.request_metadata)
        return list(by_id.values())

    def __iter__(self):
        # Keyset traversal of an already ordered queryset is not safe for arbitrary
        # field sorting; server-side cursor provides bounded reads instead.
        entries = []
        for entry in self.query.iterator(chunk_size=100):
            entries.append(entry)
            if len(entries) == 100:
                yield from self.load(entries)
                entries = []
        if entries: yield from self.load(entries)


class Inventory:
    def __init__(self, project):
        self.project = project
        self.state = ensure(project)
        self.query = InventoryURL.objects.filter(project=project, generation=self.state.generation).order_by(Collate('path', 'C'), Collate('url', 'C'), 'pk')
        self.catalog = {item.key: item.definition for item in InventoryField.objects.filter(project=project, generation=self.state.generation)}
        self.rows = InventoryRows(self.query)
        self.rows.total_count = self.state.url_count

    def choices(self, field):
        kind = field['type']
        field_key = field.get('field_id', field['id'])
        definition = self.catalog.get(field_key, {})
        samples = definition.get('choice_samples', {}).get(kind, [])
        if len(samples) < 10:
            return sorted(((value_key(item['value'], kind), typed_value(item['value'], kind)) for item in samples),
                key=lambda item: item[1].casefold() if isinstance(item[1], str) else item[1])
        column = {'number': 'number', 'date': 'date'}.get(kind, 'text')
        values = InventoryValue.objects.filter(url__in=self.query.order_by(), field_key=field_key)
        options = values.exclude(**{column+'__isnull': True}).values_list(column, flat=True).distinct()
        return sorted(((value_key(value, kind), value) for value in options),
                      key=lambda item: item[1].casefold() if isinstance(item[1], str) else item[1])

    def source_columns(self):
        sources = {field['source'] for field in self.catalog.values()}
        return [{'key': key, 'label': DEFAULT_SOURCES.get(key, key.capitalize())} for key in sorted(sources, key=lambda key: (list(DEFAULT_SOURCES).index(key) if key in DEFAULT_SOURCES else 99, key))]
