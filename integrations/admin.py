from django.contrib import admin
from sources.admin import ReadOnlyPlatformAdmin
from .models import GSCConnection, SERankingConnection, SERankingRefresh, PlaywrightCrawlSettings, PlaywrightCrawl, PlaywrightCrawlURL


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


@admin.register(PlaywrightCrawlSettings)
class PlaywrightCrawlSettingsAdmin(ReadOnlyPlatformAdmin):
    list_display = ('project', 'respect_robots', 'use_sitemaps', 'parallel_agents', 'pages_per_second', 'updated_at')


@admin.register(PlaywrightCrawl)
class PlaywrightCrawlAdmin(ReadOnlyPlatformAdmin):
    list_display = ('id','project','status','profile','started_at','finished_at')
    fields = ('id','project','created_by','status','domain','profile','started_at','finished_at','current_agents','limit_reached','error','notes')


@admin.register(PlaywrightCrawlURL)
class PlaywrightCrawlURLAdmin(ReadOnlyPlatformAdmin):
    list_display = ('crawl','url','kind','status','attempts')
    fields = ('crawl','url','kind','status','depth','discovered_from','provenance','attempts','metadata','error')
