"""SQL filters, facets, metrics and ordering for the inventory read model."""
from django.db import connection, transaction
from django.db.models import Count, F, Q, FilteredRelation
from django.db.models.functions import Collate
from functools import wraps
from .models import InventoryValue
from .column_fields import value_key, typed_value
from .configurable_inventory import build_table
from .inventory_index import InventoryRows


def snapshot(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if connection.in_atomic_block:
            return view(*args, **kwargs)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            return view(*args, **kwargs)
    return wrapped


class DatabaseRows(InventoryRows):
    def __init__(self, inventory, query=None):
        self.inventory = inventory
        super().__init__(query if query is not None else inventory.query)
        if query is None: self.total_count = inventory.state.url_count

    def choices_for(self, field):
        # Only cardinality up to ten is needed to choose a filter mode.
        definition = self.inventory.catalog.get(field.get('field_id', field['id']), {})
        return [(value_key(item['value'], field['type']), typed_value(item['value'], field['type']))
                for item in definition.get('choice_samples', {}).get(field['type'], [])]

    def values(self, field_key, query=None):
        return InventoryValue.objects.filter(url__in=(self.query if query is None else query).order_by(), field_key=field_key)

    def table(self, columns, params):
        # The established parser also generates all links, error messages and
        # state. Give it only choice representatives, never project URL rows.
        representatives = []
        for column in columns:
            if column['mode'] != 'choices': continue
            kind = column['type']
            column['_options'] = dict(self.inventory.choices(column))
            for value in column['_options'].values():
                representatives.append({'url': str(len(representatives)), 'field_values': {
                    column['field_id']: {'value': value}}})
        _, controls = build_table(representatives, columns, params)
        aliases = {key: 'inventory_value_'+str(index) for index, key in enumerate(sorted({column['field_id'] for column in columns}))}
        query = self.query.alias(**{alias: FilteredRelation('values', condition=Q(values__field_key=key)) for key, alias in aliases.items()})
        search = controls['url_query']
        if search: query = query.filter(url_folded__contains=search.casefold())
        searched = query

        def apply(query, selected):
            for column in selected:
                if not column['active']: continue
                if column['error']: return query.none()
                kind = column['type']
                attr = {'number': 'number', 'date': 'date'}.get(kind, 'folded')
                prefix = aliases[column['field_id']] + '__'
                present = Q(**{prefix+attr+'__isnull': False})
                if column['mode'] == 'choices':
                    chosen = [value for key, value in column.get('_options', {}).items() if key in column['selected']]
                    if kind == 'text':
                        condition = Q(**{prefix+'choice_key__in': column['selected'] - {'__missing__'}})
                    else:
                        match_attr = {'number': 'number', 'date': 'date'}[kind]
                        condition = Q(**{prefix+match_attr+'__in': chosen})
                    if '__missing__' in column['selected']: condition |= ~present
                elif column['missing'] == 'only':
                    condition = ~present
                else:
                    condition = present
                    if column['mode'] == 'keyword':
                        condition &= Q(**{prefix+'folded__contains': column['keyword'].casefold()})
                    else:
                        if column['lower'] is not None: condition &= Q(**{prefix+attr+'__gte': column['lower']})
                        if column['upper'] is not None: condition &= Q(**{prefix+attr+'__lte': column['upper']})
                    include_missing = column['missing'] == 'include' or column['missing'] == 'auto' and not (column['low'] or column['high'] or column['keyword'])
                    if include_missing: condition |= ~present
                query = query.filter(condition)
            return query

        query = apply(query, columns)
        unfiltered = not search and not any(column['active'] for column in columns)
        aggregate = {}
        if unfiltered:
            count = self.inventory.state.url_count
            metric_values = self.inventory.state.metrics
            for column in columns:
                definition = self.inventory.catalog.get(column['field_id'], {})
                column['present_count'] = definition.get('known_counts', {}).get(column['type'], 0)
        else:
            aggregate['total'] = Count('pk')
            for key in ('top3', 'top10', 'not_found'):
                aggregate[key+'_known'] = Count(key)
                aggregate[key+'_positive'] = Count('pk', filter=Q(**{key: True}))
            for index, column in enumerate(columns):
                attr = {'number': 'number', 'date': 'date'}.get(column['type'], 'text')
                aggregate['present_'+str(index)] = Count(aliases[column['field_id']]+'__'+attr)
            measured = query.aggregate(**aggregate)
            count = measured['total']
            metric_values = {key: measured[key+'_positive'] if measured[key+'_known'] else None
                             for key in ('top3', 'top10', 'not_found')}
            for index, column in enumerate(columns): column['present_count'] = measured['present_'+str(index)]
        for column in columns:
            column['total_count'] = count
            others = [other for other in columns if other['id'] != column['id']]
            base_facet = not search and not any(other['active'] for other in others)
            definition = self.inventory.catalog.get(column['field_id'], {})
            attr = {'number': 'number', 'date': 'date'}.get(column['type'], 'text')
            samples = definition.get('choice_samples', {}).get(column['type'], [])
            if base_facet:
                facet_count = self.inventory.state.url_count
                known = definition.get('known_counts', {}).get(column['type'], 0)
            elif not column['active']:
                facet_count, known = count, column['present_count']
            else:
                facet = apply(searched, others)
                stats = facet.aggregate(total=Count('pk'), known=Count(aliases[column['field_id']]+'__'+attr))
                facet_count, known = stats['total'], stats['known']
            if column['mode'] == 'choices':
                if base_facet and len(samples) < 10:
                    counts = {value_key(item['value'], column['type']): item['count'] for item in samples}
                else:
                    facet = apply(searched, others)
                    value_path = aliases[column['field_id']]+'__'+attr
                    grouped = facet.filter(**{value_path+'__isnull': False}).order_by().values(value_path).annotate(n=Count('pk'))
                    counts = {value_key(value, column['type']): amount for value, amount in grouped.values_list(value_path, 'n')}
                for option in column['choices']: option['count'] = counts.get(option['key'], 0)
            column['missing_count'] = facet_count - known
        sort_id, _, direction = params.get('sort', '').partition(':')
        sort_column = next((column for column in columns if column['id'] == sort_id), None)
        if sort_column is not None and direction in ('asc', 'desc'):
            attr = {'number': 'number', 'date': 'date'}.get(sort_column['type'], 'folded')
            query = query.annotate(inventory_sort=F(aliases[sort_column['field_id']]+'__'+attr))
            sort_value = Collate('inventory_sort', 'C') if attr == 'folded' else F('inventory_sort')
            order = sort_value.asc(nulls_last=True) if direction == 'asc' else sort_value.desc(nulls_last=True)
            query = query.order_by(order, Collate('path', 'C'), Collate('url', 'C'), 'pk')
        controls['url_filtered_count'] = count
        controls['inventory_metrics'] = metric_values
        result = TableRows(query, columns, params)
        result.total_count = count
        return result, controls


class TableRows(InventoryRows):
    def __init__(self, query, columns, params):
        self.columns = columns
        self.params = params
        super().__init__(query, {column['field_id'] for column in columns})

    def load(self, entries):
        rows = super().load(entries)
        # Only format the selected page; filters/sorting already happened in SQL.
        from django.http import QueryDict
        keys = {'id', 'field_id', 'name', 'type', 'mode', 'presentation', 'source', 'available'}
        formatting_columns = [{key: value for key, value in column.items() if key in keys} for column in self.columns]
        build_table(rows, formatting_columns, QueryDict())
        if hasattr(self, 'visible_count'):
            for row in rows: row['value_cells'] = row['value_cells'][:self.visible_count]
        return rows


def metrics(query):
    keys = ('top3', 'top10', 'not_found')
    aggregates = {}
    for key in keys:
        aggregates[key+'_known'] = Count(key)
        aggregates[key+'_positive'] = Count('pk', filter=Q(**{key: True}))
    counts = query.aggregate(**aggregates)
    return {key: counts[key+'_positive'] if counts[key+'_known'] else None for key in keys}


def save_view_preference(project, user, defaults):
    """Write outside the read snapshot to avoid conflicts between parallel tabs."""
    from django.conf import settings
    from .models import ProjectViewPreference
    def save():
        ProjectViewPreference.objects.update_or_create(project=project, user=user, defaults=defaults)
    if connection.in_atomic_block and not getattr(settings, 'INVENTORY_BUILD_INLINE', False):
        transaction.on_commit(save)
    else:
        save()
