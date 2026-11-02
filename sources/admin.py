from django.contrib import admin
from projects.admin import PlatformOnlyAdmin
from .models import ImportBatch, BatchDeletionEvent


class ReadOnlyPlatformAdmin(PlatformOnlyAdmin):
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ImportBatch)
class BatchAdmin(ReadOnlyPlatformAdmin):
    list_display = ("label", "project", "source", "created_by", "imported_at", "status", "record_count")
    list_filter = ("source", "status", "project")


@admin.register(BatchDeletionEvent)
class DeletionAdmin(ReadOnlyPlatformAdmin):
    list_display = ("batch_id", "project", "project_identifier", "actor", "deleted_at")
