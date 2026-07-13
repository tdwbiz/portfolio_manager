"""Account management views."""

import logging

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponseNotAllowed
from django.conf import settings
from django.db.models import Sum, Count
from decimal import Decimal

from core.models import Account, AccountCurrency, Transaction
from core.forms import AccountForm, AccountCurrencyFormSet
from core.views import CASH_ACTIVITY_TRANSACTION_TYPES, POSITION_TRANSACTION_TYPES

logger = logging.getLogger(__name__)


@login_required
def accounts_list(request):
    accounts = Account.objects.all()
    return render(request, 'core/accounts.html', {'accounts': accounts})


@login_required
def account_detail(request, account_id):
    account = get_object_or_404(Account, account_id=account_id)
    transactions = Transaction.objects.filter(account=account).order_by('-date')

    # Get sub-account currencies
    currencies = account.currencies.all()

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
        'currencies': currencies,
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
            account = form.save()
            return redirect('account_edit', account_id=account.account_id)
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
        currency_formset = AccountCurrencyFormSet(request.POST, instance=account)
        if form.is_valid() and currency_formset.is_valid():
            form.save()
            currency_formset.save()
            return redirect('account_detail', account_id=account.account_id)
    else:
        form = AccountForm(instance=account)
        currency_formset = AccountCurrencyFormSet(instance=account)

    return render(request, 'core/account_form.html', {
        'form': form,
        'currency_formset': currency_formset,
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
