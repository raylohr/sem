from django.urls import include,path
from . import ui
from integrations import settings_views, guide_views
from projects import ui as project_ui
app_name='ui'
urlpatterns=[
    path("handleiding/gsc/",guide_views.work_instructions,name="gsc-guide"),
    path("projects/<uuid:project_id>/connections/handleiding/",guide_views.work_instructions,name="gsc-project-guide"),
    path("settings/google/",settings_views.oauth_settings,name="gsc-settings"),
    path("settings/google/handleiding/",settings_views.setup_guide,name="gsc-setup-guide"),
    path("projects/create/",project_ui.create,name="project_create"),
    path("projects/<uuid:project_id>/edit/",project_ui.edit,name="project_edit"),
    path("projects/<uuid:project_id>/delete/",project_ui.remove,name="project_delete"),
    path("projects/<uuid:project_id>/sources/<uuid:batch_id>/records/<int:record_id>/",ui.source_record,name="record"),
    path("projects/<uuid:project_id>/intelligence/",include("site_intelligence.urls")),
    path("projects/<uuid:project_id>/browser/",include("browser_scans.urls")),
    path("projects/<uuid:project_id>/assessments/",include("assessments.urls")),
    path("projects/<uuid:project_id>/urls/",include("catalog.ui_urls")),
    path('',ui.dashboard,name='dashboard'),
    path('projects/<uuid:project_id>/',ui.project_home,name='project'),
    path('projects/<uuid:project_id>/sources/',ui.sources,name='sources'),
    path('projects/<uuid:project_id>/sources/<uuid:batch_id>/',ui.batch,name='batch'),
    path('projects/<uuid:project_id>/scans/',ui.scans,name='scans'),
    path('projects/<uuid:project_id>/connections/',ui.connections,name='connections'),
]
