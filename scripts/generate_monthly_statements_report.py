"""Generate monthly statement .md files for each account.

For each account, produces one .md file in docs/reconciliation/ with:
  - Opening Balance (from previous month's ending, anchored from RBC)
  - Holdings at month-end (from PositionHistory, deduplicated)
  - Activities during the month (from Transaction)
  - Ending Balance (computed = opening + net_flow, verified vs RBC)

Strategy:
  1. Pre-compute all RBC ending balances per month/currency
  2. Anchor the balance chain at the first RBC month
  3. Walk forward: ending = opening + net_flow
  4. Walk backward: opening = ending - net_flow

Usage:
    python scripts/generate_monthly_statements_report.py [--account ACCOUNT_ID]
"""
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
import django
django.setup()

from core.models import (
    Account, Transaction, PositionHistory,
    RbcMonthly, AccountCurrency,
)

output_dir = 'docs/reconciliation'
os.makedirs(output_dir, exist_ok=True)


def fmt(val):
    if val is None:
        return 'N/A'
    return f'{val:,.2f}'


def month_key(d):
    return (d.year, d.month)


def month_label(ym):
    return f'{ym[0]}-{ym[1]:02d}'


def month_end(year, month):
    if month == 12:
        return date(year + 1, 1, 1) - timedelta(days=1)
    return date(year, month + 1, 1) - timedelta(days=1)


def get_current_position(account, as_of_date, symbol, currency):
    """Get the most recent open position for a symbol as of a date."""
    return PositionHistory.objects.filter(
        account=account,
        symbol=symbol,
        currency=currency,
        date_from__lte=as_of_date,
        date_to__isnull=True,
    ).order_by('-date_from').first()


def get_rbc_ending_balances(account, months):
    """Pre-load RBC ending balances for all months."""
    balances = {}
    for ym in months:
        rbc = RbcMonthly.objects.filter(
            account=account,
            statement_date__year=ym[0],
            statement_date__month=ym[1],
        ).order_by('-statement_date')
        for r in rbc:
            val = r.end_total_market_value if hasattr(r, 'end_total_market_value') else None
            if val:
                balances.setdefault(ym, {})[r.currency] = val
            else:
                mb = RbcMonthly.objects.filter(pk=r.pk).first()
                # Try alternate field
                if hasattr(r, 'end_total_market_value'):
                    val = r.end_total_market_value
                if val:
                    balances.setdefault(ym, {})[r.currency] = val
    return balances


