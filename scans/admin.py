from django.contrib import admin
from sources.admin import ReadOnlyPlatformAdmin
from .models import ScanTarget


@admin.register(ScanTarget)
class TargetAdmin(ReadOnlyPlatformAdmin):
    list_display = ('name', 'project', 'kind', 'url', 'active')
