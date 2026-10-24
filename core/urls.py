from django.urls import path

from .views_ledger import ledger_month_view

from .views import (
    gap_analysis,
    generate_annual_reports_view,
    generate_monthly_statements_view,
    health_check,
    reconcile_view,
    reverse_statements_view,
)

# Account views
from .views_accounts import (
    account_create,
    account_delete,
    account_detail,
    account_edit,
    accounts_list,
)

# Authentication views
from .views_auth import login, logout

# Portfolio views
from .views_portfolio import (
    dashboard,
    portfolio_chart,
    portfolio_chart_data,
    portfolio_summary,
    realised_pnl,
)

# Report views
from .views_reports import (
    annual_reports_view,
    download_rejection_log,
    import_history,
    income_summary,
    tax_summary,
)

# Transaction views
from .views_transactions import (
    transaction_export_csv,
    transaction_export_excel,
    transaction_list,
)

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("login/", login, name="login"),
    path("logout/", logout, name="logout"),
    path("dashboard/", dashboard, name="dashboard"),
    path("transactions/", transaction_list, name="transaction_list"),
    path(
        "transactions/export/csv/",
        transaction_export_csv,
        name="transaction_export_csv",
    ),
    path(
        "transactions/export/excel/",
        transaction_export_excel,
        name="transaction_export_excel",
    ),
    path("portfolio/", portfolio_summary, name="portfolio_summary"),
    path("portfolio/chart/", portfolio_chart, name="portfolio_chart"),
    path("portfolio/chart/data/", portfolio_chart_data, name="portfolio_chart_data"),
    path("realised-pnl/", realised_pnl, name="realised_pnl"),
    path("income/", income_summary, name="income_summary"),
    path("reports/tax-summary/", tax_summary, name="tax_summary"),
    path("imports/", import_history, name="import_history"),
    path(
        "imports/rejection-log/<str:month>/",
        download_rejection_log,
        name="download_rejection_log",
    ),
    path("accounts/", accounts_list, name="accounts_list"),
    path("accounts/gap-analysis/", gap_analysis, name="gap_analysis"),
    path(
        "accounts/generate-monthly-statements/",
        generate_monthly_statements_view,
        name="generate_monthly_statements",
    ),
    path(
        "accounts/generate-annual-reports/",
        generate_annual_reports_view,
        name="generate_annual_reports",
    ),
    path(
        "accounts/reconcile/",
        reconcile_view,
        name="reconcile",
    ),
    path(
        "accounts/reverse-statements/",
        reverse_statements_view,
        name="reverse_statements",
    ),
    path("accounts/new/", account_create, name="account_create"),
    path("accounts/<str:account_id>/", account_detail, name="account_detail"),
    path("accounts/<str:account_id>/edit/", account_edit, name="account_edit"),
    path("accounts/<str:account_id>/delete/", account_delete, name="account_delete"),
    path("annual-reports/", annual_reports_view, name="annual_reports"),
    path("health/", health_check, name="health_check"),
    path("ledger/<str:account_id>/<int:year>/<int:month>/", ledger_month_view, name="ledger_month"),
]
