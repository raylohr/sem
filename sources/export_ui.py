from django.contrib.auth.decorators import login_required
from django.http import FileResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from projects.models import Project
from .excel_inventory import build_inventory_workbook


@never_cache
@login_required
@require_GET
def export_urls(request, project_id):
    project = get_object_or_404(Project.objects.visible_to(request.user), pk=project_id)
    try:
        output = build_inventory_workbook(project, request.user, absolute_url=request.build_absolute_uri)
    except ValueError as error:
        return HttpResponseBadRequest(str(error), content_type='text/plain; charset=utf-8')
    filename = f'{slugify(project.domain or project.name) or "project"}-url-werkbestand-{timezone.localdate().isoformat()}.xlsx'
    response = FileResponse(output, as_attachment=True, filename=filename,
                            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
