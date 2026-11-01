from django.urls import path
from . import views
app_name='catalog'
urlpatterns=[path('',views.overview,name='overview'),path('<uuid:url_id>/',views.detail,name='detail')]
