from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse, FileResponse, HttpResponseNotAllowed
from django.conf import settings
import csv
import re
from pathlib import Path
from django.utils import timezone
from datetime import datetime
from decimal import Decimal
import openpyxl
from io import BytesIO

from core.models import Transaction, Account, BrokerStatement, FileImportLog
from core.utils import compute_portfolio_value_series, compute_realised_pnl
from core.forms import AccountForm

@login_required
def dashboard(request):
    # Dashboard logic here
    return render(request, 'core/dashboard.html')

@login_required
def transaction_list(request):
    # Transaction list logic here
    return render(request, 'core/transaction_list.html')

@login_required
def accounts_list(request):
    accounts = Account.objects.all()
    return render(request, 'core/accounts.html', {'accounts': accounts})

@login_required
def account_detail(request, account_id):
    # Account detail logic here
    return render(request, 'core/account_detail.html')

@login_required
def account_create(request):
    if request.method == 'POST':
        form = AccountForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('accounts_list')
    else:
        form = AccountForm()
    
    return render(request, 'core/account_form.html', {
        'form': form,
        'title': 'Add Account',
    })

@login_required
def account_edit(request, account_id):
    account = get_object_or_404(Account, account_id=account_id)
    
    if request.method == 'POST':
        form = AccountForm(request.POST, instance=account)
        form.fields['account_id'].disabled = True
        if form.is_valid():
            form.save()
            return redirect('account_detail', account_id=account.account_id)
    else:
        form = AccountForm(instance=account)
        form.fields['account_id'].disabled = True
    
    return render(request, 'core/account_form.html', {
        'form': form,
        'title': 'Edit Account',
    })

@login_required
def account_delete(request, account_id):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    
    account = get_object_or_404(Account, account_id=account_id)
    account.delete()
    return redirect('accounts_list')

@login_required
def portfolio_summary(request):
    # Portfolio summary logic here
    return render(request, 'core/portfolio_summary.html')

@login_required
def realised_pnl(request):
    """Realised P&L page with FIFO and average-cost methods."""
    account = request.GET.get('account', '')
    
    trades = compute_realised_pnl(account_id=account if account else None)
    
    # Compute totals
    total_pnl_fifo = sum(t['realised_pnl_fifo'] for t in trades)
    total_pnl_avg = sum(t['realised_pnl_avg'] for t in trades)
    
    # Get all accounts for filter dropdown
    accounts = Account.objects.all().order_by('account_id')
    
    return render(request, 'core/realised_pnl.html', {
        'trades': trades,
        'total_pnl_fifo': total_pnl_fifo,
        'total_pnl_avg': total_pnl_avg,
        'accounts': accounts,
        'account_filter': account,
    })

@login_required
def income_summary(request):
    """Income summary grouped by year, symbol, and type."""
    year_filter = request.GET.get('year', '')
    type_filter = request.GET.get('type', '')
    
    # Income and tax types
    income_types = ['DIV', 'DIST', 'INT']
    tax_types = ['WITHTAX', 'TAX']
    all_types = income_types + tax_types
    
    # Query transactions
    qs = Transaction.objects.filter(type__in=all_types)
    
    if year_filter:
        qs = qs.filter(date__year=int(year_filter))
    if type_filter:
        qs = qs.filter(type=type_filter)
    
    # Group by (year, symbol, type) in Python
    from collections import defaultdict
    groups = defaultdict(lambda: {'total_amount_cad': Decimal('0'), 'count': 0})
    
    for tx in qs.select_related('account'):
        year = tx.date.year
        key = (year, tx.symbol, tx.type)
        groups[key]['total_amount_cad'] += tx.amount_cad
        groups[key]['count'] += 1
    
    # Build sorted rows
    rows = []
    for (year, symbol, tx_type), data in groups.items():
        rows.append({
            'year': year,
            'symbol': symbol,
            'type': tx_type,
            'count': data['count'],
            'total_amount_cad': data['total_amount_cad'],
        })
    
    rows.sort(key=lambda x: (x['year'], x['symbol'], x['type']), reverse=True)
    
    # Compute totals
    total_income_cad = sum(r['total_amount_cad'] for r in rows if r['type'] in income_types)
    total_tax_cad = sum(r['total_amount_cad'] for r in rows if r['type'] in tax_types)
    net_income_cad = total_income_cad + total_tax_cad
    
    # Get distinct years for filter
    years = sorted(set(r['year'] for r in rows), reverse=True)
    
    return render(request, 'core/income_summary.html', {
        'rows': rows,
        'total_income_cad': total_income_cad,
        'total_tax_cad': total_tax_cad,
        'net_income_cad': net_income_cad,
        'years': years,
        'income_types': all_types,
        'year_filter': year_filter,
        'type_filter': type_filter,
    })

