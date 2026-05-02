from django.urls import path, include
from . import views

urlpatterns = [
    path('transactions/export/csv/', views.transaction_export_csv, name='transaction_export_csv'),
    path('transactions/export/excel/', views.transaction_export_excel, name='transaction_export_excel'),
    path('portfolio/chart/', views.portfolio_chart, name='portfolio_chart'),
    path('portfolio/chart/data/', views.portfolio_chart_data, name='portfolio_chart_data'),
]