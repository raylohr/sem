from .models import Membership


def role_for(user, project):
    if not user.is_authenticated or not user.is_active:
        return None
    if user.is_superuser:
        return Membership.Role.ADMIN
    return project.memberships.filter(user=user).values_list("role", flat=True).first()


def can_edit(user, project):
    return role_for(user, project) in {Membership.Role.ADMIN, Membership.Role.EDITOR}


def can_manage(user, project):
    return role_for(user, project) == Membership.Role.ADMIN
