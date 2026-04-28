from django.shortcuts import render
from django.core.paginator import Paginator
from core.models import Transaction, FileImportLog
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
    qs = Transaction.objects.select_related('statement').all()
    
    broker = request.GET.get('broker')
    symbol = request.GET.get('symbol')
    tx_type = request.GET.get('type')
    
    if broker:
        qs = qs.filter(statement__source=broker)
    if symbol:
        qs = qs.filter(symbol=symbol)
    if tx_type:
        qs = qs.filter(type=tx_type)
        
    qs = qs.order_by('-date')
    
    paginator = Paginator(qs, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'core/transaction_list.html', {
        'page_obj': page_obj,
        'broker_filter': broker,
        'symbol_filter': symbol,
        'type_filter': tx_type,
    })
