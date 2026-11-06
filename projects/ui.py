from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods
from core.ui import project_context
from .forms import ProjectForm, ProjectDeleteForm
from .services import create_project, update_project, delete_project


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def create(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    form = ProjectForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            project = create_project(actor=request.user, **form.cleaned_data)
            messages.success(request, 'Project aangemaakt. Je bent als projectbeheerder toegevoegd.')
            return redirect('ui:project', project_id=project.pk)
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
    return render(request, 'projects/form.html', {'form': form, 'creating': True})


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def edit(request, project_id):
    context = project_context(request, project_id, 'settings')
    if not context['can_manage']:
        raise PermissionDenied
    project = context['project']
    form = ProjectForm(request.POST if request.method == 'POST' else None, instance=project)
    if request.method == 'POST' and form.is_valid():
        try:
            update_project(actor=request.user, project_id=project.pk, **form.cleaned_data)
            messages.success(request, 'Projectinstellingen opgeslagen.')
            return redirect('ui:project', project_id=project.pk)
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
    # ModelForm validation mutates its instance; keep the persisted name in navigation.
    project.refresh_from_db()
    return render(request, 'projects/form.html', {**context, 'form': form, 'creating': False})


@never_cache
@login_required
@sensitive_post_parameters('password')
@require_http_methods(['GET', 'POST'])
def remove(request, project_id):
    context = project_context(request, project_id, 'settings')
    if not request.user.is_superuser:
        raise PermissionDenied
    project = context['project']
    form = ProjectDeleteForm(request.POST if request.method == 'POST' else None,
                             project=project, user=request.user, request=request)
    if request.method == 'POST' and form.is_valid():
        try:
            delete_project(actor=request.user, project_id=project.pk,
                           confirmation_name=form.cleaned_data['confirmation_name'])
            messages.success(request, 'Project en bijbehorende gegevens verwijderd. Bronbestanden worden opgeruimd.')
            return redirect('ui:dashboard')
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
    context.update(form=form, deletion_counts={
        'batches': project.import_batches.count(),
        'connections': project.gsc_connections.count(),
        'members': project.memberships.count(),
    })
    return render(request, 'projects/delete.html', context)
