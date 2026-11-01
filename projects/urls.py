from django.urls import include, path
from . import views

app_name = "projects"
urlpatterns = [
    path("<uuid:project_id>/scans/", include("scans.urls")),
    path("<uuid:project_id>/connections/", include("integrations.project_urls")),
    path("<uuid:project_id>/sources/", include("sources.urls")),
    path("<uuid:project_id>/members/<int:user_id>/remove/", views.membership_remove, name="membership-remove"),
    path("", views.project_list, name="list"),
    path("<uuid:project_id>/", views.project_detail, name="detail"),
    path("<uuid:project_id>/members/", views.membership_set, name="membership-set"),
]
