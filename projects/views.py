import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST
from .models import Project, Membership
from .permissions import role_for, can_manage


def project_data(project, user):
    return {"id": str(project.pk), "name": project.name, "website": project.website,
            "host_rules": project.host_rules, "role": role_for(user, project)}


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
    from django.core.exceptions import ValidationError
    from .services import set_membership
    project=get_object_or_404(Project.objects.visible_to(request.user),pk=project_id)
    if not can_manage(request.user,project):
        return JsonResponse({"error":"Geen beheerrechten."},status=403)
    try:
        body=json.loads(request.body)
        user_id,role=body["user_id"],body["role"]
        if not isinstance(user_id,int) or isinstance(user_id,bool) or role not in Membership.Role.values:
            raise ValueError
    except (ValueError,TypeError,KeyError):
        return JsonResponse({"error":"Geef user_id en een geldige role op."},status=400)
    try:
        member=set_membership(actor=request.user,project_id=project.pk,user_id=user_id,role=role)
    except ValidationError as exc:
        message=" ".join(exc.messages)
        return JsonResponse({"error":message},status=409 if "minimaal" in message else 400)
    return JsonResponse({"user_id":member.user_id,"role":member.role})


@login_required
@require_POST
def membership_remove(request, project_id, user_id):
    from django.core.exceptions import ValidationError
    from .services import remove_membership
    project=get_object_or_404(Project.objects.visible_to(request.user),pk=project_id)
    if not can_manage(request.user,project):
        return JsonResponse({"error":"Geen beheerrechten."},status=403)
    get_object_or_404(Membership,project=project,user_id=user_id)
    try:
        remove_membership(actor=request.user,project_id=project.pk,user_id=user_id)
    except ValidationError as exc:
        return JsonResponse({"error":" ".join(exc.messages)},status=409)
    return JsonResponse({"removed":True})
