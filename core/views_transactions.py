"""Transaction list and export views."""

import csv
import logging
from django.conf import settings
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render

from core.models import Transaction, BrokerStatement, Account
from core.views import _get_transaction_type_options, _validate_date_param, TRANSACTION_EXPORT_HEADERS

logger = logging.getLogger(__name__)


def _apply_transaction_filters(qs, request):
    """Apply transaction list filters - shared by list and export views."""
    from django.conf import settings
    
    # SECURITY: Validate and sanitize all filter inputs
    broker = request.GET.get('broker', '').strip()[:100]  # Limit length to prevent abuse
    symbol = request.GET.get('symbol', '').strip().upper()[:10]  # Limit length
    tx_type = request.GET.get('type', '').strip().upper()[:20]  # Limit length
    account_param = request.GET.get('account', '').strip()[:50]  # Limit length
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
    
    # Validate account exists (pk-based; ?account= carries Account.pk)
    account_pk = None
    if account_param:
        try:
            candidate_pk = int(account_param)
        except ValueError:
            logger.warning(f'Filter request with non-integer account param: {account_param!r}')
        else:
            if Account.objects.filter(pk=candidate_pk).exists():
                account_pk = candidate_pk
            else:
                logger.warning(f'Filter request for non-existent account pk: {candidate_pk}')
    
    # Apply filters
    if broker:
        qs = qs.filter(statement__source=broker)
    if symbol:
        qs = qs.filter(symbol=symbol)
    if tx_type:
        qs = qs.filter(type=tx_type)
    if account_pk is not None:
        qs = qs.filter(account__pk=account_pk)
    if validated_date_from:
        qs = qs.filter(date__gte=validated_date_from)
    if validated_date_to:
        qs = qs.filter(date__lte=validated_date_to)
    
    filters = {
        'broker': broker,
        'symbol': symbol,
        'type': tx_type,
        'account': account_pk if account_pk is not None else '',
        'date_from': date_from,
        'date_to': date_to,
    }
    
    return qs, filters


@login_required
def transaction_list(request):
    """Transaction list with filters and pagination."""
    qs = Transaction.objects.select_related('statement', 'account').all()

    # Apply filters
    broker = request.GET.get('broker', '')
    symbol = request.GET.get('symbol', '')
    tx_type = request.GET.get('type', '')
    account_param = request.GET.get('account', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    if broker:
        qs = qs.filter(statement__source=broker)
    if symbol:
        qs = qs.filter(symbol=symbol)
    if tx_type:
        qs = qs.filter(type=tx_type)
    # ?account= carries Account.pk
    account_pk = None
    if account_param.strip():
        try:
            candidate_pk = int(account_param.strip())
        except ValueError:
            candidate_pk = None
        if candidate_pk is not None and Account.objects.filter(pk=candidate_pk).exists():
            account_pk = candidate_pk
    if account_pk is not None:
        qs = qs.filter(account__pk=account_pk)
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
            'account': account_pk if account_pk is not None else '',
            'date_from': date_from,
            'date_to': date_to,
        },
    })


@login_required
def transaction_export_csv(request):
    """Export transactions to CSV. User must be authenticated."""
    qs = Transaction.objects.select_related('statement', 'account').all()
    qs, filters = _apply_transaction_filters(qs, request)

    # SECURITY: Log export activity
    logger.info(f'CSV export requested by {request.user.username} with filters: {filters} ({qs.count()} transactions)')

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename=transactions_{timezone.now().strftime("%Y-%m-%d")}.csv'

    writer = csv.writer(response)
    writer.writerow(TRANSACTION_EXPORT_HEADERS)

    for tx in qs:
        writer.writerow([
            tx.date.strftime('%Y-%m-%d'), tx.symbol, tx.type, tx.quantity,
            tx.price, tx.amount, tx.currency, tx.amount_cad,
            tx.exchange_rate_cad, tx.account.name if tx.account else '',
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

    import openpyxl
    from openpyxl.styles import Font
    from io import BytesIO
    
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = 'Transactions'

    ws.append(TRANSACTION_EXPORT_HEADERS)

    for tx in qs:
        ws.append([
            tx.date.strftime('%Y-%m-%d'), tx.symbol, tx.type, tx.quantity,
            tx.price, tx.amount, tx.currency, tx.amount_cad,
            tx.exchange_rate_cad, tx.account.name if tx.account else '',
            tx.statement.source, tx.notes
        ])

    for cell in ws[1]:
        cell.font = Font(bold=True)

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename=transactions_{timezone.now().strftime("%Y-%m-%d")}.xlsx'

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    response.write(buffer.getvalue())
    
    return response