@login_required
def import_history(request):
    """Import history with links to rejection log downloads."""
    logs = FileImportLog.objects.all().order_by('-imported_at')
    
    # Enrich with rejection log availability
    entries = []
    for log in logs:
        year_month = log.imported_at.strftime('%Y_%m')
        log_path = Path(settings.IMPORT_LOGS_DIR) / f'rejected_{year_month}.csv'
        
        entries.append({
            'log': log,
            'rejection_month': year_month,
            'has_rejection_log': log_path.exists(),
        })
    
    return render(request, 'core/import_history.html', {
        'entries': entries,
    })

@login_required
def download_rejection_log(request, month):
    """Serve rejection log CSV file for download."""
    # Validate month parameter
    if not re.match(r'^\d{4}_\d{2}$', month):
        return HttpResponse('Invalid month format', status=400)
    
    # Construct file path
    log_path = Path(settings.IMPORT_LOGS_DIR) / f'rejected_{month}.csv'
    
    if not log_path.exists():
        return HttpResponse('File not found', status=404)
    
    # Serve file
    response = FileResponse(open(log_path, 'rb'), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="rejected_{month}.csv"'
    
    return response

@login_required
def portfolio_chart(request):
    """Render portfolio performance chart template."""
    return render(request, 'core/portfolio_chart.html')

@login_required
def portfolio_chart_data(request):
    """JSON endpoint for portfolio chart data."""
    try:
        series = compute_portfolio_value_series()
        
        # Format for Chart.js
        labels = [item['date'] for item in series]
        values = [float(item['market_value_cad']) for item in series]
        
        return JsonResponse({'labels': labels, 'values': values})
    except Exception:
        return JsonResponse({'labels': [], 'values': []}, status=500)

def _apply_transaction_filters(qs, request):
    """Apply transaction list filters - shared by list and export views."""
    broker = request.GET.get('broker', '')
    symbol = request.GET.get('symbol', '')
    tx_type = request.GET.get('type', '')
    account_id = request.GET.get('account', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    
    if broker:
        qs = qs.filter(statement__source=broker)
    if symbol:
        qs = qs.filter(symbol=symbol)
    if tx_type:
        qs = qs.filter(type=tx_type)
    if account_id:
        qs = qs.filter(account__account_id=account_id)
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    
    filters = {
        'broker': broker,
        'symbol': symbol,
        'type': tx_type,
        'account': account_id,
        'date_from': date_from,
        'date_to': date_to,
    }
    
    return qs, filters


@login_required
def transaction_export_csv(request):
    qs = Transaction.objects.select_related('statement', 'account').all()
    qs, filters = _apply_transaction_filters(qs, request)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename=transactions_{datetime.now().strftime("%Y-%m-%d")}.csv'

    writer = csv.writer(response)
    writer.writerow([
        'Date', 'Symbol', 'Type', 'Quantity', 'Price', 'Amount', 'Currency',
        'Amount (CAD)', 'Exchange Rate', 'Account', 'Broker', 'Notes'
    ])

    for tx in qs:
        writer.writerow([
            tx.date.strftime('%Y-%m-%d'), tx.symbol, tx.type, tx.quantity,
            tx.price, tx.amount, tx.currency, tx.amount_cad,
            tx.exchange_rate_cad, tx.account.account_id if tx.account else '',
            tx.statement.source, tx.notes
        ])

    return response

@login_required
def transaction_export_excel(request):
    qs = Transaction.objects.select_related('statement', 'account').all()
    qs, filters = _apply_transaction_filters(qs, request)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Transactions'

    headers = [
        'Date', 'Symbol', 'Type', 'Quantity', 'Price', 'Amount', 'Currency',
        'Amount (CAD)', 'Exchange Rate', 'Account', 'Broker', 'Notes'
    ]
    ws.append(headers)

    for tx in qs:
        ws.append([
            tx.date.strftime('%Y-%m-%d'), tx.symbol, tx.type, tx.quantity,
            tx.price, tx.amount, tx.currency, tx.amount_cad,
            tx.exchange_rate_cad, tx.account.account_id if tx.account else '',
            tx.statement.source, tx.notes
        ])

    for cell in ws[1]:
        cell.font = openpyxl.styles.Font(bold=True)

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename=transactions_{datetime.now().strftime("%Y-%m-%d")}.xlsx'

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    response.write(buffer.getvalue())
    
    return response

def fetch_last_prices(request):
    # Implementation for fetching last prices
    return JsonResponse({})