def generate_for_account(account):
    months = set()

    # Collect all months from all data sources
    for tx in Transaction.objects.filter(account=account).only('date'):
        months.add(month_key(tx.date))
    for ph in PositionHistory.objects.filter(account=account).only('date_from'):
        months.add(month_key(ph.date_from))
    for r in RbcMonthly.objects.filter(account=account).only('statement_date'):
        months.add(month_key(r.statement_date))

    if not months:
        return

    months = sorted(months)

    # Currencies
    currencies = set()
    for tx in Transaction.objects.filter(account=account).only('currency'):
        currencies.add(tx.currency)
    for ph in PositionHistory.objects.filter(account=account).only('currency'):
        currencies.add(ph.currency)
    for r in RbcMonthly.objects.filter(account=account).only('currency'):
        currencies.add(r.currency)
    if not currencies:
        currencies = {'CAD'}

    # Pre-load data
    rbc_endings = get_rbc_ending_balances(account, months)

    # Group transactions by (month, currency)
    tx_by_month = defaultdict(lambda: defaultdict(list))
    for tx in Transaction.objects.filter(account=account).order_by('date', 'symbol'):
        ym = month_key(tx.date)
        tx_by_month[ym][tx.currency].append(tx)

    # Get all unique (symbol, currency) pairs for this account
    symbols = set()
    for ph in PositionHistory.objects.filter(account=account).only('symbol', 'currency'):
        symbols.add((ph.symbol, ph.currency))

    # --- Build balance chain ---
    # Phase 1: Anchor from RBC, walk forward
    ending_balances = {}  # (ym, currency) -> Decimal

    # Find first RBC anchor per currency
    anchors = {}
    for ym in months:
        for currency in currencies:
            if ym in rbc_endings and currency in rbc_endings[ym]:
                if currency not in anchors:
                    anchors[currency] = ym

    # Forward from anchors
    for currency, anchor_ym in anchors.items():
        # Find starting balance from AccountCurrency
        acc_curr = AccountCurrency.objects.filter(
            account=account, currency=currency
        ).first()
        start_balance = None
        if acc_curr and acc_curr.starting_balance and acc_curr.starting_balance_date:
            start_balance = acc_curr.starting_balance

        # Use RBC ending at anchor as the starting point
        prev_ending = rbc_endings[anchor_ym][currency]

        # Walk backward from anchor to get opening at anchor
        anchor_idx = months.index(anchor_ym)
        for i in range(anchor_idx, -1, -1):
            ym = months[i]
            net_flow = sum(
                (t.amount_cad or Decimal('0'))
                for t in tx_by_month.get(ym, {}).get(currency, [])
            )
            prev_ending = prev_ending - net_flow
            ending_balances[(months[i - 1] if i > 0 else ym, currency)] = prev_ending if i > 0 else None

        # Walk forward
        prev_ending = rbc_endings[anchor_ym][currency]
        ending_balances[(anchor_ym, currency)] = prev_ending
        for i in range(anchor_idx + 1, len(months)):
            ym = months[i]
            net_flow = sum(
                (t.amount_cad or Decimal('0'))
                for t in tx_by_month.get(ym, {}).get(currency, [])
            )
            prev_ending = prev_ending + net_flow
            ending_balances[(ym, currency)] = prev_ending

    # --- Generate report ---
    lines = []
    lines.append(f"# {account.account_id} — {account.name}\n")
    lines.append(f"*Generated {date.today().strftime('%Y-%m-%d')}*\n")

    prev_ending_report = {c: None for c in currencies}

    for ym in months:
        year, month_num = ym
        label = month_label(ym)
        to_date = month_end(year, month_num)

        lines.append(f"\n---\n")
        lines.append(f"## {label}\n")

        for currency in sorted(currencies):
            txs = tx_by_month.get(ym, {}).get(currency, [])
            net_flow = sum((t.amount_cad or Decimal('0')) for t in txs)
            opening = prev_ending_report[currency]
            ending = (opening + net_flow) if opening is not None else None
            rbc_end = rbc_endings.get(ym, {}).get(currency)

            # Holdings at month-end (deduplicated)
            holdings = []
            mv_total = Decimal('0')
            for symbol, cur in sorted(symbols):
                if cur != currency:
                    continue
                ph = get_current_position(account, to_date, symbol, currency)
                if ph:
                    holdings.append(ph)
                    mv_total += ph.market_value or Decimal('0')

            lines.append(f"\n### {currency}\n")

            # Opening
            if opening is not None:
                lines.append(f"**Opening balance:** {fmt(opening)}")
            else:
                lines.append(f"**Opening balance:** *unknown*")

            # Activities
            if txs:
                lines.append(f"\n**Transactions ({len(txs)}):**\n")
                lines.append("| Date | Symbol | Type | Qty | Price | Amount ({currency}) |")
                lines.append("|------|--------|------|-----|-------|---------------------|")
                for t in txs:
                    lines.append(
                        f"| {t.date} | {t.symbol or '-'} | {t.type} "
                        f"| {fmt(t.quantity) if t.quantity else '-'} "
                        f"| {fmt(t.price) if t.price else '-'} "
                        f"| {fmt(t.amount) if t.amount else '-'} |"
                    )
                lines.append(f"\n**Net flow:** {fmt(net_flow)}")

            # Holdings
            if holdings:
                lines.append(f"\n**Holdings at {to_date} ({len(holdings)} positions):**\n")
                lines.append("| Symbol | Qty | Price | Mkt Value | Cost Basis |")
                lines.append("|--------|-----|-------|-----------|------------|")
                for ph in holdings:
                    lines.append(
                        f"| {ph.symbol} | {fmt(ph.quantity)} | {fmt(ph.market_price)} "
                        f"| {fmt(ph.market_value)} | {fmt(ph.total_cost_basis)} |"
                    )
                lines.append(f"\n**Total MV:** {fmt(mv_total)}")

            # Ending
            if ending is not None:
                lines.append(f"\n**Ending (computed):** {fmt(ending)}")
            if rbc_end is not None:
                diff = abs((ending or Decimal('0')) - rbc_end)
                ok = '' if diff <= Decimal('5') else ' ⚠️'
                lines.append(f"**Ending (RBC):** {fmt(rbc_end)}{ok}")
                if ending is not None:
                    lines.append(f"**Diff:** {fmt(diff)}")

            prev_ending_report[currency] = ending

    # Write
    safe = account.account_id.replace(' ', '_')
    path = os.path.join(output_dir, f'monthly_statement_{safe}.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f"  {safe}: {len(months)} months")


def main():
    filter_id = None
    if '--account' in sys.argv:
        i = sys.argv.index('--account') + 1
        if i < len(sys.argv):
            filter_id = sys.argv[i]

    accounts = Account.objects.all().order_by('account_id')
    if filter_id:
        accounts = accounts.filter(account_id=filter_id)

    print(f"Generating for {accounts.count()} accounts...")
    for a in accounts:
        generate_for_account(a)
    print("Done.")


if __name__ == '__main__':
    main()
