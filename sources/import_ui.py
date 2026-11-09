from urllib.parse import urlsplit
from datetime import datetime, time
from django.utils import timezone

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from core.ui import project_context
from projects.models import Project
from projects.permissions import can_edit
from .models import ImportBatch, SourceRecord
from .file_detection import detect_export
from .screamingfrog import content_checksum
from .host_filter import filter_file_rows, filter_staged_batch
from collections import Counter


class UploadForm(forms.Form):
    file = forms.FileField(label='Bestand', widget=forms.ClearableFileInput(attrs={
        'class': 'form-control', 'accept': '.csv,.xls,.xlsx,.zip,text/csv,application/zip,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}))


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def upload(request, project_id):
    context = project_context(request, project_id, 'sources')
    if not context['can_edit']:
        raise PermissionDenied
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        try:
            parsed = detect_export(form.cleaned_data['file'])
        except ValueError as exc:
            form.add_error('file', str(exc))
        else:
            with transaction.atomic():
                project = Project.objects.select_for_update().get(pk=context['project'].pk)
                if not can_edit(request.user, project):
                    raise PermissionDenied
                rows, excluded = filter_file_rows(project, parsed['rows'], parsed['summary'])
                summary = parsed['summary']
                batch = project.import_batches.filter(file_checksum=parsed['checksum']).first()
                if parsed['source'] == 'sf':
                    report = summary.get('type', 'internal_all')
                    fingerprint = content_checksum(rows, report)
                    summary['content_checksum'] = fingerprint
                    summary['content_checksum_version'] = 2
                    if batch is None:
                        candidates = project.import_batches.filter(source='sf', method='file')
                        if report == 'internal_all':
                            from django.db.models import Q
                            candidates = candidates.filter(Q(request_metadata__type=report) | ~Q(request_metadata__has_key='type'))
                        else:
                            candidates = candidates.filter(request_metadata__type=report)
                        batch = candidates.filter(request_metadata__content_checksum=fingerprint).first()
                        if batch is None:
                            # Older imports have only a raw file checksum. Compare their stored rows.
                            for candidate in candidates.iterator():
                                if candidate.request_metadata.get('content_checksum') and candidate.request_metadata.get('content_checksum_version') == 2:
                                    continue
                                if candidate.record_count != len(rows):
                                    continue
                                old_rows = candidate.records.values_list('data', flat=True).iterator()
                                try:
                                    matches = content_checksum(old_rows, report) == fingerprint
                                except (ValueError, TypeError):
                                    matches = False
                                if matches:
                                    batch = candidate
                                    break
                if batch is None:
                    summary['input_rows'] = len(parsed['rows'])
                    summary['empty_report'] = not parsed['rows'] and summary.get('allow_empty', False)
                    if not rows and not summary['empty_report']:
                        form.add_error('file', 'Geen URLs binnen de toegestane hosts van dit project. Kies het juiste project of pas de hostregels aan.')
                        context['form'] = form
                        return render(request, 'ui/import_upload.html', context)
                    summary.update(excluded_rows=excluded, host_rules=project.host_rules,
                                   hosts=dict(Counter(urlsplit(row.get('Address') or row.get('URL')).hostname for row in rows if row.get('Address') or row.get('URL'))))
                    if parsed['source'] in ('sf', 'ahrefs'):
                        summary['html_count'] = sum((row.get('Content Type') or row.get('Content type', '')).lower().startswith(('text/html', 'application/xhtml+xml')) for row in rows)
                    warnings = ['URLs buiten de toegestane projecthosts worden overgeslagen; ontbrekende URLs worden niet verwijderd.']
                    if summary.get('record_scope') == 'summary':
                        warnings = ['Dit is een samenvattend rapport zonder individuele URLs. Controleer dat het bij dit project hoort; de tabel blijft bij deze import bewaard.']
                    elif summary.get('record_scope') == 'external':
                        warnings = ['Dit rapport bevat externe URLs. Controleer dat het uit een crawl van dit project komt. Externe URLs blijven in dit bronrapport en komen niet in de projectinventaris.']
                    if summary.get('empty_report'):
                        warnings.append('Dit rapport is leeg. De import bewaart het rapporttype en de kolommen; er worden geen URL-records toegevoegd.')
                    if summary.get('archive_snapshot'):
                        warnings.append('Uit het crawlarchief worden algemene URL-velden en crawltijden gelezen. Linktabellen, geserialiseerde gegevens, pagina-inhoud en configuratie worden niet geïmporteerd. Gebruik de losse rapporten voor hreflang, indexeerbaarheid en probleemdetails.')
                    if summary.get('missing_report_context'):
                        warnings.append('Deze losse CSV bevat geen rapportmetadata of grafiek. Bevestig de categorie en vul eventueel de indexeringsreden in; het gerapporteerde totaal is onbekend.')
                    if summary.get('invalid_url_rows'):
                        warnings.append(f"{summary['invalid_url_rows']} ongeldige URL-rijen zijn door de hostcontrole uitgesloten.")
                    if summary.get('unknown_crawl_dates'):
                        warnings.append(f"{summary['unknown_crawl_dates']} crawl-datums met 1970-01-01 zijn als onbekend verwerkt.")
                    if summary.get('missing_expected_columns'):
                        warnings.append('Verwachte kolommen ontbreken in deze export: ' + ', '.join(summary['missing_expected_columns'])
                                        + '. Analyses die deze velden gebruiken (zoals de Performance-grafiek) tonen dan minder URLs. Voeg de kolommen toe in de kolomselectie van Ahrefs en exporteer opnieuw.')
                    if parsed['source'] == 'gsc':
                        warnings.append('GSC levert voorbeelden, geen volledige URL-lijst. De export kan maximaal 1.000 URL-voorbeelden bevatten.')
                    batch = ImportBatch.objects.create(
                        project=project, created_by=request.user, source=parsed['source'], method='file',
                        label=parsed['label'], file_checksum=parsed['checksum'],
                        measured_at=parsed['measured_at'], record_count=len(rows),
                        parser_version=parsed['parser_version'], request_metadata=summary, warnings=warnings)
                    SourceRecord.objects.bulk_create([
                        SourceRecord(batch=batch, locator=str(i), data=row)
                        for i, row in enumerate(rows, 1)], batch_size=100)
                elif batch.status == 'complete':
                    messages.info(request, 'Dit bestand is al geïmporteerd.')
                    return redirect('ui:batch', project_id=project.pk, batch_id=batch.pk)
                elif batch.created_by_id != request.user.pk:
                    form.add_error('file', 'Dit bestand wacht al op bevestiging door een andere gebruiker.')
                    batch = None
            if batch:
                return redirect('ui:import-preview', project_id=project.pk, batch_id=batch.pk)
    context['form'] = form
    return render(request, 'ui/import_upload.html', context)


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def preview(request, project_id, batch_id):
    context = project_context(request, project_id, 'sources')
    if not context['can_edit']:
        raise PermissionDenied
    observation_form = None
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=project_id)
        if not can_edit(request.user, project):
            raise PermissionDenied
        batch = get_object_or_404(ImportBatch.objects.select_for_update(), pk=batch_id,
                                 project=context['project'], method='file', created_by=request.user)
        if batch.status == 'complete':
            return redirect('ui:batch', project_id=project_id, batch_id=batch.pk)
        if batch.status != 'pending':
            raise PermissionDenied
        filter_staged_batch(batch, project)
        if batch.source == 'ahrefs':
            observation_form = ObservationDateForm(
                request.POST if request.method == 'POST' else None,
                initial={'observed_on': batch.request_metadata.get('suggested_date')})
        if request.method == 'POST':
            if request.POST.get('action') == 'cancel':
                batch.delete()
                return redirect('ui:sources', project_id=project_id)
            if request.POST.get('action') == 'confirm' and (batch.record_count or batch.request_metadata.get('empty_report')):
                if observation_form is not None:
                    if not observation_form.is_valid():
                        context.update(batch=batch, summary=batch.request_metadata,
                                       sample=batch.records.order_by('id')[:10], observation_form=observation_form)
                        return render(request, 'ui/import_preview.html', context)
                    observed_on = observation_form.cleaned_data['observed_on']
                    batch.measured_at = timezone.make_aware(datetime.combine(observed_on, time.min)) if observed_on else None
                    batch.request_metadata['observed_on'] = observed_on.isoformat() if observed_on else None
                    batch.request_metadata['date_basis'] = 'user_confirmed' if observed_on else 'import_time'
                if batch.source == 'gsc':
                    category = batch.request_metadata.get('report_status') if batch.request_metadata.get('reason_from_metadata') else request.POST.get('report_status', '')
                    if category not in ('indexed', 'not_indexed', 'unknown'):
                        messages.error(request, 'Kies de categorie van het GSC-rapport.')
                        return redirect('ui:import-preview', project_id=project_id, batch_id=batch.pk)
                    batch.request_metadata['report_status'] = category
                    if not batch.request_metadata.get('reason_from_metadata'):
                        batch.request_metadata['indexing_reason'] = request.POST.get('indexing_reason', '').strip()[:255]
                        from itertools import islice
                        iterator = batch.records.order_by('id').iterator(chunk_size=500)
                        while chunk := list(islice(iterator, 500)):
                            for record in chunk:
                                record.data.update(gsc_indexing_reason=batch.request_metadata['indexing_reason'],
                                                   gsc_report_status=category)
                            SourceRecord.objects.bulk_update(chunk, ['data'], batch_size=500)
                batch.status = 'complete'
                batch.save(update_fields=['status', 'request_metadata', 'measured_at'])
                messages.success(request, f'{batch.record_count} records geïmporteerd.')
                return redirect('ui:batch', project_id=project_id, batch_id=batch.pk)
    context.update(batch=batch, summary=batch.request_metadata,
                   sample=batch.records.order_by('id')[:10], observation_form=observation_form)
    return render(request, 'ui/import_preview.html', context)


class ObservationDateForm(forms.Form):
    observed_on = forms.DateField(required=False, label='Waarnemingsdatum / crawldatum (optioneel)',
        input_formats=['%Y-%m-%d'], widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'),
        help_text='Een datum uit de bestandsnaam is slechts een suggestie. Bevestig de crawl- of peildatum. Laat leeg om de importdatum te gebruiken.')
