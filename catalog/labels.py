"""Explicit audit classifications; no inference from prose, paths or navigation."""
import hashlib
import re
import unicodedata
from collections import Counter
from django.core.exceptions import PermissionDenied
from django.db import transaction
from projects.models import Project
from projects.permissions import can_edit
from sources.models import ImportBatch
from .adapters import normalize_url
from .models import Label, URLLabel, LabelEvidence, ProjectURL

EXPERTISES = (
    'Aansprakelijkheid', 'Arbeid', 'Bouw', 'Cassatie', 'Geschillen tussen bedrijven',
    'Erfenis', 'Familie & Echtscheiding', 'Bedrijfsfinanciering & zekerheden', 'Huur',
    'Bedrijven in financiële moeilijkheden', 'Letselschade', 'Mediation',
    'Geschillen en advies binnen de onderneming', 'Pensioen', 'Vastgoed',
)
SHEETS = {
    '01 - EXPERTISE OVERZICHT': ('URL expertisepagina', 'Expertise volgens overzichtspagina'),
    '03 - LIVE CONTROLE': ('URL', 'Expertise'),
    '02 - URL ACTIEPLAN': ('URL', 'Expertise / cluster'),
    'Inhoudelijke audit - paginas': ('URL', 'Expertise / pagina'),
    'Inhoudelijke audit - blogs': ('URL', 'Blog / cluster'),
    'Blogclusters - auditronde': ('Archief-URL', 'Blogcluster'),
    'BEVINDINGEN PER ONDERWERP URL': ('URL', 'Onderwerp / expertise'),
}
# These columns mix classifications with article titles and administrative headings.
# Recognize legal/functional label families; do not turn arbitrary titles into labels.
LEGAL = re.compile(r'^(aansprakelijkheid|arbeid|ambtenarenrecht|bouw|cassatie|geschillen|erf|famil|bedrij|huur|insolventie|letselschade|mediation|pensioen|vastgoed|onderneming|financiering|bestuursrecht|kunst|intellectueel|privacyrecht|ict-recht|procesrecht|verenigingsrecht|verbintenissenrecht|faillissement|partneralimentatie|vve|employment|tenancy|corporate|companies|company law|liability|real estate|pension|personal injury|inheritance|art law|commercial contracts|disputes|settlement agreement$|performance improvement plan$|executeur|liquidatie|zelfstandigen|loi /|turboliquidatie|en employment)', re.I)
FUNCTIONAL = re.compile(r'^(overview|overzicht|downloads|engelse blog|engels blog|engelstalige|alle blog|algemeen blog|blog / columns|columns$|column /|contact|advocaten|blogarchieven|algemene site|kantoor|english-language|lawyers)', re.I)


def label_key(name):
    return hashlib.sha256(unicodedata.normalize('NFC', name.strip()).casefold().encode()).hexdigest()


@transaction.atomic
def import_labels(*, batch_id, actor, dry_run=False):
    batch = ImportBatch.objects.select_related('project').get(pk=batch_id)
    project = Project.objects.select_for_update().get(pk=batch.project_id)
    if not can_edit(actor, project):
        raise PermissionDenied
    batch = ImportBatch.objects.select_for_update().get(pk=batch_id)
    if batch.source != 'audit' or batch.status != 'complete':
        raise ValueError('Een voltooide contentauditbron is vereist.')
    summary = Counter()
    unresolved = []
    labels = {}
    if not dry_run:
        for name in EXPERTISES:
            label, created = Label.objects.get_or_create(project=project, key=label_key(name), defaults={'name': name, 'kind': Label.Kind.EXPERTISE})
            labels[label.key] = label
            summary['labels_created'] += created
    urls = {u.normalized_url: u for u in ProjectURL.objects.filter(project=project)}
    seen_pairs = set()
    used_urls = set()
    for record in batch.records.order_by('id').iterator(chunk_size=200):
        data = record.data
        sheet = data.get('sheet')
        if sheet not in SHEETS:
            continue
        url_column, column = SHEETS[sheet]
        values = data.get('values', {})
        raw_url = values.get(url_column)
        raw_label = values.get(column)
        if not isinstance(raw_label, str) or not raw_label.strip():
            continue
        normalized = normalize_url(raw_url)
        if not normalized:
            summary['invalid_or_placeholder_urls'] += 1
            continue
        url = urls.get(normalized[0])
        if url is None:
            unresolved.append({'record': record.pk, 'locator': record.locator, 'url': raw_url})
            continue
        candidates = re.split(r'\n\s*\n', raw_label) if sheet == 'BEVINDINGEN PER ONDERWERP URL' else [raw_label]
        for name in candidates:
            name = name.strip()
            # Consolidation contains tab names and article titles as well as labels.
            if not (LEGAL.match(name) or FUNCTIONAL.match(name)):
                summary['non_label_values_skipped'] += 1
                continue
            key = label_key(name)
            pair = (url.pk, key)
            used_urls.add(url.pk)
            seen_pairs.add(pair)
            summary['evidence_candidates'] += 1
            if dry_run:
                continue
            label = labels.get(key)
            if label is None:
                kind = Label.Kind.FUNCTIONAL if FUNCTIONAL.match(name) else Label.Kind.SOURCE
                label, created = Label.objects.get_or_create(project=project, key=key, defaults={'name': name, 'kind': kind})
                if label_key(label.name) != key:
                    raise ValueError('Label identity collision')
                labels[key] = label
                summary['labels_created'] += created
            assignment, created = URLLabel.objects.get_or_create(url=url, label=label)
            summary['assignments_created'] += created
            _, created = LabelEvidence.objects.get_or_create(assignment=assignment, source_record=record, column=column,
                defaults={'original_label': raw_label, 'original_url': raw_url, 'imported_by': actor})
            summary['evidence_created'] += created
    summary['unique_urls'] = len(used_urls)
    summary['unique_assignments'] = len(seen_pairs)
    return {'counts': dict(summary), 'unmatched': unresolved, 'dry_run': dry_run}
