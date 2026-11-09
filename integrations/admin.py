from django.contrib import admin
from sources.admin import ReadOnlyPlatformAdmin
from .models import GSCConnection, SERankingConnection


@admin.register(GSCConnection)
class ConnectionAdmin(ReadOnlyPlatformAdmin):
    fields = ('id', 'project', 'owner', 'property_url', 'active', 'created_at')
    list_display = ('project', 'property_url', 'owner', 'active', 'created_at')


@admin.register(SERankingConnection)
class SERankingConnectionAdmin(ReadOnlyPlatformAdmin):
    fields = ('id', 'project', 'owner', 'site_id', 'site_name', 'active', 'created_at')
    list_display = ('project', 'site_name', 'site_id', 'owner', 'active')
