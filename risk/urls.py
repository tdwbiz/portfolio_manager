from django.urls import path
from risk import views

app_name = 'risk'

urlpatterns = [
    path('dashboard/',                     views.dashboard,         name='dashboard'),
    path('account/<str:account_id>/',      views.account_summary,   name='account_summary'),
    path('owner/<str:owner>/',             views.owner_summary,     name='owner_summary'),
    path('heatmap/',                       views.risk_heatmap,      name='heatmap'),
]
