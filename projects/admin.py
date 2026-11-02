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

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        return {}

    def website_redirect(self, request, name, **kwargs):
        from django.core.exceptions import PermissionDenied
        from django.shortcuts import redirect
        if request.method not in ('GET', 'HEAD') or not self.has_module_permission(request):
            raise PermissionDenied
        if 'project_id' in kwargs:
            import uuid
            from django.http import Http404
            try:
                kwargs['project_id'] = uuid.UUID(str(kwargs['project_id']))
            except ValueError:
                raise Http404
        return redirect(name, **kwargs)

    def add_view(self, request, form_url='', extra_context=None):
        return self.website_redirect(request, 'ui:project_create')

    def change_view(self, request, object_id, form_url='', extra_context=None):
        return self.website_redirect(request, 'ui:project_edit', project_id=object_id)

    def delete_view(self, request, object_id, extra_context=None):
        return self.website_redirect(request, 'ui:project_delete', project_id=object_id)


@admin.register(Membership)
class MembershipAdmin(PlatformOnlyAdmin):
    list_display = ("project", "user", "role")
    list_filter = ("role", "project")

    def has_add_permission(self,request):
        return False
    def has_change_permission(self,request,obj=None):
        return False
    def has_delete_permission(self,request,obj=None):
        return False
