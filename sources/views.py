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
    return {"id": str(batch.id), "source": batch.source, "method": batch.method, "scan_kind": batch.scan_kind,
        "label": batch.label, "original_filename": batch.original_filename,
        "user_id": batch.created_by_id, "username": batch.created_by.get_username(),
        "imported_at": batch.imported_at.isoformat(),
        "measured_at": batch.measured_at.isoformat() if batch.measured_at else None,
        "period_start": batch.period_start.isoformat() if batch.period_start else None,
        "period_end": batch.period_end.isoformat() if batch.period_end else None,
        "status": batch.status, "record_count": batch.record_count, "warnings": batch.warnings,
        "sheets": batch.sheet_summary, "error": batch.error, "has_file": bool(batch.file), "request": batch.request_metadata, "url_index": batch.url_index_info}


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


@login_required
@require_POST
def upload(request, project_id):
    import hashlib
    import uuid
    from pathlib import Path
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage
    from .parsers import parse_file, ImportFormatError, PARSER_VERSION, MAX_BYTES
    from .tasks import enqueue_upload

    project = visible_project(request, project_id)
    if not can_edit(request.user, project):
        return JsonResponse({"error": "Geen bewerkrechten."}, status=403)
    incoming = request.FILES.get("file")
    if incoming is None:
        return JsonResponse({"error": "Kies een bestand in veld file."}, status=400)
    if incoming.size > MAX_BYTES:
        return JsonResponse({"error": "Maximale bestandsgrootte is 20 MiB."}, status=400)
    content = incoming.read(MAX_BYTES + 1)
    try:
        parsed = parse_file(content, incoming.name)
    except ImportFormatError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    requested_source = request.POST.get("source")
    if requested_source and requested_source != parsed["source"]:
        return JsonResponse({"error": "Gekozen bron komt niet overeen met herkend rapporttype."}, status=400)
    form = BatchForm({**request.POST.dict(), "source": parsed["source"], "method": "upload",
                      "label": request.POST.get("label") or incoming.name,
                      "original_filename": incoming.name})
    if not form.is_valid():
        return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
    preview = {"source": parsed["source"], "sheets": parsed["sheets"],
               "record_count": len(parsed["records"]), "warnings": parsed["warnings"],
               "sample": parsed["records"][:5], "problem": parsed["problem"]}
    if request.POST.get("preview") == "1":
        return JsonResponse(preview)
    stored_path = None
    try:
        with transaction.atomic():
            project = get_object_or_404(Project.objects.visible_to(request.user).select_for_update(of=("self",)), pk=project_id)
            if not can_edit(request.user, project):
                return JsonResponse({"error": "Geen bewerkrechten."}, status=403)
            duplicate = project.import_batches.filter(source=parsed["source"], fingerprint=parsed["fingerprint"]).first()
            if duplicate and request.POST.get("allow_duplicate") != "1":
                return JsonResponse({"error": "Deze inhoud is al geïmporteerd.", "duplicate_batch_id": str(duplicate.id)}, status=409)
            batch = form.save(commit=False)
            batch.project = project
            batch.created_by = request.user
            batch.checksum = hashlib.sha256(content).hexdigest()
            batch.fingerprint = parsed["fingerprint"]
            batch.parser_version = PARSER_VERSION
            batch.sheet_summary = parsed["sheets"]
            batch.warnings = parsed["warnings"]
            batch.file.save(f"{uuid.uuid4().hex}{Path(incoming.name).suffix.lower()}", ContentFile(content), save=False)
            stored_path = batch.file.name
            batch.save()
            transaction.on_commit(lambda: enqueue_upload(batch.id))
    except Exception:
        if stored_path:
            default_storage.delete(stored_path)
        raise
    return JsonResponse({**batch_data(batch), "preview": preview}, status=202)


@login_required
@require_GET
def download(request, project_id, batch_id):
    from django.http import FileResponse
    project = visible_project(request, project_id)
    batch = get_object_or_404(ImportBatch, pk=batch_id, project=project)
    if not batch.file:
        return JsonResponse({"error": "Geen bronbestand."}, status=404)
    try:
        response = FileResponse(batch.file.open("rb"), as_attachment=True, filename=batch.original_filename, content_type="application/octet-stream")
    except FileNotFoundError:
        return JsonResponse({"error": "Bronbestand niet beschikbaar."}, status=404)
    response["Cache-Control"] = "private, no-store"
    return response


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
    from .tasks import enqueue_upload
    with transaction.atomic():
        project = get_object_or_404(Project.objects.visible_to(request.user).select_for_update(of=("self",)), pk=project_id)
        if not can_edit(request.user, project):
            return JsonResponse({"error": "Geen bewerkrechten."}, status=403)
        batch = get_object_or_404(ImportBatch.objects.select_for_update(), pk=batch_id, project=project)
        from scans.work import stale_scan
        if (not batch.file and not batch.gsc_connection_id and not batch.scan_target_id) or (batch.status not in ("failed", "pending") and not stale_scan(batch)):
            return JsonResponse({"error": "Batch kan niet opnieuw worden gestart."}, status=409)
        # Keep the original importing user; retries do not overwrite provenance.
        batch.status = "pending"
        batch.error = ""
        batch.save(update_fields=["status", "error"])
        if batch.scan_target_id:
            from scans.tasks import enqueue_scan
            transaction.on_commit(lambda: enqueue_scan(batch.id))
        elif batch.gsc_connection_id:
            from integrations.tasks import enqueue_gsc
            transaction.on_commit(lambda: enqueue_gsc(batch.id))
        else:
            transaction.on_commit(lambda: enqueue_upload(batch.id))
    return JsonResponse({"queued": True}, status=202)
