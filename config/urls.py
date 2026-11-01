from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.views.generic import TemplateView
from accounts.auth_views import SecurityLoginView,AccountPasswordChangeView,AccountPasswordResetView,AccountPasswordResetConfirmView
from accounts.two_factor_urls import urlpatterns as mfa_urls
from django.urls import include, path
from django.views.generic import RedirectView
from core.views import health, ready

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="ui:dashboard", permanent=False)),
    path("workspace/", include("core.ui_urls")),
    path("accounts/login/", SecurityLoginView.as_view(), name="login"),
    path("",include((mfa_urls,"two_factor"))),
    path("",include("accounts.urls")),
    path("accounts/password/change/",AccountPasswordChangeView.as_view(),name="password_change"),
    path("accounts/password/reset/",AccountPasswordResetView.as_view(),name="password_reset"),
    path("accounts/password/reset/done/",TemplateView.as_view(template_name="accounts/password_reset_done.html"),name="password_reset_done"),
    path("accounts/reset/<uidb64>/<token>/",AccountPasswordResetConfirmView.as_view(),name="password_reset_confirm"),
    path("accounts/password/reset/complete/",TemplateView.as_view(template_name="accounts/password_reset_complete.html"),name="password_reset_complete"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("health/", health, name="health"),
    path("ready/", ready, name="ready"),
    path("integrations/", include("integrations.urls")),
    path("projects/", include("projects.urls")),
    path("admin/", admin.site.urls),
]
