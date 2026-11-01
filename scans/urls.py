from django.urls import path
from . import views
app_name = 'scans'
urlpatterns = [
    path("<uuid:batch_id>/documents/<int:index>/download/", views.document_download, name="document-download"),
    path('targets/', views.targets, name='targets'),
    path('targets/create/', views.target_create, name='target-create'),
    path('targets/<uuid:target_id>/deactivate/', views.target_deactivate, name='target-deactivate'),
    path('targets/<uuid:target_id>/start/', views.start, name='start'),
    path('<uuid:batch_id>/cancel/', views.cancel, name='cancel'),
]
