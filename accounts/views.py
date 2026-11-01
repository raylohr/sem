from functools import wraps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404,render,redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST
from .models import User,AuditEvent
from .forms import AccountCreateForm,AccountUpdateForm,AdminPasswordForm,ResetMFAForm,MemberForm
from .services import create_account,update_account,admin_password_reset,reset_mfa


def platform_required(view):
    @never_cache
    @login_required
    @wraps(view)
    def wrapped(request,*args,**kwargs):
        if not request.user.is_superuser or not request.user.is_verified():
            raise PermissionDenied
        return view(request,*args,**kwargs)
    return wrapped


@platform_required
def users(request):
    query=request.GET.get('q','').strip()[:200]
    rows=User.objects.order_by('username')
    if query:
        from django.db.models import Q
        rows=rows.filter(Q(username__icontains=query)|Q(email__icontains=query))
    return render(request,'accounts/users.html',{'page':Paginator(rows,25).get_page(request.GET.get('page')),'query':query,'active':'users'})


@platform_required
@sensitive_post_parameters()
def user_create(request):
    form=AccountCreateForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        try:
            user=create_account(actor=request.user,form=form)
            return redirect('accounts:user_edit',user_id=user.pk)
        except ValidationError as exc:
            form.add_error(None,' '.join(exc.messages))
    return render(request,'accounts/form.html',{'form':form,'heading':'Gebruiker aanmaken','description':'Geef het tijdelijke wachtwoord rechtstreeks aan de gebruiker. Bij de eerste login moet deze een eigen wachtwoord kiezen.','submit':'Gebruiker aanmaken','active':'users'})


@platform_required
@sensitive_post_parameters()
def user_edit(request,user_id):
    user=get_object_or_404(User,pk=user_id)
    form=AccountUpdateForm(request.POST or None,instance=user)
    saved=False
    if request.method=='POST' and form.is_valid():
        try:
            user=update_account(actor=request.user,user_id=user.pk,data=form.cleaned_data)
            saved=True
        except ValidationError as exc:
            form.add_error(None,' '.join(exc.messages))
    return render(request,'accounts/user_edit.html',{'form':form,'target':user,'saved':saved,'active':'users'})


@platform_required
@sensitive_post_parameters()
def user_password(request,user_id):
    user=get_object_or_404(User,pk=user_id)
    if user.pk==request.user.pk:
        return redirect('password_change')
    form=AdminPasswordForm(user,request.POST or None,actor=request.user,request=request)
    if request.method=='POST' and form.is_valid():
        admin_password_reset(actor=request.user,user_id=user.pk,password=form.cleaned_data['new_password1'])
        return redirect('accounts:user_edit',user_id=user.pk)
    return render(request,'accounts/form.html',{'form':form,'heading':f'Tijdelijk wachtwoord voor {user.get_username()}','description':'Alle sessies worden ingetrokken. De gebruiker moet dit wachtwoord bij de volgende login wijzigen.','submit':'Tijdelijk wachtwoord instellen','active':'users'})


@platform_required
@sensitive_post_parameters()
def user_mfa_reset(request,user_id):
    user=get_object_or_404(User,pk=user_id)
    form=ResetMFAForm(request.POST or None,user=request.user,request=request)
    if request.method=='POST' and form.is_valid():
        try:
            reset_mfa(actor=request.user,user_id=user.pk,reason=form.cleaned_data['reason'])
            return redirect('accounts:user_edit',user_id=user.pk)
        except ValidationError as exc:
            form.add_error(None,' '.join(exc.messages))
    return render(request,'accounts/form.html',{'form':form,'heading':f'2FA herstellen voor {user.get_username()}','description':'Controleer eerst de identiteit van de gebruiker. Authenticator, herstelcodes en sessies worden ingetrokken. Opnieuw instellen wordt verplicht.','submit':'2FA herstellen','active':'users'})


@platform_required
def audit(request):
    events=AuditEvent.objects.select_related('actor','target','project').all()
    return render(request,'accounts/audit.html',{'page':Paginator(events,50).get_page(request.GET.get('page')),'active':'users'})


@never_cache
@login_required
def project_access(request,project_id):
    from core.ui import project_context
    from projects.services import set_membership
    context=project_context(request,project_id,'access')
    if not context['can_manage']:
        raise PermissionDenied
    form=MemberForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        target=User.objects.filter(email__iexact=form.cleaned_data['email'],is_active=True).first()
        if target is None:
            form.add_error('email','Geen actieve gebruiker gevonden voor dit e-mailadres.')
        else:
            try:
                set_membership(actor=request.user,project_id=project_id,user_id=target.pk,role=form.cleaned_data['role'])
                return redirect('accounts:project_access',project_id=project_id)
            except ValidationError as exc:
                form.add_error(None,' '.join(exc.messages))
    context.update(form=form,memberships=context['project'].memberships.select_related('user').order_by('user__username'))
    return render(request,'accounts/project_access.html',context)


@never_cache
@login_required
@require_POST
def project_access_remove(request,project_id,user_id):
    from core.ui import project_context
    from projects.services import remove_membership
    context=project_context(request,project_id,'access')
    if not context['can_manage']:
        raise PermissionDenied
    get_object_or_404(context['project'].memberships,user_id=user_id)
    try:
        remove_membership(actor=request.user,project_id=project_id,user_id=user_id)
    except ValidationError as exc:
        context.update(form=MemberForm(),memberships=context['project'].memberships.select_related('user'),error=' '.join(exc.messages))
        return render(request,'accounts/project_access.html',context,status=409)
    return redirect('accounts:project_access',project_id=project_id)
