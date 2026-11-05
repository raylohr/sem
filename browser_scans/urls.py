from django.urls import path
from . import views,followups
app_name = 'browser'
urlpatterns = [
    path('runs/<uuid:run_id>/export/',views.export,name='export'),
    path('runs/<uuid:run_id>/html-followup/',followups.start,name='html-followup'),
    path('', views.overview, name='overview'),
    path('start/<uuid:batch_id>/', views.start, name='start'),
    path('runs/<uuid:run_id>/', views.run_detail, name='run'),
    path('visits/<int:visit_id>/', views.visit_detail, name='visit'),
    path('artifacts/<int:artifact_id>/', views.artifact, name='artifact'),
    path('runs/<uuid:run_id>/retry/', views.retry, name='retry'),
]
