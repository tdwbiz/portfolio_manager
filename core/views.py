from django.contrib.auth.decorators import login_required
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.models import User
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse, FileResponse, HttpResponseNotAllowed
from django.conf import settings
from django.urls import reverse
from django.db.models import Sum, Count, Q as dj_Q
from collections import defaultdict
from django.core.cache import cache
from django.utils.dateparse import parse_date
from django.utils.html import format_html
import csv
import re
from pathlib import Path
from django.utils import timezone
from datetime import datetime, timedelta, date
from decimal import Decimal
import openpyxl
from io import BytesIO
import pandas as pd
import logging

logger = logging.getLogger(__name__)

# SECURITY: Helper functions for authorization and rate limiting
def _check_login_attempts(ip_address):
    """Check if IP address has exceeded login attempt limit."""
    cache_key = f'login_attempts:{ip_address}'
    attempts = cache.get(cache_key, 0)
    if attempts >= settings.LOGIN_ATTEMPT_LIMIT:
        return False
    return True

def _record_login_attempt(ip_address):
    """Record a failed login attempt for rate limiting."""
    cache_key = f'login_attempts:{ip_address}'
    attempts = cache.get(cache_key, 0)
    cache.set(cache_key, attempts + 1, settings.LOGIN_ATTEMPT_WINDOW)

