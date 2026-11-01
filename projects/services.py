from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from accounts.models import User,AuditEvent
from accounts.policy import lock_access_changes
from .models import Project,Membership
from .permissions import can_manage


def editable_project(actor,project_id):
    current=User.objects.get(pk=actor.pk)
    if not current.is_active or current.auth_epoch!=actor.auth_epoch or current.password!=actor.password:
        raise PermissionDenied
    actor=current
    project=Project.objects.select_for_update().get(pk=project_id)
    if not can_manage(actor,project):
        raise PermissionDenied
    return project


def last_admin_guard(project,member):
    if member and member.role=='admin' and not project.memberships.filter(role='admin',user__is_active=True).exclude(pk=member.pk).exists():
        raise ValidationError('Behoud minimaal één actieve projectbeheerder.')


@transaction.atomic
def set_membership(*,actor,project_id,user_id,role):
    lock_access_changes()
    project=editable_project(actor,project_id)
    if role not in Membership.Role.values:
        raise ValidationError('Ongeldige projectrol.')
    user=User.objects.select_for_update().filter(pk=user_id,is_active=True).first()
    if user is None:
        raise ValidationError('Actieve gebruiker niet gevonden.')
    previous=project.memberships.filter(user=user).first()
    if previous and previous.role=='admin' and role!='admin':
        last_admin_guard(project,previous)
    member,_=Membership.objects.update_or_create(project=project,user=user,defaults={'role':role})
    AuditEvent.objects.create(actor=actor,target=user,project=project,action='project_access_set',details={'role':role})
    return member


@transaction.atomic
def remove_membership(*,actor,project_id,user_id):
    lock_access_changes()
    project=editable_project(actor,project_id)
    member=project.memberships.get(user_id=user_id)
    last_admin_guard(project,member)
    AuditEvent.objects.create(actor=actor,target=member.user,project=project,action='project_access_removed')
    member.delete()
