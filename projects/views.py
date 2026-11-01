import json
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST
from .models import Project, Membership
from .permissions import role_for, can_manage


def project_data(project, user):
    return {"id": str(project.pk), "name": project.name, "website": project.website,
            "role": role_for(user, project)}


@login_required
@require_GET
def project_list(request):
    return JsonResponse({"projects": [project_data(p, request.user)
        for p in Project.objects.visible_to(request.user)]})


@login_required
@require_GET
def project_detail(request, project_id):
    project = get_object_or_404(Project.objects.visible_to(request.user), pk=project_id)
    return JsonResponse(project_data(project, request.user))


@login_required
@require_POST
def membership_set(request, project_id):
    # Lock the project so future concurrent membership operations can share this guard.
    with transaction.atomic():
        project = get_object_or_404(Project.objects.visible_to(request.user).select_for_update(of=("self",)), pk=project_id)
        if not can_manage(request.user, project):
            return JsonResponse({"error": "Geen beheerrechten."}, status=403)
        try:
            body = json.loads(request.body)
            user_id = body["user_id"]
            role = body["role"]
            if not isinstance(user_id, int) or isinstance(user_id, bool):
                raise ValueError
            if role not in Membership.Role.values:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            return JsonResponse({"error": "Geef user_id en een geldige role op."}, status=400)
        target = get_user_model().objects.filter(pk=user_id, is_active=True).first()
        if target is None:
            return JsonResponse({"error": "Actieve gebruiker niet gevonden."}, status=400)
        old = project.memberships.filter(user=target).first()
        if old and old.role == Membership.Role.ADMIN and role != Membership.Role.ADMIN:
            others = project.memberships.filter(role=Membership.Role.ADMIN, user__is_active=True).exclude(user=target)
            if not others.exists():
                return JsonResponse({"error": "Behoud minimaal één actieve projectbeheerder."}, status=409)
        member, _ = Membership.objects.update_or_create(project=project, user=target, defaults={"role": role})
        return JsonResponse({"user_id": member.user_id, "role": member.role})


@login_required
@require_POST
def membership_remove(request, project_id, user_id):
    with transaction.atomic():
        project = get_object_or_404(Project.objects.visible_to(request.user).select_for_update(of=("self",)), pk=project_id)
        if not can_manage(request.user, project):
            return JsonResponse({"error": "Geen beheerrechten."}, status=403)
        member = get_object_or_404(project.memberships, user_id=user_id)
        if member.role == Membership.Role.ADMIN:
            others = project.memberships.filter(role=Membership.Role.ADMIN, user__is_active=True).exclude(pk=member.pk)
            if not others.exists():
                return JsonResponse({"error": "Behoud minimaal één actieve projectbeheerder."}, status=409)
        member.delete()
        return JsonResponse({"removed": True})