def _get_client_ip(request):
    """Get client IP address from request, accounting for proxies."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDER_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

def _is_path_safe(file_path, base_dir):
    """Prevent path traversal attacks by ensuring file_path is within base_dir."""
    file_path = Path(file_path).resolve()
    base_dir = Path(base_dir).resolve()
    try:
        file_path.relative_to(base_dir)
        return True
    except ValueError:
        return False

def _validate_date_param(date_str):
    """Validate date parameter to prevent injection."""
    if not date_str:
        return None
    parsed = parse_date(date_str)
    if parsed is None:
        raise ValueError(f"Invalid date format: {date_str}")
    return parsed

from core.models import Transaction, Account, BrokerStatement, FileImportLog, TransactionType
from core.utils import (
    compute_portfolio_value_series,
    compute_realised_pnl,
    compute_positions,
    fetch_last_prices as utils_fetch_last_prices,  # Renamed to avoid conflict with view
    enrich_positions_with_prices,
    compute_cost_basis_series,
    compute_income_series,
    compute_series_by_account,
    group_positions_by_account,
    group_positions_by_category,
)
from core.forms import AccountForm


TRANSACTION_TYPE_LABELS = {
    'BUY': 'Buy',
    'SELL': 'Sell',
    'DIV': 'Dividend',
    'DIST': 'Distribution',
    'INT': 'Interest',
    'WITHTAX': 'Withholding Tax',
    'TAX': 'Tax',
}
POSITION_TRANSACTION_TYPES = ('BUY', 'SELL')
CASH_ACTIVITY_TRANSACTION_TYPES = ('BUY', 'SELL', 'DIV', 'DIST', 'INT', 'WITHTAX', 'TAX')
INCOME_TRANSACTION_TYPES = ('DIV', 'DIST', 'INT')
TAX_TRANSACTION_TYPES = ('WITHTAX', 'TAX')
TRANSACTION_EXPORT_HEADERS = [
    'Date', 'Symbol', 'Type', 'Quantity', 'Price', 'Amount', 'Currency',
    'Amount (CAD)', 'Exchange Rate', 'Account', 'Broker', 'Notes',
]


def _get_transaction_type_options(codes=None):
    requested_codes = tuple(codes) if codes is not None else tuple(TRANSACTION_TYPE_LABELS.keys())
    type_map = {
        tx_type.code: tx_type.label
        for tx_type in TransactionType.objects.filter(code__in=requested_codes).order_by('code')
    }

    options = [
        {
            'code': code,
            'label': type_map.get(code, TRANSACTION_TYPE_LABELS.get(code, code.title())),
        }
        for code in requested_codes
    ]

    if codes is None:
        known_codes = {option['code'] for option in options}
        for tx_type in TransactionType.objects.exclude(code__in=known_codes).order_by('code'):
            options.append({'code': tx_type.code, 'label': tx_type.label})

    return options


def login(request):
    """Login view - handles both GET and POST with rate limiting."""
    if request.method == 'POST':
        client_ip = _get_client_ip(request)
        
        # SECURITY: Rate limit login attempts
        if not _check_login_attempts(client_ip):
            logger.warning(f'Login rate limit exceeded for IP: {client_ip}')
            return render(request, 'core/login.html', {
                'error': 'Too many login attempts. Please try again later.',
            }, status=429)
        
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        
        # SECURITY: Validate inputs
        if not username or not password:
            _record_login_attempt(client_ip)
            return render(request, 'core/login.html', {
                'error': 'Username and password are required.',
            })
        
        user = User.objects.filter(username=username).first()
        if user and user.check_password(password):
            # Clear rate limit on successful login
            cache.delete(f'login_attempts:{client_ip}')
            auth_login(request, user)
            logger.info(f'User {username} logged in successfully from IP: {client_ip}')
            next_url = request.POST.get('next', '/')
            # SECURITY: Validate redirect URL to prevent open redirect
            if next_url.startswith('/'):
                return redirect(next_url)
            return redirect('/')
        else:
            _record_login_attempt(client_ip)
            logger.warning(f'Failed login attempt for user {username} from IP: {client_ip}')
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

    # Get recent transactions - configurable limit (see DASHBOARD_RECENT_LIMIT in settings)
    recent_transactions = Transaction.objects.select_related('account', 'statement').order_by('-date')[:settings.DASHBOARD_RECENT_LIMIT]

    # Get recent imports - same configurable limit
    recent_imports = FileImportLog.objects.order_by('-imported_at')[:settings.DASHBOARD_RECENT_LIMIT]

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
    per_page = settings.TRANSACTIONS_PAGE_SIZE
    from django.core.paginator import Paginator
    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(page)

    return render(request, 'core/transaction_list.html', {
        'page_obj': page_obj,
        'transactions': page_obj,
        'brokers': brokers,
        'symbols': symbols,
        'accounts': accounts,
        'transaction_type_options': _get_transaction_type_options(),
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
        type__in=CASH_ACTIVITY_TRANSACTION_TYPES
    ).values('type').annotate(
        count=Count('id'),
        total_cad=Sum('amount_cad')
    ).order_by('type')

    # Active holdings
    active_holdings = []
    buy_sell_txs = transactions.filter(type__in=POSITION_TRANSACTION_TYPES).order_by('date')
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
    per_page = settings.ACCOUNT_DETAIL_PAGE_SIZE
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
    
    # SECURITY: Verify authorization - currently all logged-in users own all data
    # In a multi-tenant app, add ownership check here:
    # if account.owner_id != request.user.id and not request.user.is_staff:
    #     logger.warning(f'Unauthorized account deletion attempt: {request.user} tried to delete account {account_id}')
    #     return HttpResponse('Unauthorized', status=403)
    
    account_display = account.account_id
    account.delete()
    logger.info(f'Account {account_display} deleted by user {request.user.username}')
    return redirect('accounts_list')

@login_required
def portfolio_summary(request):
    """Portfolio summary with live prices and P&L."""
    logger.info("=== Portfolio Summary Page Requested ===")
    
    # Compute positions
    positions = compute_positions()
    logger.info(f"Computed {len(positions)} positions")

    # Get unique symbols
    symbols = list(set(pos['symbol'] for pos in positions))
    logger.info(f"Unique symbols: {symbols}")

    # Fetch last prices
    prices = utils_fetch_last_prices(symbols)
    logger.info(f"Fetched {len(prices)} prices")

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

    # Total Return metrics
    all_realised = compute_realised_pnl()
    total_realised_pnl_cad = sum(t['realised_pnl_fifo'] for t in all_realised)

    total_income_cad = Transaction.objects.filter(
        type__in=INCOME_TRANSACTION_TYPES
    ).aggregate(total=Sum('amount_cad'))['total'] or Decimal('0')

    total_return_cad = total_unrealised_pnl_cad + total_realised_pnl_cad + total_income_cad

    # Add allocation_pct to each position
    for pos in enriched_positions:
        if pos.get('market_value_cad') is not None and total_market_value_cad:
            pos['allocation_pct'] = round(
                pos['market_value_cad'] / total_market_value_cad * 100, 2
            )
        else:
            pos['allocation_pct'] = None

    # Group by account
    account_groups = group_positions_by_account(enriched_positions)

    # Group by category
    category_groups = group_positions_by_category(enriched_positions)
    for group in category_groups:
        if total_market_value_cad:
            group['allocation_pct'] = round(
                group['subtotal_market_value_cad'] / total_market_value_cad * 100, 2
            )
        else:
            group['allocation_pct'] = None

    # Build currency breakdown
    currency_breakdown = {}
    for pos in enriched_positions:
        cur = pos['currency']
        if cur not in currency_breakdown:
            currency_breakdown[cur] = {'cost_cad': Decimal('0'), 'market_value_cad': Decimal('0')}
        currency_breakdown[cur]['cost_cad'] += pos['total_cost_cad']
        if pos.get('market_value_cad') is not None:
            currency_breakdown[cur]['market_value_cad'] += pos['market_value_cad']
    currency_breakdown = [
        {'currency': k, **v} for k, v in sorted(currency_breakdown.items())
    ]
    
    logger.info(f"Total Cost: {total_cost_cad}, Market Value: {total_market_value_cad}, P&L: {total_unrealised_pnl_cad}")
    logger.info(f"Total Realised: {total_realised_pnl_cad}, Total Income: {total_income_cad}, Total Return: {total_return_cad}")

    return render(request, 'core/portfolio_summary.html', {
        'positions': enriched_positions,
        'total_cost_cad': total_cost_cad,
        'total_market_value_cad': total_market_value_cad,
        'total_unrealised_pnl_cad': total_unrealised_pnl_cad,
        'total_realised_pnl_cad': total_realised_pnl_cad,
        'total_income_cad': total_income_cad,
        'total_return_cad': total_return_cad,
        'account_groups': account_groups,
        'category_groups': category_groups,
        'currency_breakdown': currency_breakdown,
    })

@login_required
def realised_pnl(request):
    """Realised P&L page with FIFO and average-cost methods."""
    account = request.GET.get('account', '')
    symbol = request.GET.get('symbol', '').strip().upper()[:20]
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    # Validate date parameters — silently discard invalid ones
    validated_date_from = None
    validated_date_to = None
    if date_from:
        try:
            validated_date_from = _validate_date_param(date_from)
        except ValueError:
            validated_date_from = None
    if date_to:
        try:
            validated_date_to = _validate_date_param(date_to)
        except ValueError:
            validated_date_to = None

    trades = compute_realised_pnl(
        account_id=account if account else None,
        symbol=symbol if symbol else None,
        date_from=validated_date_from.strftime('%Y-%m-%d') if validated_date_from else None,
        date_to=validated_date_to.strftime('%Y-%m-%d') if validated_date_to else None,
    )

    # Compute totals
    total_pnl_fifo = sum(t['realised_pnl_fifo'] for t in trades)
    total_pnl_avg = sum(t['realised_pnl_avg'] for t in trades)

    # Year grouping
    from collections import defaultdict
    year_groups = defaultdict(list)
    for trade in trades:
        year_groups[trade['sell_date'].year].append(trade)

    year_groups_sorted = sorted(
        [
            {
                'year': year,
                'trades': trade_list,
                'total_pnl_fifo': sum(t['realised_pnl_fifo'] for t in trade_list),
                'total_pnl_avg': sum(t['realised_pnl_avg'] for t in trade_list),
            }
            for year, trade_list in year_groups.items()
        ],
        key=lambda g: g['year'],
        reverse=True,
    )

    # Get all accounts for filter dropdown
    accounts = Account.objects.all().order_by('account_id')

    group_by = request.GET.get('group_by', 'flat')

    return render(request, 'core/realised_pnl.html', {
        'trades': trades,
        'total_pnl_fifo': total_pnl_fifo,
        'total_pnl_avg': total_pnl_avg,
        'accounts': accounts,
        'account_filter': account,
        'symbol_filter': symbol,
        'date_from_filter': validated_date_from.strftime('%Y-%m-%d') if validated_date_from else date_from,
        'date_to_filter': validated_date_to.strftime('%Y-%m-%d') if validated_date_to else date_to,
        'year_groups_sorted': year_groups_sorted,
        'group_by': group_by,
    })

@login_required
def income_summary(request):
    """Income summary grouped by year, symbol, and type."""
    year_filter = request.GET.get('year', '')
    type_filter = request.GET.get('type', '')
    account_filter = request.GET.get('account', '').strip()[:50]

    # Validate account exists
    if account_filter:
        if not Account.objects.filter(account_id=account_filter).exists():
            account_filter = ''

    # Income and tax types
    income_types = list(INCOME_TRANSACTION_TYPES)
    tax_types = list(TAX_TRANSACTION_TYPES)
    all_types = income_types + tax_types

    # Query transactions
    qs = Transaction.objects.filter(type__in=all_types)

    if year_filter:
        qs = qs.filter(date__year=int(year_filter))
    if type_filter:
        qs = qs.filter(type=type_filter)
    if account_filter:
        qs = qs.filter(account__account_id=account_filter)

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

    # Get all accounts for filter dropdown
    accounts = Account.objects.all().order_by('account_id')

    return render(request, 'core/income_summary.html', {
        'rows': rows,
        'total_income_cad': total_income_cad,
        'total_tax_cad': total_tax_cad,
        'net_income_cad': net_income_cad,
        'years': years,
        'income_types': _get_transaction_type_options(all_types),
        'year_filter': year_filter,
        'type_filter': type_filter,
        'account_filter': account_filter,
        'accounts': accounts,
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
    """Serve rejection log CSV file for download with security validation."""
    # SECURITY: Validate month parameter format
    if not re.match(r'^\d{4}_\d{2}$', month):
        logger.warning(f'Invalid month format requested: {month} by user {request.user.username}')
        return HttpResponse('Invalid month format', status=400)
    
    # SECURITY: Construct file path and validate it's within the logs directory
    log_path = Path(settings.IMPORT_LOGS_DIR) / f'rejected_{month}.csv'
    
    if not _is_path_safe(log_path, settings.IMPORT_LOGS_DIR):
        logger.error(f'Path traversal attempt detected: {log_path}')
        return HttpResponse('Access denied', status=403)
    
    if not log_path.exists():
        logger.warning(f'Rejection log not found: {month} requested by user {request.user.username}')
        return HttpResponse('File not found', status=404)
    
    # SECURITY: Use context manager to ensure file is properly closed
    try:
        logger.info(f'Rejection log downloaded: {month} by user {request.user.username}')
        response = FileResponse(open(log_path, 'rb'), content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="rejected_{month}.csv"'
        return response
    except IOError as e:
        logger.error(f'Error reading rejection log {month}: {e}')
        return HttpResponse('Error reading file', status=500)

@login_required
def portfolio_chart(request):
    """Render portfolio performance chart template."""
    return render(request, 'core/portfolio_chart.html')

@login_required
def portfolio_chart_data(request):
    """JSON endpoint for portfolio chart data with multiple series support."""
    try:
        # Parse query parameters
        date_from = request.GET.get('date_from', '')
        date_to = request.GET.get('date_to', '')
        freq = request.GET.get('freq', 'monthly')
        series_param = request.GET.get('series', 'market_value')
        benchmark_ticker = request.GET.get('benchmark', '^GSPC')

        # Validate freq
        VALID_FREQS = {'daily', 'weekly', 'monthly', 'annual'}
        if freq not in VALID_FREQS:
            return JsonResponse(
                {'error': f'Invalid freq: {freq}. Must be one of {sorted(VALID_FREQS)}'},
                status=400
            )

        # Validate date_from
        if date_from:
            try:
                datetime.strptime(date_from, '%Y-%m-%d')
            except ValueError:
                return JsonResponse({'error': f'Invalid date_from format: {date_from}'}, status=400)

        # Validate date_to
        if date_to:
            try:
                datetime.strptime(date_to, '%Y-%m-%d')
            except ValueError:
                return JsonResponse({'error': f'Invalid date_to format: {date_to}'}, status=400)

        # Sanitize benchmark ticker
        if benchmark_ticker:
            # Strip non-alphanumeric except '.', '^', '='
            sanitized = re.sub(r'[^a-zA-Z0-9.^=]', '', benchmark_ticker)
            if len(sanitized) > 20:
                sanitized = sanitized[:20]
            benchmark_ticker = sanitized

        # Parse series list
        series_list = [s.strip() for s in series_param.split(',') if s.strip()]
        if not series_list:
            series_list = ['market_value']

        # Build response
        labels = []
        datasets = []

        # Always include market_value
        if 'market_value' in series_list or not series_list:
            try:
                mv_series = compute_portfolio_value_series(freq=freq, date_from=date_from, date_to=date_to)
                labels = [item['date'] for item in mv_series]
                values = [float(item['market_value_cad']) for item in mv_series]
                datasets.append({
                    'id': 'market_value',
                    'label': 'Portfolio Market Value (CAD)',
                    'data': values,
                    'type': 'line',
                    'yAxisID': 'y',
                })
            except Exception as e:
                logger.error(f"Error computing market value series: {e}")

        # Optional: cost_basis
        if 'cost_basis' in series_list:
            try:
                cb_series = compute_cost_basis_series(date_from=date_from, date_to=date_to, freq=freq)
                cb_values = [float(item['cost_basis_cad']) for item in cb_series]
                # Normalize: treat the start of the range as "bought at market price that day"
                # so cost basis starts at the same value as portfolio market value at t0
                if mv_series and cb_values and float(mv_series[0]['market_value_cad']) > 0:
                    mv0 = float(mv_series[0]['market_value_cad'])
                    cb0 = cb_values[0]
                    cb_values = [mv0 + (v - cb0) for v in cb_values]
                datasets.append({
                    'id': 'cost_basis',
                    'label': 'Cost Basis (CAD)',
                    'data': cb_values,
                    'type': 'line',
                    'yAxisID': 'y',
                })
            except Exception as e:
                logger.error(f"Error computing cost basis series: {e}")

        # Optional: income
        # For daily/weekly, income stays monthly so it remains readable.
        # Only for annual do we pass annual freq to income.
        if 'income' in series_list:
            try:
                income_freq = freq if freq == 'annual' else 'monthly'
                inc_series = compute_income_series(date_from=date_from, date_to=date_to, freq=income_freq)
                inc_values = [float(item['income_cad']) for item in inc_series]
                datasets.append({
                    'id': 'income',
                    'label': 'Monthly Income (CAD)',
                    'data': inc_values,
                    'type': 'bar',
                    'yAxisID': 'y2',
                })
            except Exception as e:
                logger.error(f"Error computing income series: {e}")

        # Optional: benchmark — same starting balance, grows/falls with index returns.
        # Transaction-based cash-flow matching is not used because round-trip trades
        # (buy and sell within a period) cause the benchmark to diverge incorrectly.
        if 'benchmark' in series_list:
            try:
                from .utils import _load_prices_from_db, _load_prices_from_yfinance
                from .models import Transaction as TxModel
                first_tx = TxModel.objects.order_by('date').values_list('date', flat=True).first()
                bench_start = first_tx if first_tx else (date.today() - timedelta(days=3650))
                bench_end = date.today()

                bench_df = _load_prices_from_db([benchmark_ticker], bench_start, bench_end)
                if bench_df is None or benchmark_ticker not in bench_df.columns:
                    bench_df = _load_prices_from_yfinance([benchmark_ticker], bench_start, bench_end)

                bench_prices_series = None
                if bench_df is not None and benchmark_ticker in bench_df.columns:
                    bench_prices_series = bench_df[benchmark_ticker].dropna()

                # Start at portfolio market value at t0 and grow with index returns only.
                mv0 = float(mv_series[0]['market_value_cad']) if mv_series else 0.0
                bench_portfolio = mv0
                prev_bench_price = None
                bench_initialized = False
                bench_values = []

                for lbl in labels:
                    bench_price = None
                    if bench_prices_series is not None:
                        raw = bench_prices_series.asof(pd.Timestamp(lbl[:10]))
                        if raw is not None and not (isinstance(raw, float) and pd.isna(raw)):
                            bench_price = float(raw)

                    if bench_price is None:
                        bench_values.append(None)
                        continue

                    if not bench_initialized:
                        bench_initialized = True
                        prev_bench_price = bench_price
                        bench_values.append(max(0.0, bench_portfolio))
                        continue

                    if prev_bench_price > 0:
                        bench_portfolio *= (bench_price / prev_bench_price)

                    prev_bench_price = bench_price
                    bench_values.append(max(0.0, bench_portfolio))

                safe_id = benchmark_ticker.replace('.', '_').replace('^', '_').replace('=', '_')
                datasets.append({
                    'id': f'benchmark_{safe_id}',
                    'label': f'{benchmark_ticker} (if invested same)',
                    'data': bench_values,
                    'type': 'line',
                    'yAxisID': 'y',
                })
            except Exception as e:
                logger.error(f"Error computing benchmark series: {e}")

        # Optional: by_account
        if 'by_account' in series_list:
            try:
                account_series = compute_series_by_account(date_from=date_from, date_to=date_to, freq=freq)
                account_colors = ['#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f', '#edc948']
                
                for idx, (acct_id, acct_data) in enumerate(account_series.items()):
                    color = account_colors[idx % len(account_colors)]
                    datasets.append({
                        'id': f'account_{acct_id}',
                        'label': f'Account {acct_id}',
                        'data': [float(item['market_value_cad']) for item in acct_data],
                        'type': 'line',
                        'yAxisID': 'y',
                        'borderColor': color,
                        'backgroundColor': color,
                    })
            except Exception as e:
                logger.error(f"Error computing series by account: {e}")

        return JsonResponse({
            'labels': labels,
            'datasets': datasets,
            'freq': freq,
        })
    except Exception as e:
        logger.exception("Error in portfolio_chart_data")
        return JsonResponse({'labels': [], 'datasets': [], 'error': str(e)}, status=500)

def _apply_transaction_filters(qs, request):
    """Apply transaction list filters - shared by list and export views."""
    # SECURITY: Validate and sanitize all filter inputs
    broker = request.GET.get('broker', '').strip()[:100]  # Limit length to prevent abuse
    symbol = request.GET.get('symbol', '').strip().upper()[:10]  # Limit length
    tx_type = request.GET.get('type', '').strip().upper()[:20]  # Limit length
    account_id = request.GET.get('account', '').strip()[:50]  # Limit length
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    
    # Validate date parameters
    validated_date_from = None
    validated_date_to = None
    
    if date_from:
        try:
            validated_date_from = _validate_date_param(date_from)
        except ValueError:
            logger.warning(f'Invalid date_from parameter: {date_from}')
            validated_date_from = None
    
    if date_to:
        try:
            validated_date_to = _validate_date_param(date_to)
        except ValueError:
            logger.warning(f'Invalid date_to parameter: {date_to}')
            validated_date_to = None
    
    # Validate broker against known sources
    if broker:
        broker_exists = BrokerStatement.objects.filter(source=broker).exists()
        if not broker_exists:
            logger.warning(f'Filter request for non-existent broker: {broker}')
            broker = ''
    
    # Validate account exists
    if account_id:
        account_exists = Account.objects.filter(account_id=account_id).exists()
        if not account_exists:
            logger.warning(f'Filter request for non-existent account: {account_id}')
            account_id = ''
    
    # Apply filters
    if broker:
        qs = qs.filter(statement__source=broker)
    if symbol:
        qs = qs.filter(symbol=symbol)
    if tx_type:
        qs = qs.filter(type=tx_type)
    if account_id:
        qs = qs.filter(account__account_id=account_id)
    if validated_date_from:
        qs = qs.filter(date__gte=validated_date_from)
    if validated_date_to:
        qs = qs.filter(date__lte=validated_date_to)
    
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
    """Export transactions to CSV. User must be authenticated."""
    qs = Transaction.objects.select_related('statement', 'account').all()
    qs, filters = _apply_transaction_filters(qs, request)

    # SECURITY: Log export activity
    logger.info(f'CSV export requested by {request.user.username} with filters: {filters} ({qs.count()} transactions)')

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename=transactions_{datetime.now().strftime("%Y-%m-%d")}.csv'

    writer = csv.writer(response)
    writer.writerow(TRANSACTION_EXPORT_HEADERS)

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
    """Export transactions to Excel. User must be authenticated."""
    qs = Transaction.objects.select_related('statement', 'account').all()
    qs, filters = _apply_transaction_filters(qs, request)

    # SECURITY: Log export activity
    logger.info(f'Excel export requested by {request.user.username} with filters: {filters} ({qs.count()} transactions)')

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Transactions'

    ws.append(TRANSACTION_EXPORT_HEADERS)

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

@login_required
def tax_summary(request):
    """Annual Tax Summary report covering capital gains, income, withholding tax, and by account category."""
    year_param = request.GET.get('year', '')
    if year_param:
        try:
            year = int(year_param)
        except (ValueError, TypeError):
            year = date.today().year - 1
    else:
        year = date.today().year - 1

    date_from = f'{year}-01-01'
    date_to = f'{year}-12-31'

    # Section A — Capital Gains / Losses
    trades = compute_realised_pnl(date_from=date_from, date_to=date_to)
    total_gains_fifo = sum(t['realised_pnl_fifo'] for t in trades if t['realised_pnl_fifo'] > 0)
    total_losses_fifo = sum(t['realised_pnl_fifo'] for t in trades if t['realised_pnl_fifo'] < 0)
    net_capital_gain_fifo = total_gains_fifo + total_losses_fifo

    total_gains_avg = sum(t['realised_pnl_avg'] for t in trades if t['realised_pnl_avg'] > 0)
    total_losses_avg = sum(t['realised_pnl_avg'] for t in trades if t['realised_pnl_avg'] < 0)
    net_capital_gain_avg = total_gains_avg + total_losses_avg

    # Section B — Income
    income_txs = Transaction.objects.filter(
        date__year=year,
        type__in=INCOME_TRANSACTION_TYPES
    ).select_related('account')
    income_by_type = defaultdict(lambda: Decimal('0'))
    for tx in income_txs:
        income_by_type[tx.type] += tx.amount_cad
    total_gross_income_cad = sum(income_by_type.values())

    # Section C — Withholding Tax
    tax_txs = Transaction.objects.filter(
        date__year=year,
        type__in=TAX_TRANSACTION_TYPES
    ).select_related('account')
    total_tax_withheld_cad = sum(tx.amount_cad for tx in tax_txs)

    # Section D — By Account Category
    all_txs_for_category = Transaction.objects.filter(
        date__year=year
    ).select_related('account').filter(
        dj_Q(type__in=POSITION_TRANSACTION_TYPES) | dj_Q(type__in=INCOME_TRANSACTION_TYPES)
    )
    category_data = {}
    for tx in all_txs_for_category:
        cat = tx.account.category if tx.account and tx.account.category else 'TAXABLE'
        if cat not in category_data:
            category_data[cat] = {
                'capital_gains_fifo': Decimal('0'),
                'capital_gains_avg': Decimal('0'),
                'income_cad': Decimal('0'),
                'tax_withheld_cad': Decimal('0'),
            }
        if tx.type in POSITION_TRANSACTION_TYPES:
            # We need to compute per-trade P&L — for simplicity, use the trades list
            pass
        elif tx.type in INCOME_TRANSACTION_TYPES:
            category_data[cat]['income_cad'] += tx.amount_cad
        elif tx.type in TAX_TRANSACTION_TYPES:
            category_data[cat]['tax_withheld_cad'] += tx.amount_cad

    # Compute capital gains per category from trades
    for trade in trades:
        cat = trade.get('account_category', 'TAXABLE')
        if cat not in category_data:
            category_data[cat] = {
                'capital_gains_fifo': Decimal('0'),
                'capital_gains_avg': Decimal('0'),
                'income_cad': Decimal('0'),
                'tax_withheld_cad': Decimal('0'),
            }
        category_data[cat]['capital_gains_fifo'] += trade['realised_pnl_fifo']
        category_data[cat]['capital_gains_avg'] += trade['realised_pnl_avg']

    # Rebuild Section D with proper category from accounts
    category_data = {}
    # Get all relevant transactions with account categories
    relevant_txs = Transaction.objects.filter(
        date__year=year
    ).select_related('account').filter(
        dj_Q(type__in=POSITION_TRANSACTION_TYPES) |
        dj_Q(type__in=INCOME_TRANSACTION_TYPES) |
        dj_Q(type__in=TAX_TRANSACTION_TYPES)
    )

    # Compute capital gains per account for category grouping
    account_gains = defaultdict(lambda: {'fifo': Decimal('0'), 'avg': Decimal('0')})
    for trade in trades:
        acct_id = trade['account_id']
        account_gains[acct_id]['fifo'] += trade['realised_pnl_fifo']
        account_gains[acct_id]['avg'] += trade['realised_pnl_avg']

    # Build category-level data
    for tx in relevant_txs:
        cat = tx.account.category if tx.account and tx.account.category else 'TAXABLE'
        if cat not in category_data:
            category_data[cat] = {
                'capital_gains_fifo': Decimal('0'),
                'capital_gains_avg': Decimal('0'),
                'income_cad': Decimal('0'),
                'tax_withheld_cad': Decimal('0'),
            }
        if tx.type in POSITION_TRANSACTION_TYPES:
            # Capital gains will be added from trades below
            pass
        elif tx.type in INCOME_TRANSACTION_TYPES:
            category_data[cat]['income_cad'] += tx.amount_cad
        elif tx.type in TAX_TRANSACTION_TYPES:
            category_data[cat]['tax_withheld_cad'] += tx.amount_cad

    # Add capital gains per category from trades
    for trade in trades:
        acct_id = trade['account_id']
        acct = Account.objects.filter(account_id=acct_id).first()
        cat = acct.category if acct and acct.category else 'TAXABLE'
        if cat not in category_data:
            category_data[cat] = {
                'capital_gains_fifo': Decimal('0'),
                'capital_gains_avg': Decimal('0'),
                'income_cad': Decimal('0'),
                'tax_withheld_cad': Decimal('0'),
            }
        category_data[cat]['capital_gains_fifo'] += trade['realised_pnl_fifo']
        category_data[cat]['capital_gains_avg'] += trade['realised_pnl_avg']

    # Get distinct years available from all relevant transactions
    years_available = sorted(
        set(
            Transaction.objects.exclude(date__year=None)
            .values_list('date__year', flat=True)
            .distinct()
        ),
        reverse=True,
    )

    return render(request, 'core/tax_summary.html', {
        'year': year,
        'years_available': years_available,
        # Section A
        'total_gains_fifo': total_gains_fifo,
        'total_losses_fifo': total_losses_fifo,
        'net_capital_gain_fifo': net_capital_gain_fifo,
        'total_gains_avg': total_gains_avg,
        'total_losses_avg': total_losses_avg,
        'net_capital_gain_avg': net_capital_gain_avg,
        # Section B
        'income_by_type': dict(income_by_type),
        'total_gross_income_cad': total_gross_income_cad,
        # Section C
        'total_tax_withheld_cad': total_tax_withheld_cad,
        # Section D
        'category_data': category_data,
    })


def fetch_prices_api(request):
    """API endpoint for fetching last prices (unused - placeholder)."""
    return JsonResponse({})
