"""Shared ranking-history coverage planning."""
from datetime import date, timedelta
from django.core.management.base import CommandError
from django.utils import timezone


def history_bounds(payload):
    first, current = [], []
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('first', 'current') and isinstance(item, str):
                    try:
                        parsed = date.fromisoformat(item)
                    except ValueError:
                        continue
                    (first if key == 'first' else current).append(parsed)
                else:
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    collect(payload)
    if not first or not current:
        raise CommandError('SE Ranking geeft geen eerste/laatste controledatum; historie is niet stilzwijgend beperkt.')
    start, end = min(first), min(max(current), timezone.localdate())
    if end < start:
        raise CommandError('Ongeldige historieperiode ontvangen van SE Ranking.')
    return start, end


def missing_periods(start, end, covered):
    """Yield non-overlapping windows of at most 31 days, skipping complete imports."""
    cursor = start
    for left, right in sorted(covered):
        if right < cursor:
            continue
        if left > end:
            break
        stop = min(end, left-timedelta(days=1))
        while cursor <= stop:
            last = min(stop, cursor+timedelta(days=30))
            yield cursor, last
            cursor = last+timedelta(days=1)
        cursor = max(cursor, right+timedelta(days=1))
    while cursor <= end:
        last = min(end, cursor+timedelta(days=30))
        yield cursor, last
        cursor = last+timedelta(days=1)

