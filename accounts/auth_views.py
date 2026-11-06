import time
from django.conf import settings
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordChangeView,PasswordResetView,PasswordResetConfirmView,INTERNAL_RESET_SESSION_TOKEN
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect,render
from django.urls import reverse,reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.generic import TemplateView
from urllib.parse import urlsplit
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp.plugins.otp_static.models import StaticDevice
from two_factor.views import LoginView,SetupView,SetupCompleteView,BackupTokensView,QRGeneratorView
from two_factor.forms import AuthenticationTokenForm,BackupTokenForm
from two_factor.utils import default_device,get_otpauth_url
from .models import User,AuditEvent
from .forms import RateLimitedAuthenticationForm,ConfirmPasswordForm,style_form
from .policy import recent_password,mfa_required,lock_access_changes,auth_allowed,auth_failure,auth_success


class AccountTokenGenerator(PasswordResetTokenGenerator):
    def _make_hash_value(self,user,timestamp):
        return super()._make_hash_value(user,timestamp)+str(user.auth_epoch)

reset_tokens=AccountTokenGenerator()


def safe_next(request,fallback):
    value=request.POST.get('next') or request.GET.get('next') or fallback
    return value if url_has_allowed_host_and_scheme(value,allowed_hosts={request.get_host()},require_https=request.is_secure()) else fallback


def current_self(request):
    user=User.objects.select_for_update().get(pk=request.user.pk)
    if not user.is_active or user.auth_epoch!=request.user.auth_epoch or user.password!=request.user.password:
        raise PermissionDenied('Accountbeveiliging gewijzigd; log opnieuw in.')
    return user


def bump_self(request,action):
    with transaction.atomic():
        user=User.objects.select_for_update().get(pk=request.user.pk)
        user.auth_epoch+=1;user.save(update_fields=['auth_epoch'])
        request.user.auth_epoch=user.auth_epoch
        AuditEvent.objects.create(actor=user,target=user,action=action)
    update_session_auth_hash(request,request.user)


class StyledWizard:
    def get_form(self,*args,**kwargs):
        return style_form(super().get_form(*args,**kwargs))


class SecurityLoginView(StyledWizard,LoginView):
    template_name='accounts/login.html'
    form_list=(('auth',RateLimitedAuthenticationForm),('token',AuthenticationTokenForm),('backup',BackupTokenForm))
    def get_context_data(self,form,**kwargs):
        context=super().get_context_data(form,**kwargs)
        context.update(next=self.get_redirect_url(),email_enabled=settings.ACCOUNT_EMAIL_ENABLED)
        return context


class RecentPasswordMixin:
    def dispatch(self,request,*args,**kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.path)
        if not recent_password(request):
            return redirect(reverse('accounts:confirm_password')+'?next='+request.path)
        return super().dispatch(request,*args,**kwargs)


@method_decorator(sensitive_post_parameters(),name='dispatch')
class SecuritySetupView(RecentPasswordMixin,StyledWizard,SetupView):
    template_name='accounts/mfa_setup.html'
    def get_context_data(self,form,**kwargs):
        context=super().get_context_data(form,**kwargs)
        if context.get('secret_key'):
            context['otpauth_url']=get_otpauth_url(self.request.user.get_username(),context['secret_key'],issuer='Search Visibility Toolkit')
        return context
    def post(self,request,*args,**kwargs):
        if 'generator-token' in request.POST:
            key=f"setup:{request.user.pk}"
            ip=request.META.get('REMOTE_ADDR','')
            if not auth_allowed(key,ip):
                form=self.get_form(data=request.POST)
                form.add_error(None,'Te veel pogingen. Probeer later opnieuw.')
                return self.render(form)
            auth_failure(key,ip)
        return super().post(request,*args,**kwargs)
    def done(self,form_list,**kwargs):
        with transaction.atomic():
            lock_access_changes()
            current_self(self.request)
            response=super().done(form_list,**kwargs)
            bump_self(self.request,'mfa_enabled')
        auth_success(f'setup:{self.request.user.pk}',self.request.META.get('REMOTE_ADDR',''))
        return response


class SecurityQRView(RecentPasswordMixin,QRGeneratorView):
    def get_issuer(self):
        return 'Search Visibility Toolkit'


class SecuritySetupCompleteView(SetupCompleteView):
    template_name='accounts/mfa_complete.html'


class SecurityBackupTokensView(RecentPasswordMixin,BackupTokensView):
    template_name='accounts/backup_tokens.html'
    def form_valid(self,form):
        with transaction.atomic():
            lock_access_changes()
            current_self(self.request)
            response=super().form_valid(form)
            bump_self(self.request,'recovery_codes_generated')
        return response


@method_decorator([never_cache,login_required],name='dispatch')
class SecurityProfileView(TemplateView):
    template_name='accounts/security.html'
    def get_context_data(self,**kwargs):
        context=super().get_context_data(**kwargs)
        context.update(device=default_device(self.request.user),mfa_required=mfa_required(self.request.user),
            backup_count=sum(d.token_set.count() for d in self.request.user.staticdevice_set.all()),active='security')
        return context


@never_cache
@login_required
@sensitive_post_parameters()
def confirm_password(request):
    form=ConfirmPasswordForm(request.POST or None,user=request.user,request=request)
    next_url=safe_next(request,reverse('two_factor:profile'))
    if request.method=='POST' and form.is_valid():
        request.session['password_confirmed_at']=time.time()
        return redirect(next_url)
    return render(request,'accounts/form.html',{'form':form,'heading':'Bevestig je wachtwoord','description':'Bevestig je identiteit om accountbeveiliging te wijzigen.','submit':'Bevestigen','next':next_url})


