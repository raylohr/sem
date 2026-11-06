from projects.hosts import filter_records


def filter_file_rows(project, rows):
    items = [{'locator': str(i), 'data': row} for i, row in enumerate(rows, 1)]
    accepted, excluded = filter_records(project, items)
    return [item['data'] for item in accepted], excluded


def filter_staged_batch(batch, project):
    """Recheck current rules at preview/confirmation, in case project settings changed."""
    rows = list(batch.records.order_by('id'))
    accepted, rejected = [], []
    from projects.hosts import allows_url
    for row in rows:
        if allows_url(project, row.data.get('Address') or row.data.get('URL')):
            accepted.append(row)
        else:
            rejected.append(row.pk)
    if rejected:
        batch.records.filter(pk__in=rejected).delete()
        summary = batch.request_metadata
        summary['excluded_rows'] = summary.get('excluded_rows', 0) + len(rejected)
        summary['host_rules'] = project.host_rules
        from collections import Counter
        from urllib.parse import urlsplit
        summary['hosts'] = dict(Counter(urlsplit(row.data.get('Address') or row.data.get('URL')).hostname for row in accepted))
        if batch.source == 'sf':
            summary['html_count'] = sum(row.data.get('Content Type', '').lower().startswith(('text/html', 'application/xhtml+xml')) for row in accepted)
        batch.request_metadata = summary
        batch.record_count = len(accepted)
        batch.save(update_fields=['record_count', 'request_metadata'])
    return accepted
