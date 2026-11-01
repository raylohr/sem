from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower
from django.utils.crypto import salted_hmac


class User(AbstractUser):
    mfa_required = models.BooleanField(default=False, db_default=False)
    must_change_password = models.BooleanField(default=False, db_default=False)
    auth_epoch = models.PositiveIntegerField(default=0, db_default=0)

    class Meta:
        constraints = [models.UniqueConstraint(Lower("email"),condition=~models.Q(email=""),name="unique_account_email_ci")]

    def _get_session_auth_hash(self,secret=None):
        value=super()._get_session_auth_hash(secret=secret)
        return salted_hmac("accounts.session_epoch",f"{value}:{self.auth_epoch}",secret=secret or settings.SECRET_KEY,algorithm="sha256").hexdigest()


class AuditEvent(models.Model):
    class Action(models.TextChoices):
        LOGIN="login","Ingelogd"
        RECOVERY_USED="recovery_code_used","Herstelcode gebruikt"
        CREATED="user_created","Gebruiker aangemaakt"
        UPDATED="user_updated","Gebruiker bijgewerkt"
        PASSWORD_ADMIN="password_admin_reset","Tijdelijk wachtwoord ingesteld"
        MFA_RESET="mfa_reset","2FA hersteld"
        PASSWORD_CHANGED="password_changed","Wachtwoord gewijzigd"
        MFA_ENABLED="mfa_enabled","2FA ingesteld"
        MFA_DISABLED="mfa_disabled","2FA uitgeschakeld"
        MFA_REPLACED="mfa_replacement_started","Vervangen authenticator gestart"
        RECOVERY_GENERATED="recovery_codes_generated","Herstelcodes vernieuwd"
        EMAIL_RESET="password_email_reset","Wachtwoord hersteld via e-mail"
        ACCESS_SET="project_access_set","Projecttoegang ingesteld"
        ACCESS_REMOVED="project_access_removed","Projecttoegang ingetrokken"
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name="security_actions")
    target = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name="security_events")
    project = models.ForeignKey("projects.Project",null=True,blank=True,on_delete=models.SET_NULL)
    action = models.CharField(max_length=64,choices=Action.choices)
    occurred_at = models.DateTimeField(auto_now_add=True)
    details = models.JSONField(default=dict,blank=True)
    class Meta:
        ordering=["-occurred_at","-id"]
