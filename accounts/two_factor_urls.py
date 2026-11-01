from django.urls import path
from .auth_views import SecurityLoginView,SecuritySetupView,SecurityQRView,SecuritySetupCompleteView,SecurityBackupTokensView,SecurityProfileView,disable_mfa,replace_mfa
urlpatterns=[
 path('accounts/two-factor/replace/',replace_mfa,name='replace'),
 path('accounts/login/',SecurityLoginView.as_view(),name='login'),
 path('accounts/two-factor/setup/',SecuritySetupView.as_view(),name='setup'),
 path('accounts/two-factor/qr/',SecurityQRView.as_view(),name='qr'),
 path('accounts/two-factor/complete/',SecuritySetupCompleteView.as_view(),name='setup_complete'),
 path('accounts/two-factor/backup/',SecurityBackupTokensView.as_view(),name='backup_tokens'),
 path('accounts/security/',SecurityProfileView.as_view(),name='profile'),
 path('accounts/two-factor/disable/',disable_mfa,name='disable'),
]
