from django.contrib.auth.decorators import login_required
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.models import User
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse, FileResponse, HttpResponseNotAllowed
from django.conf import settings
from django.urls import reverse
from django.db.models import Sum, Count
import csv
import re
from pathlib import Path
from django.utils import timezone
from datetime import datetime
from decimal import Decimal
import openpyxl
from io import BytesIO

from core.models import Transaction, Account, BrokerStatement, FileImportLog
from core.utils import (
    compute_portfolio_value_series,
    compute_realised_pnl,
    compute_positions,
    fetch_last_prices,
    enrich_positions_with_prices,
)
from core.forms import AccountForm


def login(request):
    """Login view - handles both GET and POST."""
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = User.objects.filter(username=username).first()
        if user and user.check_password(password):
            auth_login(request, user)
            next_url = request.POST.get('next', '/')
            return redirect(next_url)
        else:
            return render(request, 'core/login.html', {
                'error': 'Invalid username or password.',
            })
    else:
        return render(request, 'core/login.html')


def logout(request):
    """Logout view - handles POST redirect."""
    auth_logout(request)
    return redirect('login')


@login_required
def dashboard(request):
    """Dashboard view with summary stats."""
    accounts = Account.objects.all()
    total_accounts = accounts.count()
    total_transactions = Transaction.objects.count()
    total_brokers = BrokerStatement.objects.values_list('source', flat=True).distinct().count()

    # Get recent transactions
    recent_transactions = Transaction.objects.select_related('account', 'statement').order_by('-date')[:10]

    # Get recent imports
    recent_imports = FileImportLog.objects.order_by('-imported_at')[:10]

    return render(request, 'core/dashboard.html', {
        'accounts': accounts,
        'total_accounts': total_accounts,
        'total_transactions': total_transactions,
        'total_brokers': total_brokers,
        'recent_transactions': recent_transactions,
        'recent_imports': recent_imports,
    })


@login_required
def transaction_list(request):
    """Transaction list with filters and pagination."""
    qs = Transaction.objects.select_related('statement', 'account').all()

    # Apply filters
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

    # Get distinct values for filter dropdowns
    brokers = BrokerStatement.objects.values_list('source', flat=True).distinct().order_by('source')
    symbols = Transaction.objects.values_list('symbol', flat=True).distinct().order_by('symbol')
    accounts = Account.objects.all().order_by('account_id')

    # Pagination
    page = request.GET.get('page', 1)
    per_page = 50
    from django.core.paginator import Paginator
    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(page)

    return render(request, 'core/transaction_list.html', {
        'page_obj': page_obj,
        'transactions': page_obj,
        'brokers': brokers,
        'symbols': symbols,
        'accounts': accounts,
        'filters': {
            'broker': broker,
            'symbol': symbol,
            'type': tx_type,
            'account': account_id,
            'date_from': date_from,
            'date_to': date_to,
        },
    })


@login_required
def accounts_list(request):
    accounts = Account.objects.all()
    return render(request, 'core/accounts.html', {'accounts': accounts})


@login_required
def account_detail(request, account_id):
    account = get_object_or_404(Account, account_id=account_id)
    transactions = Transaction.objects.filter(account=account).order_by('-date')

    # Compute balance summary
    total_cash_flow_cad = transactions.aggregate(
        total=Sum('amount_cad')
    )['total'] or Decimal('0')

    ending_balance_cad = account.starting_balance + total_cash_flow_cad

    # Cash flow by type
    cash_by_type = transactions.filter(
        type__in=['BUY', 'SELL', 'DIV', 'DIST', 'INT', 'WITHTAX', 'TAX']
    ).values('type').annotate(
        count=Count('id'),
        total_cad=Sum('amount_cad')
    ).order_by('type')

    # Active holdings
    active_holdings = []
    buy_sell_txs = transactions.filter(type__in=['BUY', 'SELL']).order_by('date')
    holdings = {}
    for tx in buy_sell_txs:
        if tx.symbol not in holdings:
            holdings[tx.symbol] = {'shares': Decimal('0'), 'buy_cost_cad': Decimal('0')}
        if tx.type == 'BUY':
            holdings[tx.symbol]['shares'] += tx.quantity
            holdings[tx.symbol]['buy_cost_cad'] += tx.amount_cad
        elif tx.type == 'SELL':
            holdings[tx.symbol]['shares'] -= tx.quantity

    for symbol, data in holdings.items():
        if data['shares'] > 0:
            active_holdings.append({
                'symbol': symbol,
                'shares': data['shares'],
                'buy_cost_cad': data['buy_cost_cad'],
                'avg_cost': data['buy_cost_cad'] / data['shares'] if data['shares'] else Decimal('0'),
            })

    # Pagination for transactions
    page = request.GET.get('page', 1)
    per_page = 50
    from django.core.paginator import Paginator
    paginator = Paginator(transactions, per_page)
    page_obj = paginator.get_page(page)

    return render(request, 'core/account_detail.html', {
        'account': account,
        'total_cash_flow_cad': total_cash_flow_cad,
        'ending_balance_cad': ending_balance_cad,
        'cash_by_type': cash_by_type,
        'active_holdings': active_holdings,
        'page_obj': page_obj,
    })

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
    """Portfolio summary with live prices and P&L."""
    # Compute positions
    positions = compute_positions()

    # Get unique symbols
    symbols = list(set(pos['symbol'] for pos in positions))

    # Fetch last prices
    prices = fetch_last_prices(symbols)

    # Enrich positions with prices
    enriched_positions = enrich_positions_with_prices(positions, prices)

    # Compute totals
    total_cost_cad = sum(pos['total_cost_cad'] for pos in enriched_positions)
    total_market_value_cad = sum(
        pos['market_value_cad'] for pos in enriched_positions
        if pos['market_value_cad'] is not None
    )
    total_unrealised_pnl_cad = sum(
        pos['unrealised_pnl_cad'] for pos in enriched_positions
        if pos['unrealised_pnl_cad'] is not None
    )

    return render(request, 'core/portfolio_summary.html', {
        'positions': enriched_positions,
        'total_cost_cad': total_cost_cad,
        'total_market_value_cad': total_market_value_cad,
        'total_unrealised_pnl_cad': total_unrealised_pnl_cad,
    })

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