import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST
from django.db import transaction
from projects.models import Project
from projects.permissions import can_edit, can_manage
from .models import ImportBatch
from .forms import BatchForm
from .services import delete_batch


def visible_project(request, project_id):
    return get_object_or_404(Project.objects.visible_to(request.user), pk=project_id)


def batch_data(batch):
    return {"id": str(batch.id), "source": batch.source, "method": batch.method,
        "label": batch.label, "original_filename": batch.original_filename,
        "user_id": batch.created_by_id, "username": batch.created_by.get_username(),
        "imported_at": batch.imported_at.isoformat(),
        "measured_at": batch.measured_at.isoformat() if batch.measured_at else None,
        "period_start": batch.period_start.isoformat() if batch.period_start else None,
        "period_end": batch.period_end.isoformat() if batch.period_end else None,
        "status": batch.status, "record_count": batch.record_count, "warnings": batch.warnings}


@login_required
@require_GET
def overview(request, project_id):
    project = visible_project(request, project_id)
    batches = project.import_batches.select_related("created_by")
    return JsonResponse({"project_id": str(project.id), "can_delete": can_manage(request.user, project),
        "batches": [batch_data(b) for b in batches]})


@login_required
@require_POST
def create(request, project_id):
    with transaction.atomic():
        project = get_object_or_404(Project.objects.visible_to(request.user).select_for_update(of=("self",)), pk=project_id)
        if not can_edit(request.user, project):
            return JsonResponse({"error": "Geen bewerkrechten."}, status=403)
        try:
            data = json.loads(request.body)
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, TypeError):
            return JsonResponse({"error": "Ongeldige JSON."}, status=400)
        form = BatchForm(data)
        if not form.is_valid():
            return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
        batch = form.save(commit=False)
        batch.project = project
        batch.created_by = request.user
        batch.save()
        return JsonResponse(batch_data(batch), status=201)


@login_required
@require_POST
def remove(request, project_id, batch_id):
    project = visible_project(request, project_id)
    if not can_manage(request.user, project):
        return JsonResponse({"error": "Geen beheerrechten."}, status=403)
    get_object_or_404(ImportBatch, pk=batch_id, project=project)
    try:
        delete_batch(batch_id=batch_id, project=project, actor=request.user)
    except ImportBatch.DoesNotExist:
        return JsonResponse({"error": "Batch niet gevonden."}, status=404)
    return JsonResponse({"deleted": True})
