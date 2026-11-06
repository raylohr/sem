from urllib.parse import urlsplit

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
from .host_filter import filter_file_rows, filter_staged_batch
from collections import Counter


class UploadForm(forms.Form):
    file = forms.FileField(label='Bestand', widget=forms.ClearableFileInput(attrs={
        'class': 'form-control', 'accept': '.csv,.zip,text/csv,application/zip'}))


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
                batch = project.import_batches.filter(file_checksum=parsed['checksum']).first()
                if batch is None:
                    summary = parsed['summary']
                    summary['input_rows'] = len(parsed['rows'])
                    rows, excluded = filter_file_rows(project, parsed['rows'])
                    if not rows:
                        form.add_error('file', 'Geen URLs binnen de toegestane hosts van dit project. Kies het juiste project of pas de hostregels aan.')
                        context['form'] = form
                        return render(request, 'ui/import_upload.html', context)
                    summary.update(excluded_rows=excluded, host_rules=project.host_rules,
                                   hosts=dict(Counter(urlsplit(row.get('Address') or row.get('URL')).hostname for row in rows)))
                    if parsed['source'] == 'sf':
                        summary['html_count'] = sum(row['Content Type'].lower().startswith(('text/html', 'application/xhtml+xml')) for row in rows)
                    warnings = ['URLs buiten de toegestane projecthosts worden overgeslagen; ontbrekende URLs worden niet verwijderd.']
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
        if request.method == 'POST':
            if request.POST.get('action') == 'cancel':
                batch.delete()
                return redirect('ui:sources', project_id=project_id)
            if request.POST.get('action') == 'confirm' and batch.record_count:
                if batch.source == 'gsc':
                    category = request.POST.get('report_status', '')
                    if category not in ('indexed', 'not_indexed', 'unknown'):
                        messages.error(request, 'Kies de categorie van het GSC-rapport.')
                        return redirect('ui:import-preview', project_id=project_id, batch_id=batch.pk)
                    batch.request_metadata['report_status'] = category
                    batch.request_metadata['indexing_reason'] = request.POST.get('indexing_reason', '').strip()[:255]
                batch.status = 'complete'
                batch.save(update_fields=['status', 'request_metadata'])
                messages.success(request, f'{batch.record_count} URLs geïmporteerd.')
                return redirect('ui:batch', project_id=project_id, batch_id=batch.pk)
    context.update(batch=batch, summary=batch.request_metadata,
                   sample=batch.records.order_by('id')[:10])
    return render(request, 'ui/import_preview.html', context)
