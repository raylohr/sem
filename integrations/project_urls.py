from django.urls import path
from . import views, seranking_views
app_name = "connections"
urlpatterns = [
    path("seranking/discover/", seranking_views.discover, name="seranking-discover"),
    path("seranking/connect/", seranking_views.connect, name="seranking-connect"),
    path("seranking/<uuid:connection_id>/disconnect/", seranking_views.disconnect, name="seranking-disconnect"),
    path("seranking/<uuid:connection_id>/import/", seranking_views.import_data, name="seranking-import"),
    path("seranking/<uuid:connection_id>/audits/", seranking_views.audits, name="seranking-audits"),
    path("gsc/<uuid:connection_id>/property/", views.select_property, name="gsc-property"),
    path("gsc/<uuid:connection_id>/schedule/", views.schedule, name="gsc-schedule"),
    path("gsc/<uuid:connection_id>/inspect/", views.inspect_urls, name="gsc-inspect"),
    path("gsc/", views.connections, name="gsc-list"),
    path("gsc/connect/", views.connect, name="gsc-connect"),
    path("gsc/<uuid:connection_id>/disconnect/", views.disconnect, name="gsc-disconnect"),
    path("gsc/<uuid:connection_id>/import/", views.import_data, name="gsc-import"),
]
