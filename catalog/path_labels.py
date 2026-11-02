"""Deterministic path classification; preserves existing assignments and labels."""
from urllib.parse import urlsplit
from django.core.exceptions import PermissionDenied
from django.db import transaction
from projects.models import Project
from projects.permissions import can_edit
from .models import Label, URLLabel


@transaction.atomic
def assign_path_labels(*, project_id, actor, language='en', apply=False):
    if language not in {'nl', 'en', 'both'}:
        raise ValueError('Onbekende taalkeuze')
    project = Project.objects.select_for_update().get(pk=project_id)
    if not can_edit(actor, project):
        raise PermissionDenied
    host = (urlsplit(project.website).hostname or '').lower()
    hosts = {host}
    if host.startswith('www.'):
        hosts.add(host[4:])
    elif host:
        hosts.add('www.' + host)
    fields = [('nl', 'url_path'), ('en', 'url_path_en')]
    if language != 'both':
        fields = [item for item in fields if item[0] == language]
    rules = []
    skipped = []
    for label in project.labels.filter(label_type=Label.Type.EXPERTISE):
        for lang, field in fields:
            path = getattr(label, field)
            if not path:
                skipped.append({'label': label.name, 'language': lang, 'reason': 'URL-pad niet bevestigd'})
                continue
            parsed = urlsplit(path)
            if not path.startswith('/') or path.startswith('//') or parsed.query or parsed.fragment or path == '/':
                raise ValueError(f'Ongeldig regelpad voor {label.name}')
            if lang == 'en' and not path.startswith('/en/'):
                raise ValueError(f'Geen Engels pad voor {label.name}')
            rules.append((label, lang, path.rstrip('/')))
    existing = set(URLLabel.objects.filter(url__project=project).values_list('url_id', 'label_id'))
    pairs = set()
    matches = []
    for url in project.urls.all().iterator(chunk_size=500):
        parsed = urlsplit(url.normalized_url)
        if parsed.scheme not in {'http', 'https'} or parsed.hostname not in hosts:
            continue
        for label, lang, base in rules:
            pair = (url.pk, label.pk)
            if pair in pairs or not (parsed.path == base or parsed.path.startswith(base + '/')):
                continue
            pairs.add(pair)
            matches.append({'url_id': str(url.pk), 'url': url.normalized_url, 'label_id': label.pk,
                'label': label.name, 'language': lang, 'rule_path': base + '/', 'existing': pair in existing})
    new_pairs = pairs - existing
    if apply:
        URLLabel.objects.bulk_create([URLLabel(url_id=url_id, label_id=label_id) for url_id, label_id in new_pairs], ignore_conflicts=True)
    return {'project_id': str(project.pk), 'actor': actor.username, 'method': 'url_path', 'rule_version': 1,
        'language': language, 'dry_run': not apply, 'matched_assignments': len(pairs),
        'unique_urls': len({u for u, _ in pairs}), 'new_assignments': len(new_pairs),
        'created_assignments': len(new_pairs) if apply else 0, 'skipped': skipped, 'matches': matches}
