from django.urls import path
from . import views
app_name = "connections"
urlpatterns = [
    path("gsc/<uuid:connection_id>/property/", views.select_property, name="gsc-property"),
    path("gsc/<uuid:connection_id>/schedule/", views.schedule, name="gsc-schedule"),
    path("gsc/<uuid:connection_id>/inspect/", views.inspect_urls, name="gsc-inspect"),
    path("gsc/", views.connections, name="gsc-list"),
    path("gsc/connect/", views.connect, name="gsc-connect"),
    path("gsc/<uuid:connection_id>/disconnect/", views.disconnect, name="gsc-disconnect"),
    path("gsc/<uuid:connection_id>/import/", views.import_data, name="gsc-import"),
]
