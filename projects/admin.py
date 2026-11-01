from django.contrib import admin
from .models import Project, Membership


class PlatformOnlyAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def get_queryset(self, request):
        return super().get_queryset(request).visible_to(request.user) if self.model is Project else (
            super().get_queryset(request) if self.has_module_permission(request) else super().get_queryset(request).none())


@admin.register(Project)
class ProjectAdmin(PlatformOnlyAdmin):
    list_display = ("name", "website", "created_at")
    readonly_fields = ("created_by", "created_at")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Membership)
class MembershipAdmin(PlatformOnlyAdmin):
    list_display = ("project", "user", "role")
    list_filter = ("role", "project")
