import json
import time
from urllib.parse import urlparse
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from google.auth.transport.requests import AuthorizedSession
from projects.models import Project
from projects.permissions import can_edit, can_manage
from sources.models import ImportBatch
from sources.views import visible_project, batch_data
from .models import GSCConnection
from .forms import GSCImportForm
from .gsc import configured, oauth_flow, encrypt_token, list_properties, configuration_fingerprint, ConnectorError
from .tasks import enqueue_gsc


def connection_data(connection):
    return {"id": str(connection.id), "property": connection.property_url,
        "owner_id": connection.owner_id, "active": connection.active,
        "created_at": connection.created_at.isoformat()}


@login_required
@require_GET
def connections(request, project_id):
    project = visible_project(request, project_id)
    return JsonResponse({"configured": configured(), "connections": [connection_data(c) for c in project.gsc_connections.all()]})


@login_required
@require_POST
def connect(request, project_id):
    project = visible_project(request, project_id)
    if not can_manage(request.user, project):
        return JsonResponse({"error": "Geen beheerrechten."}, status=403)
    if not configured():
        return JsonResponse({"error": "Google OAuth is nog niet geconfigureerd."}, status=503)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            raise ValueError
        property_url = data.get("property", "")
        if not isinstance(property_url, str) or len(property_url) > 2048:
            raise ValueError
        parsed = urlparse(property_url)
        if property_url and not (property_url.startswith("sc-domain:") and len(property_url) > 10) and not (parsed.scheme in ("http", "https") and parsed.hostname):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        return JsonResponse({"error": "Geef een geldige GSC-property op."}, status=400)
    flow = oauth_flow()
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    request.session["gsc_oauth"] = {"state": state, "verifier": flow.code_verifier,
        "user_id": request.user.pk, "project_id": str(project.pk), "property": property_url,
        "started": time.time(), "configuration": configuration_fingerprint()}
    response = JsonResponse({"authorization_url": url})
    response["Cache-Control"] = "no-store"
    return response


