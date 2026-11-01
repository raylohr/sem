from django.urls import path
from . import views,auth_views
app_name='accounts'
urlpatterns=[
 path('workspace/users/',views.users,name='users'),
 path('workspace/users/create/',views.user_create,name='user_create'),
 path('workspace/users/audit/',views.audit,name='audit'),
 path('workspace/users/<int:user_id>/',views.user_edit,name='user_edit'),
 path('workspace/users/<int:user_id>/password/',views.user_password,name='user_password'),
 path('workspace/users/<int:user_id>/mfa-reset/',views.user_mfa_reset,name='user_mfa_reset'),
 path('workspace/projects/<uuid:project_id>/access/',views.project_access,name='project_access'),
 path('workspace/projects/<uuid:project_id>/access/<int:user_id>/remove/',views.project_access_remove,name='project_access_remove'),
 path('accounts/confirm-password/',auth_views.confirm_password,name='confirm_password'),
]
