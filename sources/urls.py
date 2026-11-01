from django.urls import path
from . import views
app_name = "sources"
urlpatterns = [
    path("", views.overview, name="overview"),
    path("create/", views.create, name="create"),
    path("<uuid:batch_id>/remove/", views.remove, name="remove"),
]