@never_cache
@require_GET
def callback(request):
    # Don't redirect unauthenticated callbacks to login with the OAuth code in next=.
    pending = request.session.get("gsc_oauth")
    if not request.user.is_authenticated or not request.user.is_active or not pending:
        return JsonResponse({"error": "OAuth-sessie ontbreekt; start opnieuw."}, status=400)
    if pending["user_id"] != request.user.pk or not constant_time_compare(request.GET.get("state", ""), pending["state"]):
        return JsonResponse({"error": "Ongeldige OAuth-state."}, status=400)
    del request.session["gsc_oauth"]
    if time.time() - pending["started"] > 600 or request.GET.get("error") or not request.GET.get("code"):
        return JsonResponse({"error": "Google-koppeling verlopen of geweigerd; start opnieuw."}, status=400)
    project = get_object_or_404(Project.objects.visible_to(request.user), pk=pending["project_id"])
    if not can_manage(request.user, project):
        return JsonResponse({"error": "Geen beheerrechten."}, status=403)
    stage = "configuration"
    try:
        if pending.get("configuration") and pending["configuration"] != configuration_fingerprint():
            raise ValueError("OAuth configuration changed")
        flow = oauth_flow(state=pending["state"], verifier=pending["verifier"])
        stage = "token_exchange"
        flow.fetch_token(code=request.GET["code"], timeout=30)
        credentials = flow.credentials
        if not credentials.refresh_token:
            raise ConnectorError("Google heeft geen blijvende toestemming afgegeven. Start opnieuw met Verbinden met Google en geef toestemming voor Search Console.")
        granted = credentials.granted_scopes or credentials.scopes or []
        from .gsc import SCOPE
        if SCOPE not in granted:
            raise ConnectorError("Leestoegang tot Search Console ontbreekt. Start opnieuw en selecteer de Search Console-toestemming op het Google-scherm.")
        stage = "properties"
        with AuthorizedSession(credentials, refresh_timeout=30) as session:
            properties = list_properties(session)
            if not properties:
                raise ConnectorError("Dit Google-account heeft geen toegankelijke Search Console-properties. Controleer het account op search.google.com/search-console en laat de website-eigenaar toegang geven.")
            if pending["property"] and pending["property"] not in properties:
                raise ConnectorError("De gekozen property is niet toegankelijk voor dit Google-account. Controleer de propertyrechten in Search Console.")
        stage = "storage"
        token = encrypt_token(credentials.refresh_token)
    except Exception as exc:
        errors = {
            "invalid_client": "Google weigert de OAuth-client. Controleer of client-ID en client secret van dezelfde Web application zijn en sla het juiste secret opnieuw op.",
            "invalid_grant": "De Google-toestemming is verlopen of al gebruikt. Start opnieuw via Verbinden met Google; vernieuw de callbackpagina niet.",
            "redirect_uri_mismatch": "De redirect-URI komt niet overeen. Neem de exacte redirect-URI van de Google-instellingen over in je Google OAuth-client.",
            "access_denied": "Google-toestemming geweigerd. Controleer testgebruikers en geef toestemming voor Search Console.",
        }
        defaults = {
            "configuration": "De Google-instellingen zijn tijdens het koppelen gewijzigd. Start opnieuw via Verbinden met Google.",
            "token_exchange": "Het ophalen van Google-toestemming is mislukt. Controleer de OAuth-clientgegevens en probeer opnieuw.",
            "properties": "Search Console-properties konden niet worden opgehaald. Controleer of de Google Search Console API actief is in hetzelfde Google Cloud-project als de OAuth-client.",
            "storage": "De Google-koppeling kon niet veilig worden opgeslagen. Neem contact op met de platformbeheerder.",
        }
        code = getattr(exc, "error", None)
        known_code = code if isinstance(code, str) and code in errors else "unknown"
        message = str(exc) if isinstance(exc, ConnectorError) else errors.get(known_code, defaults[stage])
        # Only fixed stage/code labels: exception text and token payloads may contain secrets.
        import logging
        logging.getLogger(__name__).warning("gsc_oauth_failed stage=%s code=%s", stage, known_code)
        response = render(request, "integrations/oauth_error.html", {"project": project, "error": message}, status=400)
        response["Referrer-Policy"] = "no-referrer"
        return response
    with transaction.atomic():
        project = get_object_or_404(Project.objects.visible_to(request.user).select_for_update(of=("self",)), pk=project.pk)
        if not can_manage(request.user, project):
            return JsonResponse({"error": "Geen beheerrechten."}, status=403)
        GSCConnection.objects.create(project=project, owner=request.user, property_url=pending["property"], encrypted_refresh_token=token,
            active=bool(pending["property"]), available_properties=properties)
    response = redirect("ui:connections", project_id=project.pk)
    response["Referrer-Policy"] = "no-referrer"
    return response


@login_required
@require_POST
def disconnect(request, project_id, connection_id):
    with transaction.atomic():
        project = get_object_or_404(Project.objects.visible_to(request.user).select_for_update(of=("self",)), pk=project_id)
        if not can_manage(request.user, project):
            return JsonResponse({"error": "Geen beheerrechten."}, status=403)
        connection = get_object_or_404(GSCConnection.objects.select_for_update(), pk=connection_id, project=project)
        connection.active = False
        connection.daily_enabled = False
        connection.available_properties = []
        connection.encrypted_refresh_token = ""
        connection.save(update_fields=["active", "encrypted_refresh_token", "daily_enabled", "available_properties"])
    return JsonResponse({"disconnected": True})


