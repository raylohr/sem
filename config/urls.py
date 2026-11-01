from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import include, path
from django.views.generic import RedirectView
from core.views import health, ready

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="ui:dashboard", permanent=False)),
    path("workspace/", include("core.ui_urls")),
    path("accounts/login/", LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("health/", health, name="health"),
    path("ready/", ready, name="ready"),
    path("integrations/", include("integrations.urls")),
    path("projects/", include("projects.urls")),
    path("admin/", admin.site.urls),
]
