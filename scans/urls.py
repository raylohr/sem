from django.urls import path
from . import views
from . import retry_views,resource_views
app_name = 'scans'
urlpatterns = [
    path('<uuid:batch_id>/resources/',resource_views.inventory,name='resources'),
    path('<uuid:batch_id>/resources/<uuid:resource_id>/',resource_views.detail,name='resource'),
    path('<uuid:batch_id>/retry-downloads/',retry_views.retry_downloads,name='retry-downloads'),
    path('<uuid:batch_id>/coverage/',retry_views.coverage,name='coverage'),
    path('<uuid:batch_id>/attempts/',retry_views.attempts,name='attempts'),
    path('<uuid:batch_id>/progress/', views.progress, name='progress'),
    path("<uuid:batch_id>/documents/<int:index>/download/", views.document_download, name="document-download"),
    path('targets/', views.targets, name='targets'),
    path('targets/create/', views.target_create, name='target-create'),
    path('targets/<uuid:target_id>/update/', views.target_update, name='target-update'),
    path('hosts/<int:policy_id>/decision/', views.host_decision, name='host-decision'),
    path('targets/<uuid:target_id>/deactivate/', views.target_deactivate, name='target-deactivate'),
    path('targets/<uuid:target_id>/start/', views.start, name='start'),
    path('<uuid:batch_id>/cancel/', views.cancel, name='cancel'),
]
