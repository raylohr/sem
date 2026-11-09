import json
from datetime import timedelta

from django import forms
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from projects.models import Project
from projects.permissions import can_edit, can_manage
from sources.models import ImportBatch
from sources.views import visible_project, batch_data
from .models import SERankingConnection
from .seranking import ProjectClient, REPORTS, SERankingError, configured, matching_projects, verify_connection
from .seranking_tasks import enqueue_seranking


class ImportForm(forms.Form):
    report = forms.ChoiceField(choices=[(key, value[0]) for key, value in REPORTS.items()])
    start = forms.DateField(required=False)
    end = forms.DateField(required=False)
    audit_id = forms.IntegerField(min_value=1, required=False)

    def clean(self):
        values = super().clean()
        if values.get('report') == 'rankings':
            start, end = values.get('start'), values.get('end')
            if not start or not end or end < start or end - start > timedelta(days=30) or end > timezone.localdate():
                raise forms.ValidationError('Kies een geldige periode van maximaal 31 dagen, tot en met vandaag.')
        if values.get('report') == 'audit_pages' and not values.get('audit_id'):
            self.add_error('audit_id', 'Kies een bestaande voltooide audit.')
        return values


def form_data(request):
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body)
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (ValueError, TypeError):
            return {}
    return request.POST


@never_cache
@login_required
@require_POST
def discover(request, project_id):
    project = visible_project(request, project_id)
    if not can_manage(request.user, project):
        return JsonResponse({'error': 'Geen beheerrechten.'}, status=403)
    try:
        sites = matching_projects(ProjectClient(), project)
    except SERankingError as exc:
        return JsonResponse({'error': str(exc)}, status=502)
    from core.ui import project_context
    return render(request, 'integrations/seranking_projects.html', {**project_context(request, project.pk, 'connections'), 'sites': sites})


@login_required
@require_POST
def connect(request, project_id):
    project = visible_project(request, project_id)
    if not can_manage(request.user, project):
        return JsonResponse({'error': 'Geen beheerrechten.'}, status=403)
    try:
        site_id = int(form_data(request).get('site_id', ''))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Kies een geldig SE Ranking-project.'}, status=400)
    try:
        site = next((site for site in matching_projects(ProjectClient(), project) if site['id'] == site_id), None)
    except SERankingError as exc:
        return JsonResponse({'error': str(exc)}, status=502)
    if not site:
        return JsonResponse({'error': 'SE Ranking-project valt buiten de toegestane projecthosts.'}, status=400)
    with transaction.atomic():
        project = get_object_or_404(Project.objects.visible_to(request.user).select_for_update(), pk=project_id)
        if not can_manage(request.user, project):
            return JsonResponse({'error': 'Geen beheerrechten.'}, status=403)
        from projects.hosts import allows_url
        if not allows_url(project, site['name'] if '://' in site['name'] else 'https://' + site['name']):
            return JsonResponse({'error': 'Projecthosts zijn gewijzigd. Zoek opnieuw.'}, status=409)
        SERankingConnection.objects.update_or_create(project=project, site_id=site_id,
            defaults={'owner': request.user, 'site_name': site['name'], 'active': True})
    return redirect('ui:connections', project_id=project.pk)


@login_required
@require_POST
def disconnect(request, project_id, connection_id):
    with transaction.atomic():
        project = get_object_or_404(Project.objects.visible_to(request.user).select_for_update(), pk=project_id)
        if not can_manage(request.user, project):
            return JsonResponse({'error': 'Geen beheerrechten.'}, status=403)
        connection = get_object_or_404(SERankingConnection.objects.select_for_update(), pk=connection_id, project=project)
        connection.active = False
        connection.save(update_fields=['active'])
    return redirect('ui:connections', project_id=project.pk)


@never_cache
@login_required
@require_POST
def audits(request, project_id, connection_id):
    project = visible_project(request, project_id)
    if not can_edit(request.user, project):
        return JsonResponse({'error': 'Geen bewerkrechten.'}, status=403)
    connection = get_object_or_404(SERankingConnection, pk=connection_id, project=project, active=True)
    if not can_manage(connection.owner, project):
        return JsonResponse({'error': 'Eigenaar heeft geen beheerrechten meer.'}, status=403)
    try:
        client = ProjectClient()
        verify_connection(client, connection)
        rows = client.pages('audits', search=connection.site_name)
    except SERankingError as exc:
        return JsonResponse({'error': str(exc)}, status=502)
    rows = [{'id': row.get('id'), 'title': row.get('title'), 'date': row.get('last_update')} for row in rows
            if str(row.get('site_id')) == str(connection.site_id) and row.get('status') == 'finished']
    from core.ui import project_context
    return render(request, 'integrations/seranking_audits.html', {**project_context(request, project.pk, 'connections'), 'connection': connection, 'audits': rows})


@login_required
@require_POST
def import_data(request, project_id, connection_id):
    with transaction.atomic():
        project = get_object_or_404(Project.objects.visible_to(request.user).select_for_update(), pk=project_id)
        if not can_edit(request.user, project):
            return JsonResponse({'error': 'Geen bewerkrechten.'}, status=403)
        connection = get_object_or_404(SERankingConnection.objects.select_related('owner'), pk=connection_id, project=project, active=True)
        if not can_manage(connection.owner, project):
            return JsonResponse({'error': 'Eigenaar heeft geen beheerrechten meer.'}, status=403)
        if not configured():
            return JsonResponse({'error': 'SERANKING_API_KEY is nog niet ingesteld.'}, status=503)
        form = ImportForm(form_data(request))
        if not form.is_valid():
            return JsonResponse({'errors': form.errors.get_json_data()}, status=400)
        values = form.cleaned_data
        report = values['report']
        label, scope = REPORTS[report]
        batch = ImportBatch.objects.create(project=project, source='seranking', method='api',
            label='SE Ranking · ' + label, created_by=request.user, seranking_connection=connection,
            period_start=values.get('start') if report == 'rankings' else None,
            period_end=values.get('end') if report == 'rankings' else None,
            parser_version='seranking-project-v1', request_metadata={'type': report, 'record_scope': scope,
                'site_id': connection.site_id, 'site_name': connection.site_name, 'report_label': label,
                'audit_id': values.get('audit_id') if report == 'audit_pages' else None, 'api_scope': 'project-only'})
        transaction.on_commit(lambda: enqueue_seranking(batch.pk))
    return JsonResponse(batch_data(batch), status=202)
