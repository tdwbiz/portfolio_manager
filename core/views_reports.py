"""Report views: income, tax, import history, annual reports, and chart data."""

import logging
import re
from collections import defaultdict
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse, FileResponse
from django.shortcuts import render
from django.conf import settings
from django.db.models import Q as dj_Q

from core.models import Transaction, Account, FileImportLog, RbcAnnual
from core.views import (
    _is_path_safe,
    _get_transaction_type_options,
    INCOME_TRANSACTION_TYPES,
    TAX_TRANSACTION_TYPES,
    POSITION_TRANSACTION_TYPES,
)
from core.utils import compute_realised_pnl

logger = logging.getLogger(__name__)


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
        with open(log_path, 'rb') as f:
            response = FileResponse(f, content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="rejected_{month}.csv"'
            return response
    except OSError as e:
        logger.error(f'Error reading rejection log {month}: {e}')
        return HttpResponse('Error reading file', status=500)


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


class _ReportSummaryRow:
    """Fake object to bridge RbcAnnual flat table to the template that expects a report object.
    
    The template expects: summary.report.report_year, summary.report.account.name,
    summary.report.id, summary.report.balance_snapshots, summary.report.account_type_code.
    """
    def __init__(self, cad_record):
        self._cad = cad_record
        # The template accesses summary.report.* — expose the RbcAnnual as .report
        self.report = cad_record  # RbcAnnual instance

    @property
    def ending_value(self):
        return self._cad.bal_ending_value if self._cad else Decimal('0')

    @property
    def net_return(self):
        return self._cad.bal_net_return if self._cad else Decimal('0')

    @property
    def total_charges_cad(self):
        return self._cad.chg_total_paid if self._cad else Decimal('0')

    @property
    def total_compensation_cad(self):
        return self._cad.comp_total if self._cad else Decimal('0')

    @property
    def balance_snapshots(self):
        """Return a simple queryset-like object with balance data from the CAD record."""
        if not self._cad:
            return _EmptyQuerySet([])
        return _EmptyQuerySet([{
            'currency': 'CAD',
            'beginning_market_value': self._cad.bal_beginning_value,
            'ending_market_value': self._cad.bal_ending_value,
            'net_investment_return': self._cad.bal_net_return,
        }])


class _EmptyQuerySet:
    """Minimal queryset-like wrapper for template rendering."""
    def __init__(self, items):
        self._items = items

    def __iter__(self):
        return iter(self._items)

    def exists(self):
        return bool(self._items)


@login_required
def annual_reports_view(request):
    """List all RBC Annual Reports for the user's accounts."""
    records = RbcAnnual.objects.select_related('account').order_by('-report_year', 'account', 'currency')

    # Group into per-report summaries (one per account+year, combining CAD and USD rows)
    report_summaries = []
    seen = set()
    for rec in records:
        key = (rec.account_id, rec.report_year)
        if key not in seen:
            seen.add(key)
            cad = records.filter(account=rec.account, report_year=rec.report_year, currency='CAD').first()
            _ = records.filter(account=rec.account, report_year=rec.report_year, currency='USD').first()
            summary = _ReportSummaryRow(cad)
            report_summaries.append(summary)

    return render(request, 'core/annual_reports.html', {
        'report_summaries': report_summaries,
    })
