"""Ledger views — per-month ledger report."""

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404

from core.models import (
    Account,
    LedgerAccount,
    LedgerEntry,
    MonthlyLedgerBalance,
)

logger = logging.getLogger(__name__)


@login_required
def ledger_month_view(request, account_id, year, month):
    """Show a ledger month report: opening balance, all entries, closing balance.

    URL pattern: /ledger/<account_id>/<year>/<month>/
    """
    account = get_object_or_404(Account, account_id=account_id)
    stmt_month = date.fromisoformat(f"{year}-{month}-01")

    # All ledger accounts for this account
    ledger_accounts = LedgerAccount.objects.filter(account=account).order_by(
        'account_type', 'currency', 'symbol'
    )

    # Entries for this month, ordered by date
    entries = LedgerEntry.objects.filter(
        ledger_account__account=account,
        statement_month=stmt_month,
    ).order_by('entry_date', 'created_at')

    # Previous month's closing balances (used as opening balances)
    prev_month = (stmt_month.replace(day=28) + timedelta(days=-1)).replace(day=1)
    prev_balances = {}
    for snap in MonthlyLedgerBalance.objects.filter(
        ledger_account__account=account,
        statement_month=prev_month,
    ):
        prev_balances[snap.ledger_account_id] = {
            'quantity': snap.quantity,
            'cost_basis_total': snap.cost_basis_total,
            'cost_basis_per_unit': snap.cost_basis_per_unit,
        }

    # If no previous snapshot, fall back to two months ago, etc.
    if not prev_balances:
        fallback = MonthlyLedgerBalance.objects.filter(
            ledger_account__account=account,
            statement_month__lt=stmt_month,
        ).order_by('-statement_month')
        for snap in fallback:
            if snap.ledger_account_id not in prev_balances:
                prev_balances[snap.ledger_account_id] = {
                    'quantity': snap.quantity,
                    'cost_basis_total': snap.cost_basis_total,
                    'cost_basis_per_unit': snap.cost_basis_per_unit,
                }

    # Build per-account sections
    sections = []
    for la in ledger_accounts:
        la_entries = entries.filter(ledger_account=la)

        opening_qty = prev_balances.get(la.id, {}).get('quantity', Decimal('0'))
        opening_cb = prev_balances.get(la.id, {}).get('cost_basis_total', Decimal('0'))

        net_qty = Decimal('0')
        net_cb = Decimal('0')
        net_cash = Decimal('0')

        for e in la_entries:
            if la.account_type == 'POSITION':
                net_qty += e.quantity_change
                net_cb += e.cost_basis_change
            else:
                net_cash += e.cash_amount

        # Use MonthlyLedgerBalance snapshot for closing balance if available,
        # otherwise compute from opening + net changes
        current_snap = MonthlyLedgerBalance.objects.filter(
            ledger_account=la,
            statement_month=stmt_month,
        ).first()

        if current_snap:
            closing_qty = current_snap.quantity
            closing_cb = current_snap.cost_basis_total
        else:
            closing_qty = opening_qty + net_qty
            closing_cb = opening_cb + net_cb

        closing_cbu = (
            (closing_cb / closing_qty).quantize(Decimal('0.0001'))
            if closing_qty > 0 else Decimal('0')
        )

        sections.append({
            'ledger_account': la,
            'entries': la_entries,
            'opening_qty': opening_qty,
            'opening_cb': opening_cb,
            'closing_qty': closing_qty,
            'closing_cb': closing_cb,
            'closing_cbu': closing_cbu,
            'net_cash': net_cash,
        })

    # Summary totals
    total_net_cash = sum(s['net_cash'] for s in sections)

    # Previous / next month navigation
    prev = stmt_month.replace(day=1) - timedelta(days=1)
    prev = prev.replace(day=1)
    next_m = (stmt_month.replace(day=28) + timedelta(days=4)).replace(day=1)

    context = {
        'account': account,
        'stmt_month': stmt_month,
        'prev_month': prev,
        'next_month': next_m,
        'sections': sections,
        'total_net_cash': total_net_cash,
    }
    return render(request, 'core/ledger_month.html', context)
