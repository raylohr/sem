"""Sorting and faceting for the project inventory, before pagination."""
from collections import Counter
from urllib.parse import urlencode
from django.utils.text import slugify


def type_option(row):
    badge = row['observations'].get('sf', {}).get('badge')
    if not badge:
        return 'not-observed', 'Niet gemeten door SF'
    return slugify(badge['label']) or 'unknown', badge['label']


def state_query(query, sort, active, selected):
    values = []
    if query:
        values.append(('url_q', query))
    if sort:
        values.append(('traffic_sort', sort))
    if active:
        values.append(('content_filter', '1'))
        values.extend(('content_type', key) for key in selected)
    return urlencode(values)


def inventory_controls(rows, columns, params):
    query = params.get('url_q', '').strip()[:500]
    has_traffic = any(column['key'] == 'ahrefs' for column in columns)
    has_content = any(column['key'] == 'sf' for column in columns)
    sort = params.get('traffic_sort', '') if has_traffic else ''
    if sort not in ('asc', 'desc'):
        sort = ''
    active = params.get('content_filter') == '1' and has_content
    labels = dict(type_option(row) for row in rows)
    selected = sorted(set(params.getlist('content_type')) & labels.keys()) if active else []
    searched = [row for row in rows if not query or query.casefold() in row['url'].casefold()]
    counts = Counter(type_option(row)[0] for row in searched)
    choices = [{'key': key, 'label': label, 'count': counts[key],
                'checked': not active or key in selected}
               for key, label in sorted(labels.items(), key=lambda item: item[1].casefold())]
    result = [row for row in searched if not active or type_option(row)[0] in selected]
    if sort:
        def traffic_order(row):
            value = row['observations'].get('ahrefs', {}).get('traffic')
            return (value is None, (value if sort == 'asc' else -value) if value is not None else 0,
                    row['path'], row['url'])
        result = sorted(result, key=traffic_order)
    next_sort = {'': 'asc', 'asc': 'desc', 'desc': ''}[sort]
    current_query = state_query(query, sort, active, selected)
    next_query = state_query(query, next_sort, active, selected)
    reset_query = state_query(query, sort, False, [])
    return result, {
        'url_query': query, 'traffic_sort': sort,
        'traffic_aria_sort': {'asc': 'ascending', 'desc': 'descending'}.get(sort, 'none'),
        'traffic_sort_url': '?' + next_query + '#url-inventory',
        'traffic_sort_action': {'asc': 'Sorteer oplopend op organisch verkeer',
                                'desc': 'Sorteer aflopend op organisch verkeer',
                                '': 'Herstel sortering op URL-pad'}[next_sort],
        'url_sort_description': {'asc': 'Organisch verkeer oplopend; onbekende waarden onderaan.',
                                 'desc': 'Organisch verkeer aflopend; onbekende waarden onderaan.'}.get(sort, 'Gesorteerd op URL-pad.'),
        'content_filter_active': active, 'content_choices': choices,
        'selected_content_types': selected, 'content_filter_count': len(selected),
        'content_filter_reset_url': '?' + reset_query + '#url-inventory',
        'url_state_query': current_query, 'url_filtered_count': len(result),
        'has_content_column': has_content,
    }
