from django.contrib import admin
from sources.admin import ReadOnlyPlatformAdmin
from .models import GSCConnection, SERankingConnection, SERankingRefresh


@admin.register(GSCConnection)
class ConnectionAdmin(ReadOnlyPlatformAdmin):
    fields = ('id', 'project', 'owner', 'property_url', 'active', 'created_at')
    list_display = ('project', 'property_url', 'owner', 'active', 'created_at')


@admin.register(SERankingConnection)
class SERankingConnectionAdmin(ReadOnlyPlatformAdmin):
    fields = ('id', 'project', 'owner', 'site_id', 'site_name', 'active', 'daily_enabled', 'created_at')
    list_display = ('project', 'site_name', 'site_id', 'owner', 'active', 'daily_enabled')


@admin.register(SERankingRefresh)
class SERankingRefreshAdmin(ReadOnlyPlatformAdmin):
    fields = ('id', 'connection', 'check_date', 'status', 'started_at', 'finished_at', 'results')
    list_display = ('connection', 'check_date', 'status', 'finished_at')
