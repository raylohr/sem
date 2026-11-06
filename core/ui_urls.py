from django.urls import path
from . import ui
from sources import import_ui, export_ui
from integrations import settings_views, guide_views
from projects import ui as project_ui
app_name='ui'
urlpatterns=[
    path("projects/<uuid:project_id>/performance/preferences/", ui.save_performance_preferences, name="performance-preferences"),
    path("projects/<uuid:project_id>/performance/", ui.performance, name="performance"),
    path("projects/<uuid:project_id>/urls/detail/", ui.url_detail, name="url-detail"),
    path("projects/<uuid:project_id>/urls/export/progress/", export_ui.export_progress, name="export-progress"),
    path("projects/<uuid:project_id>/urls/export/", export_ui.export_urls, name="export-urls"),
    path("projects/<uuid:project_id>/import/", import_ui.upload, name="import"),
    path("projects/<uuid:project_id>/import/<uuid:batch_id>/", import_ui.preview, name="import-preview"),
    path("handleiding/gsc/",guide_views.work_instructions,name="gsc-guide"),
    path("projects/<uuid:project_id>/connections/handleiding/",guide_views.work_instructions,name="gsc-project-guide"),
    path("settings/google/",settings_views.oauth_settings,name="gsc-settings"),
    path("settings/google/handleiding/",settings_views.setup_guide,name="gsc-setup-guide"),
    path("projects/create/",project_ui.create,name="project_create"),
    path("projects/<uuid:project_id>/edit/",project_ui.edit,name="project_edit"),
    path("projects/<uuid:project_id>/delete/",project_ui.remove,name="project_delete"),
    path("projects/<uuid:project_id>/sources/<uuid:batch_id>/records/<int:record_id>/",ui.source_record,name="record"),
    path('',ui.dashboard,name='dashboard'),
    path('projects/<uuid:project_id>/',ui.project_home,name='project'),
    path('projects/<uuid:project_id>/sources/',ui.sources,name='sources'),
    path('projects/<uuid:project_id>/sources/<uuid:batch_id>/',ui.batch,name='batch'),
    path('projects/<uuid:project_id>/connections/',ui.connections,name='connections'),
]
