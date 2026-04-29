from django.shortcuts import render
from django.core.paginator import Paginator
from core.models import Transaction, FileImportLog, Account
from core.utils import compute_positions
from django.db.models import Count, Sum, Q

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
