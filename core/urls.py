from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', views.login, name='login'),
    path('logout/', views.logout, name='logout'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('transactions/', views.transaction_list, name='transaction_list'),
    path('transactions/export/csv/', views.transaction_export_csv, name='transaction_export_csv'),
    path('transactions/export/excel/', views.transaction_export_excel, name='transaction_export_excel'),
    path('portfolio/', views.portfolio_summary, name='portfolio_summary'),
    path('portfolio/chart/', views.portfolio_chart, name='portfolio_chart'),
    path('portfolio/chart/data/', views.portfolio_chart_data, name='portfolio_chart_data'),
    path('realised-pnl/', views.realised_pnl, name='realised_pnl'),
    path('income/', views.income_summary, name='income_summary'),
    path('imports/', views.import_history, name='import_history'),
    path('imports/rejection-log/<str:month>/', views.download_rejection_log, name='download_rejection_log'),
    path('accounts/', views.accounts_list, name='accounts_list'),
    path('accounts/new/', views.account_create, name='account_create'),
    path('accounts/<str:account_id>/', views.account_detail, name='account_detail'),
    path('accounts/<str:account_id>/edit/', views.account_edit, name='account_edit'),
    path('accounts/<str:account_id>/delete/', views.account_delete, name='account_delete'),
]
