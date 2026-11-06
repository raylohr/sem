from django.urls import path
from . import views
app_name='reports'
urlpatterns=[
    path('proposals/<int:proposal_id>/',views.proposal_detail,name='proposal'),
    path('proposals/<int:proposal_id>/approve/',views.proposal_approve,name='proposal-approve'),
    path('proposals/<int:proposal_id>/supplement/',views.proposal_supplement,name='proposal-supplement'),
    path('proposals/<int:proposal_id>/dismiss/',views.proposal_dismiss,name='proposal-dismiss'),
    path('',views.overview,name='overview'),path('start/',views.start,name='start'),
    path('dossiers/<uuid:dossier_id>/',views.detail,name='dossier'),
    path('dossiers/<uuid:dossier_id>/references/',views.references,name='references'),
    path('dossiers/<uuid:dossier_id>/categories/<str:category_key>/',views.category_detail,name='category'),
    path('dossiers/<uuid:dossier_id>/retry/',views.retry,name='retry'),
    path('dossiers/<uuid:dossier_id>/prospect/',views.prospect,name='prospect'),
    path('dossiers/<uuid:dossier_id>/export/',views.export,name='export'),
    path('problems/<int:group_id>/',views.group_detail,name='group'),
    path('problems/<int:group_id>/link/',views.group_link,name='group-link'),
    path('actions/',views.action_list,name='actions'),path('actions/create/',views.action_create,name='action-create'),
    path('actions/<uuid:action_id>/',views.action_detail,name='action'),
    path('actions/<uuid:action_id>/edit/',views.action_edit,name='action-edit'),
    path('actions/<uuid:action_id>/verify/',views.verify,name='verify'),
]
