from django.http import JsonResponse
from django.db import connection
from django.views.decorators.http import require_GET


@require_GET
def health(request):
    return JsonResponse({"status": "ok", "service": "sem"})


@require_GET
def ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        from config.celery import app
        with app.connection_for_write() as broker:
            broker.ensure_connection(max_retries=0)
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})
