from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models import Count, Sum, Q
from django.shortcuts import get_object_or_404, render, redirect
from django.http import HttpResponseNotAllowed

from core.models import Account, FileImportLog, Transaction, TransactionType
from core.utils import compute_positions, fetch_last_prices, enrich_positions_with_prices, compute_realised_pnl
from core.forms import AccountForm


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
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')

    if broker:
        qs = qs.filter(statement__source=broker)
    if symbol:
        qs = qs.filter(symbol__icontains=symbol)
    if tx_type:
        qs = qs.filter(type=tx_type)
    if account_id:
        qs = qs.filter(account__account_id=account_id)
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)

    qs = qs.order_by('-date')

    paginator = Paginator(qs, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    accounts = Account.objects.all().order_by('account_id')
    tx_types = TransactionType.objects.order_by('code')

    return render(request, 'core/transaction_list.html', {
        'page_obj': page_obj,
        'broker_filter': broker,
        'symbol_filter': symbol,
        'type_filter': tx_type,
        'account_filter': account_id,
        'date_from': date_from or '',
        'date_to': date_to or '',
        'accounts': accounts,
        'tx_types': tx_types,
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

    # Cash flow summary
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

    # Holdings (current positions)
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

    # Paginated transaction list
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


def account_create(request):
    if request.method == 'POST':
        form = AccountForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('accounts_list')
    else:
        form = AccountForm()
    return render(request, 'core/account_form.html', {'form': form, 'title': 'Add Account'})


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

    return render(request, 'core/account_form.html', {'form': form, 'title': 'Edit Account'})


def account_delete(request, account_id):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    account = get_object_or_404(Account, account_id=account_id)
    account.delete()
    return redirect('accounts_list')


def portfolio_summary(request):
    positions = compute_positions()
    symbols = [p['symbol'] for p in positions if p['symbol']]
    prices = fetch_last_prices(symbols)
    positions = enrich_positions_with_prices(positions, prices)
    positions.sort(key=lambda p: (p['broker'], p['account_name'], p['symbol'] or ''))

    total_cost_cad = sum(p['total_cost_cad'] for p in positions)
    total_market_value_cad = sum(
        p['market_value_cad'] for p in positions if p.get('market_value_cad') is not None
    )
    total_unrealised_pnl_cad = total_market_value_cad - sum(
        p['total_cost_cad'] for p in positions if p.get('market_value_cad') is not None
    )

    return render(request, 'core/portfolio.html', {
        'positions': positions,
        'total_cost_cad': total_cost_cad,
        'total_market_value_cad': total_market_value_cad,
        'total_unrealised_pnl_cad': total_unrealised_pnl_cad,
    })


def realised_pnl(request):
    account_filter = request.GET.get('account')
    trades = compute_realised_pnl(account_id=account_filter)

    total_pnl_fifo = sum(t['realised_pnl_fifo'] for t in trades)
    total_pnl_avg = sum(t['realised_pnl_avg'] for t in trades)

    accounts = Account.objects.all().order_by('account_id')

    return render(request, 'core/realised_pnl.html', {
        'trades': trades,
        'total_pnl_fifo': total_pnl_fifo,
        'total_pnl_avg': total_pnl_avg,
        'accounts': accounts,
        'account_filter': account_filter,
    })