from django.urls import path
from . import views
app_name = "sources"
urlpatterns = [
    path("upload/", views.upload, name="upload"),
    path("<uuid:batch_id>/download/", views.download, name="download"),
    path("<uuid:batch_id>/records/", views.records, name="records"),
    path("<uuid:batch_id>/retry/", views.retry, name="retry"),
    path("", views.overview, name="overview"),
    path("create/", views.create, name="create"),
    path("<uuid:batch_id>/remove/", views.remove, name="remove"),
]
