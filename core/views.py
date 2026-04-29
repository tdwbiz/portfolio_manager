from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models import Count, Sum, Q
from django.shortcuts import get_object_or_404, render

from core.models import Account, FileImportLog, Transaction
from core.utils import compute_positions

def dashboard(request):
    positions = compute_positions()
    total_value = sum(p['total_cost_cad'] for p in positions)
    recent_imports = FileImportLog.objects.all().order_by('-imported_at')[:10]
    
    return render(request, 'core/dashboard.html', {
        'positions': positions,
        'total_value': total_value,
        'recent_imports': recent_imports,
    })

def transaction_list(request):
    qs = Transaction.objects.select_related('statement', 'account').all()
    
    broker = request.GET.get('broker')
    symbol = request.GET.get('symbol')
    tx_type = request.GET.get('type')
    account_id = request.GET.get('account')
    
    if broker:
        qs = qs.filter(statement__source=broker)
    if symbol:
        qs = qs.filter(symbol=symbol)
    if tx_type:
        qs = qs.filter(type=tx_type)
    if account_id:
        qs = qs.filter(account__account_id=account_id)
        
    qs = qs.order_by('-date')
    
    paginator = Paginator(qs, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    accounts = Account.objects.all().order_by('account_id')
    
    return render(request, 'core/transaction_list.html', {
        'page_obj': page_obj,
        'broker_filter': broker,
        'symbol_filter': symbol,
        'type_filter': tx_type,
        'account_filter': account_id,
        'accounts': accounts,
    })

def accounts_list(request):
    accounts = Account.objects.all().order_by('broker', 'account_id').annotate(
        transaction_count=Count('transactions'),
        total_invested_cad=Sum(
            'transactions__amount_cad',
            filter=Q(transactions__type='BUY')
        )
    )

    return render(request, 'core/accounts.html', {
        'accounts': accounts,
    })


def account_detail(request, account_id):
    account = get_object_or_404(Account, account_id=account_id)

    transactions = (
        Transaction.objects
        .filter(account=account)
        .select_related('statement')
        .order_by('-date', '-id')
    )

    # ── Cash flow summary ────────────────────────────────────────────────────
    # Sum all amount_cad (already signed: negative = outflow, positive = inflow)
    total_cash_flow_cad = (
        transactions.aggregate(total=Sum('amount_cad'))['total'] or Decimal('0')
    )
    ending_balance_cad = account.starting_balance + total_cash_flow_cad

    # Breakdown by transaction type
    cash_by_type = (
        transactions
        .values('type')
        .annotate(total_cad=Sum('amount_cad'), count=Count('id'))
        .order_by('type')
    )

    # ── Holdings (current positions) ─────────────────────────────────────────
    holdings = {}
    for tx in transactions.filter(type__in=['BUY', 'SELL']):
        sym = tx.symbol or '(unknown)'
        if sym not in holdings:
            holdings[sym] = {
                'symbol': sym,
                'shares': Decimal('0'),
                'buy_qty': Decimal('0'),
                'buy_cost_cad': Decimal('0'),
            }
        h = holdings[sym]
        if tx.type == 'BUY':
            h['shares'] += tx.quantity
            h['buy_qty'] += tx.quantity
            h['buy_cost_cad'] += abs(tx.amount_cad)
        else:  # SELL
            h['shares'] -= tx.quantity

    active_holdings = sorted(
        [h for h in holdings.values() if h['shares'] > Decimal('0.0001')],
        key=lambda h: h['symbol'],
    )
    for h in active_holdings:
        h['avg_cost'] = (
            h['buy_cost_cad'] / h['buy_qty'] if h['buy_qty'] else Decimal('0')
        )

    # ── Paginated transaction list ────────────────────────────────────────────
    paginator = Paginator(transactions, 50)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'core/account_detail.html', {
        'account': account,
        'total_cash_flow_cad': total_cash_flow_cad,
        'ending_balance_cad': ending_balance_cad,
        'cash_by_type': cash_by_type,
        'active_holdings': active_holdings,
        'page_obj': page_obj,
    })
