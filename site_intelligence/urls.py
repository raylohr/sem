from django.urls import path
from . import views
app_name='intelligence'
urlpatterns=[
    path('',views.overview,name='overview'),
    path('start/<uuid:batch_id>/',views.start,name='start'),
    path('runs/<int:run_id>/',views.run_detail,name='run'),
    path('runs/<int:run_id>/retry/',views.retry,name='retry'),
    path('runs/<int:run_id>/export/',views.export,name='export'),
    path('detections/<int:detection_id>/',views.detection_detail,name='detection'),
    path('registry/',views.registry,name='registry'),
    path('registry/<int:supplier_id>/',views.supplier_edit,name='supplier'),
    path('declarations/new/',views.declaration,name='declaration'),
    path('policies/<int:policy_id>/',views.policy_download,name='policy'),
]
