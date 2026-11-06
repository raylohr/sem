from uuid import UUID
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import FileResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from projects.models import Project
from .excel_inventory import build_inventory_workbook


def progress_key(request, project):
    value = request.GET.get('export_token')
    if value is None:
        return None
    try:
        token = UUID(value)
    except (ValueError, AttributeError):
        raise ValueError('Ongeldig exportkenmerk.') from None
    return f'url-export:{request.user.pk}:{project.pk}:{token}'


def save_progress(key, *, percent=0, message='', state='building'):
    if key:
        try:
            cache.set(key, {'state': state, 'percent': percent, 'message': message}, timeout=3600)
        except Exception:
            # Feedback failure must never break an otherwise valid export.
            pass


@never_cache
@login_required
@require_GET
def export_progress(request, project_id):
    project = get_object_or_404(Project.objects.visible_to(request.user), pk=project_id)
    try:
        key = progress_key(request, project)
        if key is None:
            raise ValueError('Exportkenmerk ontbreekt.')
    except ValueError as error:
        return JsonResponse({'error': str(error)}, status=400)
    try:
        result = cache.get(key)
    except Exception:
        return JsonResponse({'state': 'unavailable'}, status=503)
    return JsonResponse(result or {'state': 'waiting', 'percent': 0, 'message': 'Export wordt gestart…'})


@never_cache
@login_required
@require_GET
def export_urls(request, project_id):
    project = get_object_or_404(Project.objects.visible_to(request.user), pk=project_id)
    try:
        key = progress_key(request, project)
    except ValueError as error:
        return HttpResponseBadRequest(str(error), content_type='text/plain; charset=utf-8')
    save_progress(key, message='Export wordt gestart…')
    try:
        options = {'absolute_url': request.build_absolute_uri}
        if key:
            options['progress'] = lambda **data: save_progress(key, **data)
        output = build_inventory_workbook(project, request.user, **options)
    except ValueError as error:
        save_progress(key, message=str(error), state='failed')
        return HttpResponseBadRequest(str(error), content_type='text/plain; charset=utf-8')
    except Exception:
        save_progress(key, message='De export is mislukt. Probeer opnieuw.', state='failed')
        raise
    filename = f'{slugify(project.domain or project.name) or "project"}-url-werkbestand-{timezone.localdate().isoformat()}.xlsx'
    response = FileResponse(output, as_attachment=True, filename=filename,
                            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
