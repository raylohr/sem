from django.http import JsonResponse
from django.db import connection
from django.views.decorators.http import require_GET
from django.shortcuts import render
from django.views.decorators.cache import never_cache


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


# CSRF failures bypass Django's normal 403 handler. Keep the same branded page
# while avoiding disclosure of internal validation reasons or submitted values.
@never_cache
def csrf_failure(request, reason=""):
    return render(request, "403.html", {"csrf_error": True}, status=403)
