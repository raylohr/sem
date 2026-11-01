from django.urls import path
from . import ui
app_name='ui'
urlpatterns=[
    path('',ui.dashboard,name='dashboard'),
    path('projects/<uuid:project_id>/',ui.project_home,name='project'),
    path('projects/<uuid:project_id>/sources/',ui.sources,name='sources'),
    path('projects/<uuid:project_id>/sources/<uuid:batch_id>/',ui.batch,name='batch'),
    path('projects/<uuid:project_id>/scans/',ui.scans,name='scans'),
    path('projects/<uuid:project_id>/connections/',ui.connections,name='connections'),
]
