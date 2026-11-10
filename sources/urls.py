from django.urls import path
from . import views
app_name = "sources"
urlpatterns = [
    path("<uuid:batch_id>/records/<int:record_id>/documents/<str:variant>/", views.crawl_document, name="crawl_document"),
    path("<uuid:batch_id>/records/", views.records, name="records"),
    path("<uuid:batch_id>/retry/", views.retry, name="retry"),
    path("", views.overview, name="overview"),
    path("<uuid:batch_id>/remove/", views.remove, name="remove"),
]
