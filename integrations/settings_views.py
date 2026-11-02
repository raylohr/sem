from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.shortcuts import render, redirect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods
from accounts.models import AuditEvent
from accounts.views import platform_required
from .forms import GSCOAuthSettingsForm
from .models import GSCOAuthSettings, GSCConnection
from .gsc import configured, encrypt_token


def settings_context():
    stored = GSCOAuthSettings.objects.filter(pk=1).first()
    return {"configuration": stored, "ready": configured(),
        "encryption_ready": bool(settings.INTEGRATION_TOKEN_KEY),
        "redirect_uri": settings.GSC_REDIRECT_URI,
        "client_id": stored.client_id if stored else settings.GSC_CLIENT_ID,
        "secret_present": bool(stored.encrypted_client_secret) if stored else bool(settings.GSC_CLIENT_SECRET),
        "connection_count": GSCConnection.objects.exclude(encrypted_refresh_token="").count(),
        "active": "integrations"}


@platform_required
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters("client_secret")
def oauth_settings(request):
    context = settings_context()
    form = GSCOAuthSettingsForm(initial={"client_id": context["client_id"],
        "enabled": context["configuration"].enabled if context["configuration"] else True},
        current_id=context["client_id"], has_secret=context["secret_present"],
        has_connections=bool(context["connection_count"]))
    if request.method == "POST":
        with transaction.atomic():
            # Serialize platform settings updates, including the first save.
            from accounts.models import User
            list(User.objects.filter(is_superuser=True).order_by("pk").select_for_update())
            context = settings_context()
            form = GSCOAuthSettingsForm(request.POST, current_id=context["client_id"],
                has_secret=context["secret_present"], has_connections=bool(context["connection_count"]))
            if not request.user.is_active or not User.objects.filter(pk=request.user.pk, is_superuser=True, is_active=True).exists():
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied
            if not context["encryption_ready"]:
                form.is_valid()
                form.add_error(None, "De server mist de versleutelingssleutel. Neem contact op met de serverbeheerder.")
            elif form.is_valid():
                data = form.cleaned_data
                stored = context["configuration"]
                secret = data["client_secret"]
                encrypted = encrypt_token(secret) if secret else (
                    stored.encrypted_client_secret if stored else encrypt_token(settings.GSC_CLIENT_SECRET))
                changed = bool(context["client_id"] and context["client_id"] != data["client_id"])
                disconnected = 0
                if changed:
                    disconnected = GSCConnection.objects.exclude(encrypted_refresh_token="").update(
                        active=False, daily_enabled=False, available_properties=[], encrypted_refresh_token="")
                GSCOAuthSettings.objects.update_or_create(pk=1, defaults={"client_id": data["client_id"],
                    "encrypted_client_secret": encrypted, "enabled": data["enabled"], "updated_by": request.user})
                AuditEvent.objects.create(actor=request.user, action="gsc_oauth_updated",
                    details={"enabled": data["enabled"], "client_changed": changed, "disconnected": disconnected})
                messages.success(request, "Google OAuth-instellingen opgeslagen. Koppel nu een project om de Google-toestemming te testen.")
                return redirect("ui:gsc-settings")
    context["form"] = form
    return render(request, "integrations/oauth_settings.html", context)


@platform_required
@require_http_methods(["GET"])
def setup_guide(request):
    from .guide_views import work_instructions
    return work_instructions(request)
