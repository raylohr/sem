from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST
from django.db import transaction
from projects.models import Project
from projects.permissions import can_edit, can_manage
from .models import ImportBatch
from .services import delete_batch


def visible_project(request, project_id):
    return get_object_or_404(Project.objects.visible_to(request.user), pk=project_id)


def batch_data(batch):
    return {"id": str(batch.id), "source": batch.source, "method": batch.method,
        "label": batch.label,
        "user_id": batch.created_by_id, "username": batch.created_by.get_username(),
        "imported_at": batch.imported_at.isoformat(),
        "measured_at": batch.measured_at.isoformat() if batch.measured_at else None,
        "period_start": batch.period_start.isoformat() if batch.period_start else None,
        "period_end": batch.period_end.isoformat() if batch.period_end else None,
        "status": batch.status, "record_count": batch.record_count, "warnings": batch.warnings,
        "error": batch.error, "request": batch.request_metadata}


@login_required
@require_GET
def overview(request, project_id):
    project = visible_project(request, project_id)
    batches = project.import_batches.select_related("created_by")
    return JsonResponse({"project_id": str(project.id), "can_delete": can_manage(request.user, project),
        "batches": [batch_data(b) for b in batches]})


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


@login_required
@require_GET
def records(request, project_id, batch_id):
    project = visible_project(request, project_id)
    batch = get_object_or_404(ImportBatch, pk=batch_id, project=project)
    if batch.status not in (ImportBatch.Status.COMPLETE, ImportBatch.Status.INCOMPLETE):
        return JsonResponse({"error": "Batch is nog niet voltooid."}, status=409)
    try:
        offset = int(request.GET.get("offset", 0))
        if offset < 0:
            raise ValueError
    except ValueError:
        return JsonResponse({"error": "Ongeldige offset."}, status=400)
    rows = list(batch.records.order_by("id").values("locator", "data")[offset:offset+100])
    return JsonResponse({"records": rows, "total": batch.record_count, "offset": offset})


@login_required
@require_POST
def retry(request, project_id, batch_id):
    with transaction.atomic():
        project = get_object_or_404(Project.objects.visible_to(request.user).select_for_update(of=("self",)), pk=project_id)
        if not can_edit(request.user, project):
            return JsonResponse({"error": "Geen bewerkrechten."}, status=403)
        batch = get_object_or_404(ImportBatch.objects.select_for_update(), pk=batch_id, project=project)
        if not batch.gsc_connection_id or batch.status not in ("failed", "pending"):
            return JsonResponse({"error": "Batch kan niet opnieuw worden gestart."}, status=409)
        # Keep the original importing user; retries do not overwrite provenance.
        batch.status = "pending"
        batch.error = ""
        batch.save(update_fields=["status", "error"])
        from integrations.tasks import enqueue_gsc
        transaction.on_commit(lambda: enqueue_gsc(batch.id))
    return JsonResponse({"queued": True}, status=202)
