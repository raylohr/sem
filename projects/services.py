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


@transaction.atomic
def create_project(*, actor, name, website):
    from accounts.services import platform_actor
    lock_access_changes()
    platform_actor(actor)
    project = Project(name=name, website=website, created_by=actor)
    project.full_clean()
    project.save()
    Membership.objects.create(project=project, user=actor, role=Membership.Role.ADMIN)
    AuditEvent.objects.create(actor=actor, project=project, action='project_created',
                              details={'name': project.name, 'website': project.website})
    return project


@transaction.atomic
def update_project(*, actor, project_id, name, website):
    lock_access_changes()
    project = editable_project(actor, project_id)
    previous = {'name': project.name, 'website': project.website}
    project.name, project.website = name, website
    project.full_clean()
    project.save(update_fields=['name', 'website'])
    AuditEvent.objects.create(actor=actor, project=project, action='project_updated',
                              details={'before': previous, 'after': {'name': name, 'website': website}})
    return project


@transaction.atomic
def delete_project(*, actor, project_id, confirmation_name):
    from accounts.services import platform_actor
    from sources.models import ImportBatch, BatchDeletionEvent, PendingFileDeletion
    from sources.tasks import enqueue_cleanup
    from integrations.models import GSCConnection
    from scans.models import ScanTarget
    lock_access_changes()
    platform_actor(actor)
    project = Project.objects.select_for_update().get(pk=project_id)
    if confirmation_name != project.name:
        raise ValidationError('De projectnaam is gewijzigd. Controleer de bevestiging opnieuw.')
    batches = list(ImportBatch.objects.filter(project=project).values_list('id', 'file', 'scan_target_id'))
    for batch_id, file_path, scan_target_id in batches:
        BatchDeletionEvent.objects.create(project=project, project_identifier=project.pk,
                                           batch_id=batch_id, actor=actor)
        if file_path:
            PendingFileDeletion.objects.get_or_create(path=file_path)
        if scan_target_id:
            from scans.work import work_paths
            for path in work_paths(batch_id):
                PendingFileDeletion.objects.get_or_create(path=path)
    # Remove protected children in dependency order; late workers cannot recreate batches.
    ImportBatch.objects.filter(project=project).delete()
    GSCConnection.objects.filter(project=project).delete()
    ScanTarget.objects.filter(project=project).delete()
    AuditEvent.objects.create(actor=actor, action='project_deleted',
                              details={'project_id': str(project.pk), 'name': project.name,
                                       'batches': len(batches)})
    # Preserve project identity even for deletion events written by an older release.
    BatchDeletionEvent.objects.filter(project=project, project_identifier__isnull=True).update(project_identifier=project.pk)
    for event in AuditEvent.objects.filter(project=project).iterator():
        event.details = {**event.details, "project_id": str(project.pk)}
        event.save(update_fields=["details"])
    project.delete()
    transaction.on_commit(enqueue_cleanup)
