from django.urls import path
from . import views
app_name = 'assessments'
urlpatterns = [
    path('', views.overview, name='overview'),
    path('reprocess/', views.reprocess, name='reprocess-selection'),
    path('pages/<int:page_id>/', views.page_detail, name='page'),
    path('batches/<uuid:batch_id>/reprocess/', views.reprocess, name='reprocess'),
    path('runs/<uuid:run_id>/', views.run_detail, name='run'),
    path('results/<int:result_id>/', views.result_detail, name='result'),
    path('findings/<int:finding_id>/', views.finding_detail, name='finding'),
    path('runs/<uuid:run_id>/export/', views.export, name='export'),
]
