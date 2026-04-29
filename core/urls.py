from django.urls import path
from core import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('transactions/', views.transaction_list, name='transaction_list'),
    path('accounts/', views.accounts_list, name='accounts_list'),
    path('accounts/new/', views.account_create, name='account_create'),
    path('accounts/<str:account_id>/edit/', views.account_edit, name='account_edit'),
    path('accounts/<str:account_id>/delete/', views.account_delete, name='account_delete'),
    path('accounts/<str:account_id>/', views.account_detail, name='account_detail'),
]
