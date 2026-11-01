from django.urls import path
from . import views
app_name = "connections"
urlpatterns = [
    path("gsc/", views.connections, name="gsc-list"),
    path("gsc/connect/", views.connect, name="gsc-connect"),
    path("gsc/<uuid:connection_id>/disconnect/", views.disconnect, name="gsc-disconnect"),
    path("gsc/<uuid:connection_id>/import/", views.import_data, name="gsc-import"),
]
