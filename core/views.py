from django.http import HttpResponse, JsonResponse
from django.utils.csv import csv
from django.utils import timezone
from datetime import datetime
import openpyxl
from io import BytesIO

from core.models import Transaction, Account, BrokerStatement

def _apply_transaction_filters(qs, request):
    broker = request.GET.get('broker')
    symbol = request.GET.get('symbol')
    tx_type = request.GET.get('type')
    account_id = request.GET.get('account')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')

    if broker:
        qs = qs.filter(broker_statement__broker__name=broker)
    if symbol:
        qs = qs.filter(symbol=symbol)
    if tx_type:
        qs = qs.filter(type=tx_type)
    if account_id:
        qs = qs.filter(account_id=account_id)
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)

    return qs, {
        'broker': broker,
        'symbol': symbol,
        'type': tx_type,
        'account': account_id,
        'date_from': date_from,
        'date_to': date_to
    }

def transaction_export_csv(request):
    qs = Transaction.objects.all()
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
            tx.exchange_rate, tx.account.id, tx.broker_statement.broker.name,
            tx.notes
        ])

    return response

def transaction_export_excel(request):
    qs = Transaction.objects.all()
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
            tx.exchange_rate, tx.account.id, tx.broker_statement.broker.name,
            tx.notes
        ])

    for cell in ws[1]:
        cell.font = openpyxl.styles.Font(bold=True)

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename=transactions_{datetime.now().strftime("%Y-%m-%d")}.xlsx'

    wb.save(BytesIO())
    return response

def portfolio_chart_data(request):
    """
    JSON endpoint for portfolio chart data.
    Returns {'labels': [...dates...], 'values': [...float values...]}
    """
    try:
        series = compute_portfolio_value_series()
        
        # Format for Chart.js
        labels = [item['date'] for item in series]
        values = [float(item['market_value_cad']) for item in series]
        
        return JsonResponse({'labels': labels, 'values': values})
    except Exception:
        return JsonResponse({'labels': [], 'values': []}, status=500)

def portfolio_chart(request):
    """
    Render portfolio performance chart template.
    """
    return render(request, 'core/portfolio_chart.html')