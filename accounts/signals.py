import time
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from two_factor.signals import user_verified
from django_otp.plugins.otp_static.models import StaticDevice
from .models import AuditEvent
from .policy import auth_success


@receiver(user_logged_in)
def login_record(sender,request,user,**kwargs):
    request.session["password_confirmed_at"]=time.time()
    auth_success(user.get_username(),request.META.get("REMOTE_ADDR",""))
    AuditEvent.objects.create(actor=user,target=user,action="login")


@receiver(user_verified)
def recovery_record(sender,request,user,device,**kwargs):
    if isinstance(device,StaticDevice):
        AuditEvent.objects.create(actor=user,target=user,action="recovery_code_used")
