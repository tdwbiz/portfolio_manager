from django.urls import path
from django.contrib.auth import views as auth_views
from imports import views as import_views

# Authentication views
from .views_auth import login, logout

# Account views
from .views_accounts import accounts_list, account_detail, account_create, account_edit, account_delete

# Portfolio views
from .views_portfolio import dashboard, portfolio_summary, realised_pnl, portfolio_chart, portfolio_chart_data

# Transaction views
from .views_transactions import transaction_list, transaction_export_csv, transaction_export_excel

# Report views
from .views_reports import income_summary, tax_summary, import_history, download_rejection_log, annual_reports_view

urlpatterns = [
    path('', dashboard, name='dashboard'),
    path('login/', login, name='login'),
    path('logout/', logout, name='logout'),
    path('dashboard/', dashboard, name='dashboard'),
    path('transactions/', transaction_list, name='transaction_list'),
    path('transactions/export/csv/', transaction_export_csv, name='transaction_export_csv'),
    path('transactions/export/excel/', transaction_export_excel, name='transaction_export_excel'),
    path('portfolio/', portfolio_summary, name='portfolio_summary'),
    path('portfolio/chart/', portfolio_chart, name='portfolio_chart'),
    path('portfolio/chart/data/', portfolio_chart_data, name='portfolio_chart_data'),
    path('realised-pnl/', realised_pnl, name='realised_pnl'),
    path('income/', income_summary, name='income_summary'),
    path('reports/tax-summary/', tax_summary, name='tax_summary'),
    path('imports/', import_history, name='import_history'),
    path('imports/rejection-log/<str:month>/', download_rejection_log, name='download_rejection_log'),
    path('accounts/', accounts_list, name='accounts_list'),
    path('accounts/new/', account_create, name='account_create'),
    path('accounts/<str:account_id>/', account_detail, name='account_detail'),
    path('accounts/<str:account_id>/edit/', account_edit, name='account_edit'),
    path('accounts/<str:account_id>/delete/', account_delete, name='account_delete'),
    path('pdf-import/', import_views.pdf_import_view, name='pdf_import'),
    path('annual-report-import/', import_views.annual_report_import_view, name='annual_report_import'),
    path('annual-reports/', annual_reports_view, name='annual_reports'),
]
