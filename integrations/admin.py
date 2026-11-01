from django.contrib import admin
from sources.admin import ReadOnlyPlatformAdmin
from .models import GSCConnection


@admin.register(GSCConnection)
class ConnectionAdmin(ReadOnlyPlatformAdmin):
    fields = ('id', 'project', 'owner', 'property_url', 'active', 'created_at')
    list_display = ('project', 'property_url', 'owner', 'active', 'created_at')
