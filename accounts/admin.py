from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from two_factor.admin import AdminSiteOTPRequired
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp.plugins.otp_static.models import StaticDevice,StaticToken
from .models import User,AuditEvent


class PlatformAdminSite(AdminSiteOTPRequired):
    def has_permission(self,request):
        return request.user.is_superuser and super().has_permission(request)


admin.site.__class__=PlatformAdminSite
for model in (TOTPDevice,StaticDevice,StaticToken):
    if admin.site.is_registered(model):
        admin.site.unregister(model)


@admin.register(User)
class AccountAdmin(UserAdmin):
    def has_module_permission(self,request):
        return request.user.is_superuser and request.user.is_verified()
    def has_view_permission(self,request,obj=None):
        return self.has_module_permission(request)
    def has_add_permission(self,request):
        return False
    def has_change_permission(self,request,obj=None):
        return False
    def has_delete_permission(self,request,obj=None):
        return False
    def get_queryset(self,request):
        return super().get_queryset(request) if self.has_module_permission(request) else super().get_queryset(request).none()


@admin.register(AuditEvent)
class AuditAdmin(admin.ModelAdmin):
    list_display=('occurred_at','action','actor','target','project')
    def has_module_permission(self,request):
        return request.user.is_superuser and request.user.is_verified()
    def has_view_permission(self,request,obj=None):
        return self.has_module_permission(request)
    def has_add_permission(self,request):return False
    def has_change_permission(self,request,obj=None):return False
    def has_delete_permission(self,request,obj=None):return False
