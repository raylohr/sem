from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp.plugins.otp_static.models import StaticDevice
from projects.models import Project,Membership
from .models import User,AuditEvent
from .policy import lock_access_changes


def platform_actor(actor):
    current=User.objects.get(pk=actor.pk)
    if not current.is_active or not current.is_superuser or current.auth_epoch!=actor.auth_epoch or current.password!=actor.password:
        raise PermissionDenied


@transaction.atomic
def create_account(*,actor,form):
    lock_access_changes();platform_actor(actor)
    user=form.save(commit=False)
    user.is_superuser=bool(form.cleaned_data['platform_admin'])
    user.is_staff=user.is_superuser
    user.mfa_required=user.is_superuser or bool(form.cleaned_data.get('mfa_required'))
    user.is_active=True
    user.must_change_password=True
    user.full_clean()
    user.save()
    AuditEvent.objects.create(actor=actor,target=user,action='user_created',details={'platform_admin':user.is_superuser,'mfa_required':user.mfa_required})
    return user


@transaction.atomic
def update_account(*,actor,user_id,data):
    lock_access_changes();platform_actor(actor)
    user=User.objects.select_for_update().get(pk=user_id)
    active=bool(data['is_active']);superuser=bool(data['platform_admin'])
    if actor.pk==user.pk and (not active or not superuser):
        raise ValidationError('Je kunt jezelf niet deactiveren of je eigen platformbeheer afnemen.')
    if user.is_superuser and user.is_active and (not active or not superuser) and not User.objects.filter(is_active=True,is_superuser=True).exclude(pk=user.pk).exists():
        raise ValidationError('Behoud minimaal één actieve platformbeheerder.')
    if user.is_active and not active:
        ids=list(Membership.objects.filter(user=user,role='admin').values_list('project_id',flat=True))
        projects=Project.objects.select_for_update().filter(pk__in=ids).order_by('pk')
        for project in projects:
            if not Membership.objects.filter(project=project,role='admin',user__is_active=True).exclude(user=user).exists():
                raise ValidationError(f'Wijs eerst een andere projectbeheerder toe aan {project.name}.')
    previous={key:getattr(user,key) for key in ('email','is_active','is_superuser','mfa_required')}
    for key in ('email','first_name','last_name'):
        setattr(user,key,data[key])
    user.is_active=active;user.is_superuser=superuser;user.is_staff=superuser
    user.mfa_required=superuser or bool(data.get('mfa_required'))
    if any(previous[key]!=getattr(user,key) for key in previous):
        user.auth_epoch+=1
    user.full_clean();user.save()
    AuditEvent.objects.create(actor=actor,target=user,action='user_updated',details={'platform_admin':superuser,'active':active,'mfa_required':user.mfa_required})
    return user


@transaction.atomic
def admin_password_reset(*,actor,user_id,password):
    lock_access_changes();platform_actor(actor)
    user=User.objects.select_for_update().get(pk=user_id)
    user.set_password(password);user.must_change_password=True;user.auth_epoch+=1
    user.save(update_fields=['password','must_change_password','auth_epoch'])
    AuditEvent.objects.create(actor=actor,target=user,action='password_admin_reset')
    return user


@transaction.atomic
def reset_mfa(*,actor,user_id,reason,operator=None):
    lock_access_changes()
    if actor is not None:
        platform_actor(actor)
        if actor.pk==user_id:
            raise ValidationError('Gebruik voor je eigen account je authenticator of herstelcodes.')
    user=User.objects.select_for_update().get(pk=user_id)
    TOTPDevice.objects.filter(user=user).delete()
    StaticDevice.objects.filter(user=user).delete()
    user.mfa_required=True;user.auth_epoch+=1
    user.save(update_fields=['mfa_required','auth_epoch'])
    details={'reason':reason[:200]}
    if operator:
        details['operator']=operator
    AuditEvent.objects.create(actor=actor,target=user,action='mfa_reset',details=details)
    return user