@never_cache
@login_required
@sensitive_post_parameters()
def disable_mfa(request):
    if mfa_required(request.user):
        raise PermissionDenied('2FA is verplicht voor dit account.')
    if not request.user.is_verified():
        raise PermissionDenied('Verifieer eerst met 2FA.')
    form=ConfirmPasswordForm(request.POST or None,user=request.user,request=request)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            lock_access_changes()
            fresh=current_self(request)
            if mfa_required(fresh):
                raise PermissionDenied('2FA is verplicht voor dit account.')
            TOTPDevice.objects.filter(user=request.user).delete()
            StaticDevice.objects.filter(user=request.user).delete()
            bump_self(request,'mfa_disabled')
        return redirect('two_factor:profile')
    return render(request,'accounts/form.html',{'form':form,'heading':'2FA uitschakelen','description':'Je authenticator en herstelcodes worden verwijderd. Andere sessies worden ingetrokken.','submit':'2FA uitschakelen'})


@method_decorator(sensitive_post_parameters(),name='dispatch')
class AccountPasswordChangeView(PasswordChangeView):
    template_name='accounts/password_change.html'
    success_url=reverse_lazy('two_factor:profile')
    def get_form(self,*args,**kwargs):
        return style_form(super().get_form(*args,**kwargs))
    def form_valid(self,form):
        with transaction.atomic():
            lock_access_changes()
            user=current_self(self.request)
            if not user.check_password(form.cleaned_data['old_password']):
                raise PermissionDenied('Wachtwoord gewijzigd; log opnieuw in.')
            user.set_password(form.cleaned_data['new_password1'])
            user.must_change_password=False
            user.save(update_fields=['password','must_change_password'])
            self.request.user.password=user.password
            self.request.user.must_change_password=False
            update_session_auth_hash(self.request,self.request.user)
            self.request.session['password_confirmed_at']=time.time()
            AuditEvent.objects.create(actor=user,target=user,action='password_changed')
        return redirect(self.get_success_url())



class AccountPasswordResetView(PasswordResetView):
    template_name='accounts/password_reset.html'
    email_template_name='accounts/email/password_reset.txt'
    subject_template_name='accounts/email/password_reset_subject.txt'
    success_url=reverse_lazy('password_reset_done')
    token_generator=reset_tokens
    def get_context_data(self,**kwargs):
        return {**super().get_context_data(**kwargs),'email_enabled':settings.ACCOUNT_EMAIL_ENABLED}
    def get_form(self,*args,**kwargs):
        return style_form(super().get_form(*args,**kwargs))
    def post(self,request,*args,**kwargs):
        if not settings.ACCOUNT_EMAIL_ENABLED:
            return render(request,self.template_name,self.get_context_data(),status=503)
        from .policy import auth_allowed,auth_failure
        ip=request.META.get('REMOTE_ADDR','')
        if not auth_allowed('password-reset:'+request.POST.get('email','').strip().casefold(),ip):
            return render(request,self.template_name,self.get_context_data(),status=429)
        auth_failure('password-reset:'+request.POST.get('email','').strip().casefold(),ip)
        return super().post(request,*args,**kwargs)
    def form_valid(self,form):
        site=urlsplit(settings.PUBLIC_BASE_URL)
        form.save(use_https=site.scheme=='https',domain_override=site.netloc,token_generator=self.token_generator,
            email_template_name=self.email_template_name,subject_template_name=self.subject_template_name,request=self.request)
        from django.http import HttpResponseRedirect
        return HttpResponseRedirect(self.get_success_url())


class AccountPasswordResetConfirmView(PasswordResetConfirmView):
    template_name='accounts/password_reset_confirm.html'
    token_generator=reset_tokens
    post_reset_login=False
    success_url=reverse_lazy('password_reset_complete')
    def get_form(self,*args,**kwargs):
        return style_form(super().get_form(*args,**kwargs))
    def form_valid(self,form):
        with transaction.atomic():
            lock_access_changes()
            user=User.objects.select_for_update().get(pk=self.user.pk)
            token=self.request.session.get(INTERNAL_RESET_SESSION_TOKEN,'')
            if not self.token_generator.check_token(user,token):
                self.validlink=False
                return self.render_to_response(self.get_context_data())
            user.set_password(form.cleaned_data['new_password1'])
            user.must_change_password=False;user.auth_epoch+=1
            user.save(update_fields=['password','must_change_password','auth_epoch'])
            self.request.session.pop(INTERNAL_RESET_SESSION_TOKEN,None)
            AuditEvent.objects.create(target=user,action='password_email_reset')
        return redirect(self.get_success_url())


@never_cache
@login_required
@sensitive_post_parameters()
def replace_mfa(request):
    if not request.user.is_verified():
        raise PermissionDenied('Verifieer eerst met 2FA of een herstelcode.')
    form=ConfirmPasswordForm(request.POST or None,user=request.user,request=request)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            lock_access_changes()
            user=current_self(request)
            TOTPDevice.objects.filter(user=user).delete()
            StaticDevice.objects.filter(user=user).delete()
            user.mfa_required=True;user.save(update_fields=['mfa_required'])
            request.user.mfa_required=True
            bump_self(request,'mfa_replacement_started')
            request.session['password_confirmed_at']=time.time()
        return redirect('two_factor:setup')
    return render(request,'accounts/form.html',{'form':form,'heading':'Authenticator vervangen','description':'Je huidige authenticator en herstelcodes worden ingetrokken. Daarna koppel je direct een nieuwe authenticator. Andere sessies worden afgemeld.','submit':'Nieuwe authenticator instellen'})
