import json,re
from urllib.parse import urlsplit
from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase,Client,RequestFactory,override_settings,SimpleTestCase
from django.urls import reverse
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp.plugins.otp_static.models import StaticDevice,StaticToken
from projects.models import Project,Membership
from .models import User,AuditEvent
from .services import update_account,admin_password_reset
from .auth_views import reset_tokens
from .forms import RateLimitedAuthenticationForm

PASSWORD='Original-Password-For-Tests!36'
NEW_PASSWORD='Changed-Password-For-Tests!92'


def verified_login(client,user):
    device,_=TOTPDevice.objects.get_or_create(user=user,name='default',defaults={'confirmed':True})
    client.force_login(user)
    session=client.session;session[DEVICE_ID_SESSION_KEY]=device.persistent_id;session.save()
    return device


def password_step(client,user,password=PASSWORD):
    response=client.get(reverse('login'))
    management=response.context['wizard']['management_form']['current_step'].html_name
    response=client.post(reverse('login'),{management:'auth','auth-username':user.get_username(),'auth-password':password})
    return response,management


class AccountSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin=User.objects.create_superuser('platform','platform@example.test',PASSWORD)
        self.user=User.objects.create_user('member','member@example.test',PASSWORD)
        self.project=Project.objects.create(name='Private project',website='https://example.com',created_by=self.admin)
        Membership.objects.create(project=self.project,user=self.admin,role='admin')
        Membership.objects.create(project=self.project,user=self.user,role='viewer')
        verified_login(self.client,self.admin)

    def test_platform_admin_without_device_is_forced_to_setup(self):
        TOTPDevice.objects.filter(user=self.admin).delete()
        self.client.force_login(self.admin)
        response=self.client.get(reverse('ui:dashboard'))
        self.assertEqual(response.status_code,302)
        self.assertTrue(response.url.startswith(reverse('two_factor:setup')))
        response=self.client.get(reverse('projects:list'))
        self.assertEqual(response.status_code,302)
        self.assertTrue(response.url.startswith(reverse('two_factor:setup')))

    def test_enabled_mfa_cannot_be_bypassed_by_password_session(self):
        TOTPDevice.objects.create(user=self.user,name='default')
        self.client.force_login(self.user)
        response=self.client.get(reverse('projects:list'))
        self.assertEqual(response.status_code,302)
        self.assertTrue(response.url.startswith(reverse('login')))

    def test_password_is_not_a_full_login_before_otp(self):
        device=TOTPDevice.objects.create(user=self.user,name='default')
        client=Client()
        response,management=password_step(client,self.user)
        self.assertEqual(response.context['wizard']['steps'].current,'token')
        self.assertNotIn('_auth_user_id',client.session)
        self.assertEqual(client.get(reverse('projects:list')).status_code,302)
        code=f'{totp(device.bin_key):06d}'
        response=client.post(reverse('login'),{management:'token','token-otp_token':code})
        self.assertEqual(response.status_code,302)
        self.assertEqual(client.session[DEVICE_ID_SESSION_KEY],device.persistent_id)
        self.assertEqual(client.get(reverse('projects:list')).status_code,200)
        device.refresh_from_db()
        self.assertFalse(device.verify_token(code))

    def test_recovery_code_is_consumed_and_audited(self):
        TOTPDevice.objects.create(user=self.user,name='default')
        backup=StaticDevice.objects.create(user=self.user,name='backup')
        StaticToken.objects.create(device=backup,token='rescue01')
        client=Client();response,management=password_step(client,self.user)
        response=client.post(reverse('login'),{'wizard_goto_step':'backup'})
        self.assertEqual(response.context['wizard']['steps'].current,'backup')
        response=client.post(reverse('login'),{management:'backup','backup-otp_token':'rescue01'})
        self.assertEqual(response.status_code,302)
        self.assertFalse(StaticToken.objects.filter(token='rescue01').exists())
        self.assertTrue(AuditEvent.objects.filter(target=self.user,action='recovery_code_used').exists())
        self.assertEqual(client.get(reverse('projects:list')).status_code,200)

    def test_rate_limit_blocks_correct_password_after_failed_attempts(self):
        request=RequestFactory().post(reverse('login'));request.META['REMOTE_ADDR']='192.0.2.10'
        for _ in range(5):
            self.assertFalse(RateLimitedAuthenticationForm(request,data={'username':self.user.username,'password':'wrong'}).is_valid())
        form=RateLimitedAuthenticationForm(request,data={'username':self.user.username,'password':PASSWORD})
        self.assertFalse(form.is_valid())
        self.assertIn('Te veel',str(form.errors))

    def test_empty_login_is_a_validation_error_not_a_crash(self):
        request=RequestFactory().post(reverse('login'))
        self.assertFalse(RateLimitedAuthenticationForm(request,data={}).is_valid())

    def test_mandatory_mfa_cannot_be_disabled(self):
        self.assertEqual(self.client.get(reverse('two_factor:disable')).status_code,403)
        self.assertEqual(self.client.post(reverse('two_factor:disable'),{'password':PASSWORD}).status_code,403)

    def test_admin_mfa_reset_revokes_sessions_and_requires_new_enrollment(self):
        target_client=Client();verified_login(target_client,self.user)
        backup=StaticDevice.objects.create(user=self.user,name='backup');StaticToken.objects.create(device=backup,token='rescue01')
        old_token=reset_tokens.make_token(self.user)
        response=self.client.post(reverse('accounts:user_mfa_reset',args=[self.user.pk]),{'password':PASSWORD,'reason':'Verified identity; lost authenticator'})
        self.assertEqual(response.status_code,302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.mfa_required)
        self.assertFalse(TOTPDevice.objects.filter(user=self.user).exists())
        self.assertFalse(StaticDevice.objects.filter(user=self.user).exists())
        self.assertFalse(reset_tokens.check_token(self.user,old_token))
        self.assertEqual(target_client.get(reverse('ui:dashboard')).status_code,302)
        self.assertNotIn('_auth_user_id',target_client.session)
        response,_=password_step(target_client,self.user)
        self.assertEqual(response.status_code,302)
        response=target_client.get(reverse('ui:dashboard'))
        self.assertTrue(response.url.startswith(reverse('two_factor:setup')))

    def test_regular_user_cannot_manage_users(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('accounts:users')).status_code,403)
        self.assertEqual(self.client.get(reverse('accounts:user_create')).status_code,403)

    def test_user_creation_requires_own_password_change_and_honors_mfa_policy(self):
        response=self.client.post(reverse('accounts:user_create'),{'username':'created','email':'created@example.test','first_name':'New','last_name':'User','mfa_required':'on','password1':NEW_PASSWORD,'password2':NEW_PASSWORD})
        self.assertEqual(response.status_code,302)
        created=User.objects.get(username='created')
        self.assertTrue(created.must_change_password)
        self.assertTrue(created.mfa_required)
        self.assertFalse(created.is_superuser)
        self.assertTrue(AuditEvent.objects.filter(target=created,action='user_created').exists())
        self.assertNotIn(NEW_PASSWORD,json.dumps(list(AuditEvent.objects.values_list('details',flat=True))))

    def test_platform_creation_always_requires_mfa(self):
        response=self.client.post(reverse('accounts:user_create'),{'username':'newplatform','email':'newplatform@example.test','platform_admin':'on','password1':NEW_PASSWORD,'password2':NEW_PASSWORD})
        self.assertEqual(response.status_code,302)
        created=User.objects.get(username='newplatform')
        self.assertTrue(created.is_superuser and created.is_staff and created.mfa_required)

    def test_email_is_unique_case_insensitively(self):
        response=self.client.post(reverse('accounts:user_create'),{'username':'duplicate','email':'MEMBER@example.test','password1':NEW_PASSWORD,'password2':NEW_PASSWORD})
        self.assertEqual(response.status_code,200)
        self.assertFalse(User.objects.filter(username='duplicate').exists())
        self.assertContains(response,'al in gebruik')

    def test_disabling_last_project_admin_is_prevented(self):
        project=Project.objects.create(name='Last admin project',website='https://example.org',created_by=self.admin)
        Membership.objects.create(project=project,user=self.user,role='admin')
        with self.assertRaises(ValidationError):
            update_account(actor=self.admin,user_id=self.user.pk,data={'email':self.user.email,'first_name':'','last_name':'','is_active':False,'platform_admin':False,'mfa_required':False})
        self.user.refresh_from_db();self.assertTrue(self.user.is_active)

    def test_cannot_deactivate_or_demote_self(self):
        with self.assertRaises(ValidationError):
            update_account(actor=self.admin,user_id=self.admin.pk,data={'email':self.admin.email,'first_name':'','last_name':'','is_active':False,'platform_admin':False,'mfa_required':False})

    def test_temporary_password_reset_requires_first_login_change(self):
        user_client=Client();user_client.force_login(self.user)
        response=self.client.post(reverse('accounts:user_password',args=[self.user.pk]),{'actor_password':PASSWORD,'new_password1':NEW_PASSWORD,'new_password2':NEW_PASSWORD})
        self.assertEqual(response.status_code,302)
        self.user.refresh_from_db();self.assertTrue(self.user.must_change_password)
        self.assertNotIn('_auth_user_id',user_client.get(reverse('ui:dashboard')).wsgi_request.session)
        response,_=password_step(user_client,self.user,NEW_PASSWORD)
        self.assertEqual(response.status_code,302)
        response=user_client.get(reverse('ui:dashboard'))
        self.assertTrue(response.url.startswith(reverse('password_change')))
        replacement='User-Owned-Password-For-Tests!87'
        response=user_client.post(reverse('password_change'),{'old_password':NEW_PASSWORD,'new_password1':replacement,'new_password2':replacement})
        self.assertEqual(response.status_code,302)
        self.user.refresh_from_db();self.assertFalse(self.user.must_change_password)
        self.assertTrue(self.user.check_password(replacement))
        self.assertEqual(user_client.get(reverse('ui:dashboard')).status_code,200)

    def test_admin_reset_without_correct_actor_password_does_not_change_target(self):
        response=self.client.post(reverse('accounts:user_password',args=[self.user.pk]),{'actor_password':'wrong','new_password1':NEW_PASSWORD,'new_password2':NEW_PASSWORD})
        self.assertEqual(response.status_code,200)
        self.user.refresh_from_db();self.assertTrue(self.user.check_password(PASSWORD))

    def test_project_manager_can_assign_by_email_and_change_is_logged(self):
        Membership.objects.filter(project=self.project,user=self.user).update(role='admin')
        target=User.objects.create_user('recipient','recipient@example.test',PASSWORD)
        self.client.force_login(self.user)
        response=self.client.post(reverse('accounts:project_access',args=[self.project.pk]),{'email':'recipient@example.test','role':'editor'})
        self.assertEqual(response.status_code,302)
        self.assertTrue(Membership.objects.filter(project=self.project,user=target,role='editor').exists())
        self.assertTrue(AuditEvent.objects.filter(actor=self.user,target=target,project=self.project,action='project_access_set').exists())
        self.assertContains(self.client.get(reverse('accounts:project_access',args=[self.project.pk])),'platform@example.test')

    def test_project_viewer_cannot_view_access_management(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('accounts:project_access',args=[self.project.pk])).status_code,403)

    def test_reauth_required_before_recovery_codes_can_be_viewed(self):
        session=self.client.session;session['password_confirmed_at']=0;session.save()
        response=self.client.get(reverse('two_factor:backup_tokens'))
        self.assertEqual(response.status_code,302)
        self.assertTrue(response.url.startswith(reverse('accounts:confirm_password')))

    def test_backup_code_rotation_invalidates_other_sessions(self):
        other=Client();verified_login(other,self.admin)
        response=self.client.post(reverse('two_factor:backup_tokens'))
        self.assertEqual(response.status_code,302)
        self.assertEqual(StaticToken.objects.filter(device__user=self.admin).count(),10)
        self.assertEqual(other.get(reverse('ui:dashboard')).status_code,302)
        self.assertNotIn('_auth_user_id',other.session)
        self.assertEqual(self.client.get(reverse('ui:dashboard')).status_code,200)

    def test_setup_secret_is_not_available_after_reauth_expiry(self):
        self.client.force_login(self.user)
        session=self.client.session;session['password_confirmed_at']=0;session.save()
        response=self.client.get(reverse('two_factor:setup'))
        self.assertTrue(response.url.startswith(reverse('accounts:confirm_password')))

    def test_reauth_next_cannot_redirect_offsite(self):
        response=self.client.post(reverse('accounts:confirm_password'),{'password':PASSWORD,'next':'https://evil.example/'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(response.url,reverse('two_factor:profile'))

    @override_settings(ACCOUNT_EMAIL_ENABLED=False)
    def test_disabled_mail_does_not_claim_to_send(self):
        self.client.logout()
        response=self.client.get(reverse('password_reset'))
        self.assertContains(response,'nog niet ingesteld')
        self.assertEqual(self.client.post(reverse('password_reset'),{'email':self.user.email}).status_code,503)

    @override_settings(ACCOUNT_EMAIL_ENABLED=True,EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',DEFAULT_FROM_EMAIL='sem@example.test',PUBLIC_BASE_URL='https://sem.lohr.nl')
    def test_email_reset_does_not_auto_login_or_remove_mfa(self):
        TOTPDevice.objects.create(user=self.user,name='default')
        self.client.logout()
        response=self.client.post(reverse('password_reset'),{'email':self.user.email})
        self.assertEqual(response.status_code,302)
        self.assertEqual(len(mail.outbox),1)
        link=re.search(r'https://sem\.lohr\.nl/\S+',mail.outbox[0].body).group(0)
        path=urlsplit(link).path
        response=self.client.get(path)
        self.assertEqual(response.status_code,302)
        response=self.client.post(response.url,{'new_password1':NEW_PASSWORD,'new_password2':NEW_PASSWORD})
        self.assertEqual(response.status_code,302)
        self.user.refresh_from_db();self.assertTrue(self.user.check_password(NEW_PASSWORD))
        self.assertTrue(TOTPDevice.objects.filter(user=self.user).exists())
        self.assertNotIn('_auth_user_id',self.client.session)
        self.assertContains(self.client.get(path),'verlopen of al gebruikt')
        response=self.client.post(reverse('password_reset'),{'email':'unknown@example.test'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(len(mail.outbox),1)

    def test_unauthenticated_admin_password_login_is_redirected(self):
        self.client.logout()
        response=self.client.get(reverse('admin:login'))
        self.assertEqual(response.status_code,302)
        self.assertTrue(response.url.startswith(reverse('login')))

    def test_full_setup_enrolls_authenticator_and_serves_local_qr(self):
        self.client.force_login(self.user)
        response=self.client.get(reverse('two_factor:setup'))
        management=response.context['wizard']['management_form']['current_step'].html_name
        response=self.client.post(reverse('two_factor:setup'),{management:'welcome'})
        self.assertEqual(response.context['wizard']['steps'].current,'generator')
        self.assertEqual(self.client.get(response.context['QR_URL']).status_code,200)
        import base64
        key=base64.b32decode(response.context['secret_key'].upper())
        response=self.client.post(reverse('two_factor:setup'),{management:'generator','generator-token':f'{totp(key):06d}'})
        self.assertEqual(response.status_code,302)
        self.assertTrue(TOTPDevice.objects.filter(user=self.user,confirmed=True).exists())
        self.assertTrue(AuditEvent.objects.filter(target=self.user,action='mfa_enabled').exists())
        self.assertEqual(self.client.get(reverse('ui:dashboard')).status_code,200)

    def test_replacing_authenticator_forces_reenrollment_and_revokes_old_sessions(self):
        other=Client();verified_login(other,self.admin)
        response=self.client.post(reverse('two_factor:replace'),{'password':PASSWORD})
        self.assertRedirects(response,reverse('two_factor:setup'),fetch_redirect_response=False)
        self.assertFalse(TOTPDevice.objects.filter(user=self.admin).exists())
        self.assertTrue(AuditEvent.objects.filter(target=self.admin,action='mfa_replacement_started').exists())
        self.assertTrue(self.client.get(reverse('ui:dashboard')).url.startswith(reverse('two_factor:setup')))
        other.get(reverse('ui:dashboard'))
        self.assertNotIn('_auth_user_id',other.session)

    def test_revoked_platform_actor_cannot_apply_stale_account_change(self):
        User.objects.filter(pk=self.admin.pk).update(auth_epoch=1)
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            admin_password_reset(actor=self.admin,user_id=self.user.pk,password=NEW_PASSWORD)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_https_login_with_csrf_enforcement_and_same_origin_provenance(self):
        for provenance in ({'HTTP_ORIGIN':'https://sem.lohr.nl'},
                           {'HTTP_REFERER':'https://sem.lohr.nl/accounts/login/'}):
            client=Client(enforce_csrf_checks=True)
            response=client.get(reverse('login'),secure=True,HTTP_HOST='sem.lohr.nl')
            self.assertEqual(response['Referrer-Policy'],'same-origin')
            management=response.context['wizard']['management_form']['current_step'].html_name
            response=client.post(reverse('login'),{
                management:'auth','auth-username':self.user.username,'auth-password':PASSWORD,
                'csrfmiddlewaretoken':client.cookies[settings.CSRF_COOKIE_NAME].value,
            },secure=True,HTTP_HOST='sem.lohr.nl',**provenance)
            self.assertEqual(response.status_code,302)
            self.assertEqual(client.session['_auth_user_id'],str(self.user.pk))

    def test_https_login_still_rejects_missing_token_or_untrusted_origin(self):
        client=Client(enforce_csrf_checks=True)
        client.get(reverse('login'),secure=True,HTTP_HOST='sem.lohr.nl')
        data={'csrfmiddlewaretoken':client.cookies[settings.CSRF_COOKIE_NAME].value}
        for origin in ('null','https://untrusted.example'):
            self.assertEqual(client.post(reverse('login'),data,secure=True,HTTP_HOST='sem.lohr.nl',HTTP_ORIGIN=origin).status_code,403)
        self.assertEqual(client.post(reverse('login'),{},secure=True,HTTP_HOST='sem.lohr.nl',HTTP_ORIGIN='https://sem.lohr.nl').status_code,403)

    def test_sensitive_views_are_not_cached(self):
        response=self.client.get(reverse('two_factor:backup_tokens'))
        self.assertIn('no-store',response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'],'same-origin')


class AccessLogTests(SimpleTestCase):
    def test_password_reset_tokens_and_query_strings_are_redacted(self):
        from .logging import safe_access_atoms
        atoms=safe_access_atoms({'U':'/accounts/reset/user/private-token/','r':'GET /accounts/reset/user/private-token/ HTTP/1.1','q':'?secret=code'},
            {'PATH_INFO':'/accounts/reset/user/private-token/','REQUEST_METHOD':'GET','SERVER_PROTOCOL':'HTTP/1.1'})
        self.assertNotIn('private-token',json.dumps(atoms))
        self.assertEqual(atoms['q'],'')
