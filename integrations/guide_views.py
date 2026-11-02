from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from core.ui import project_context
from projects.models import Project
from .gsc import configured


@never_cache
@login_required
@require_GET
def work_instructions(request, project_id=None):
    context = project_context(request, project_id, "connections") if project_id else {"active": "help"}
    context.update(redirect_uri=settings.GSC_REDIRECT_URI, ready=configured(),
        guide_projects=Project.objects.visible_to(request.user).order_by("name"))
    # Project number is safe to put in Google Console links; never include the secret.
    suffix = ""
    if request.user.is_superuser:
        from .models import GSCOAuthSettings
        client_id = GSCOAuthSettings.objects.filter(pk=1).values_list("client_id", flat=True).first() or settings.GSC_CLIENT_ID
        number = client_id.split("-", 1)[0]
        if number.isdigit():
            suffix = "?project=" + number
    context["google_links"] = {key: "https://console.cloud.google.com/" + path + suffix for key, path in {
        "api": "apis/library/searchconsole.googleapis.com", "branding": "auth/branding",
        "audience": "auth/audience", "scopes": "auth/scopes", "clients": "auth/clients",
    }.items()}
    return render(request, "integrations/work_instructions.html", context)
