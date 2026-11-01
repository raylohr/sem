from django.urls import path
from . import views
app_name='urls'
urlpatterns=[path('',views.api_overview,name='list'),path('<uuid:url_id>/',views.api_detail,name='detail')]