@login_required
@require_POST
def import_data(request, project_id, connection_id):
    with transaction.atomic():
        project = get_object_or_404(Project.objects.visible_to(request.user).select_for_update(of=("self",)), pk=project_id)
        if not can_edit(request.user, project):
            return JsonResponse({"error": "Geen bewerkrechten."}, status=403)
        connection = get_object_or_404(GSCConnection.objects.select_related("owner"), pk=connection_id, project=project, active=True)
        if not can_manage(connection.owner, project):
            return JsonResponse({"error": "Eigenaar van bronverbinding heeft geen beheerrechten meer."}, status=403)
        if not configured():
            return JsonResponse({"error": "Google OAuth is nog niet geconfigureerd."}, status=503)
        try:
            data = json.loads(request.body)
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, TypeError):
            return JsonResponse({"error": "Ongeldige JSON."}, status=400)
        form = GSCImportForm(data)
        if not form.is_valid():
            return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
        start, end = form.cleaned_data["start"], form.cleaned_data["end"]
        batch = ImportBatch.objects.create(project=project, source="gsc", method="api",
            label=f"GSC zoekprestaties {start} – {end}", created_by=request.user,
            period_start=start, period_end=end, gsc_connection=connection, parser_version="gsc-api-v1",
            request_metadata={"property": connection.property_url, "dimensions": ["date", "page"],
                "type": "web", "dataState": "final", "timezone": "America/Los_Angeles"})
        transaction.on_commit(lambda: enqueue_gsc(batch.id))
    return JsonResponse(batch_data(batch), status=202)


@login_required
@require_POST
def select_property(request, project_id, connection_id):
    with transaction.atomic():
        project = get_object_or_404(Project.objects.visible_to(request.user).select_for_update(), pk=project_id)
        if not can_manage(request.user, project):
            return JsonResponse({"error": "Geen beheerrechten."}, status=403)
        connection = get_object_or_404(GSCConnection.objects.select_for_update(), pk=connection_id,
            project=project, owner=request.user, active=False, property_url="")
        selected = request.POST.get("property", "")
        if not connection.encrypted_refresh_token or selected not in connection.available_properties:
            return JsonResponse({"error": "Kies een beschikbare property of koppel opnieuw."}, status=400)
        connection.property_url = selected
        connection.active = True
        connection.daily_enabled = True
        connection.available_properties = []
        connection.save()
    return redirect("ui:connections", project_id=project.pk)


@login_required
@require_POST
def schedule(request, project_id, connection_id):
    project = visible_project(request, project_id)
    if not can_manage(request.user, project):
        return JsonResponse({"error": "Geen beheerrechten."}, status=403)
    connection = get_object_or_404(GSCConnection, pk=connection_id, project=project, active=True)
    connection.daily_enabled = request.POST.get("enabled") == "1"
    connection.save(update_fields=["daily_enabled"])
    return redirect("ui:connections", project_id=project.pk)


@login_required
@require_POST
def inspect_urls(request, project_id, connection_id):
    from .gsc import property_contains
    project = visible_project(request, project_id)
    if not can_edit(request.user, project):
        return JsonResponse({"error": "Geen bewerkrechten."}, status=403)
    connection = get_object_or_404(GSCConnection, pk=connection_id, project=project, active=True)
    if not can_manage(connection.owner, project):
        return JsonResponse({"error": "Eigenaar heeft geen beheerrechten meer."}, status=403)
    if not configured():
        return JsonResponse({"error": "Google OAuth is nog niet geconfigureerd."}, status=503)
    urls = list(dict.fromkeys(request.POST.get("urls", "").split()))
    if not 1 <= len(urls) <= 20 or any(not property_contains(connection.property_url, u) for u in urls):
        return JsonResponse({"error": "Kies 1 tot 20 URL’s binnen de gekoppelde property."}, status=400)
    batch = ImportBatch.objects.create(project=project, source="gsc", method="api",
        label="GSC URL-inspecties", created_by=request.user, gsc_connection=connection,
        parser_version="gsc-inspection-v1", request_metadata={"property": connection.property_url,
        "kind": "inspection", "urls": urls})
    transaction.on_commit(lambda: enqueue_gsc(batch.pk))
    return redirect("ui:batch", project_id=project.pk, batch_id=batch.pk)
