"""Generate monthly accounting ledger for each account.

For each (account, currency), produces a .md file in docs/reconciliation/
that shows the complete monthly ledger:
- Opening Balances (from previous month's closing or Holdings snapshot)
- Transactions during the month (from CSV activity)
- Holdings at month-end (from Holdings CSV snapshots)
- Market values at month-end (from RBC PDF when available)
- Closing Balances

Algorithm:
1. Determine all months covered by data for each account
2. For each month, compute opening balance from previous month's close
3. Apply transactions to update share quantities
4. Match holdings snapshot data for quantities and market values
5. Match RBC PDF data for verification
6. Flag discrepancies
"""
import os
import sys
import django
import logging
from datetime import date, timedelta
from decimal import Decimal
from collections import defaultdict

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
sys.path.insert(0, r'C:\AI-DevTeam\projects\portfolio_manager')
django.setup()

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

import django.db.models as models
from core.models import (
    Account, PositionHistory, Transaction,
    RbcMonthlyHolding, RbcMonthly, RbcMonthlyBalance, AccountCurrency
)

OUTPUT_DIR = r'C:\AI-DevTeam\projects\portfolio_manager\docs\reconciliation'

# Only RBC accounts with holdings data
RBC_ACCOUNT_IDS = [
    '90000004', '90000006', '90000007', '90000001',
    '90000002', '90000008', '90000005', '90000003',
]


def get_months_for_account(account):
    """Get all months that have data for this account."""
    months = set()
    
    # From PositionHistory snapshots
    for ph in PositionHistory.objects.filter(account=account):
        months.add(ph.date_from.replace(day=1))
    
    # From RbcMonthly statements
    for rm in RbcMonthly.objects.filter(account=account):
        months.add(rm.statement_date.replace(day=1))
    
    # From Transactions
    for tx in Transaction.objects.filter(account=account):
        months.add(tx.date.replace(day=1))
    
    return sorted(months)


def get_last_day_of_month(month_start: date) -> date:
    """Get the last day of a month."""
    if month_start.month == 12:
        return month_start.replace(day=28) + timedelta(days=4)
    else:
        return (month_start.replace(month=month_start.month + 1, day=1) - timedelta(days=1))


def get_open_positions_at_month(account, month_start):
    """Get all open positions at the start of a month.
    
    A position is open at month_start if date_from <= month_start and 
    (date_to is None or date_to >= month_start).
    """
    return list(PositionHistory.objects.filter(
        account=account,
        date_from__lte=month_start,
        date_to__isnull=True
    ).filter(
        models.Q(date_to__isnull=True) | models.Q(date_to__gte=month_start)
    ).order_by('symbol'))


def get_transactions_in_month(account, month_start):
    """Get all transactions in a month."""
    month_end = get_last_day_of_month(month_start)
    return list(Transaction.objects.filter(
        account=account,
        date__gte=month_start,
        date__lte=month_end,
    ).order_by('date'))


def get_holdings_active_at_month(account, month_start):
    """Get all holdings that were active during a specific month.
    
    A position is active if date_from <= month_end AND (date_to is None OR date_to >= month_start).
    Returns the most recent snapshot for each (symbol, currency) that covers this month.
    """
    month_end = get_last_day_of_month(month_start)
    
    # Find all positions that were active during this month
    active = PositionHistory.objects.filter(
        account=account,
        date_from__lte=month_end,
    ).filter(
        models.Q(date_to__isnull=True) | models.Q(date_to__gte=month_start)
    ).order_by('symbol', 'currency', '-date_from')
    
    # Group by (symbol, currency) and take the first (most recent date_from)
    result = {}
    for ph in active:
        key = (ph.symbol, ph.currency)
        if key not in result:
            result[key] = ph
    
    return result


def get_rbc_holding_for_month(account, month_start, symbol, currency):
    """Get RBC PDF holding data for a specific month."""
    try:
        rbc_monthly = RbcMonthly.objects.get(
            account=account,
            statement_date=month_start,
            currency=currency
        )
        holding = RbcMonthlyHolding.objects.filter(
            rbc_monthly=rbc_monthly,
            symbol=symbol,
            currency=currency
        ).first()
        return holding
    except RbcMonthly.DoesNotExist:
        return None


def get_rbc_end_value_for_month(account, month_start, currency):
    """Get RBC end_total_market_value for a month."""
    try:
        rbc = RbcMonthly.objects.get(
            account=account,
            statement_date=month_start,
            currency=currency
        )
        return rbc.end_total_market_value
    except RbcMonthly.DoesNotExist:
        return None


def get_rbc_begin_value_for_month(account, month_start, currency):
    """Get RBC begin_total_market_value for a month."""
    try:
        rbc = RbcMonthly.objects.get(
            account=account,
            statement_date=month_start,
            currency=currency
        )
        return rbc.begin_total_market_value
    except RbcMonthly.DoesNotExist:
        return None


def generate_ledger_for_account(account):
    """Generate a complete monthly ledger .md file for an account."""
    months = get_months_for_account(account)
    
    if not months:
        return
    
    # Get currencies for this account
    currencies = list(PositionHistory.objects.filter(
        account=account
    ).values_list('currency').distinct())
    currencies = [c[0] for c in currencies]
    
    filename = f"ledger_{account.account_id}.md"
    path = os.path.join(OUTPUT_DIR, filename)
    
    with open(path, 'w') as f:
        f.write(f"# Monthly Ledger — {account.name} ({account.account_id})\n\n")
        f.write(f"Currencies: {', '.join(currencies)}\n\n")
        f.write(f"Period: {months[0].strftime('%Y-%m')} to {months[-1].strftime('%Y-%m')} "
                f"({len(months)} months)\n\n")
        f.write("---\n\n")
        
        # Track previous month's closing balances
        prev_closing = {}
        
        # Track previous month's closing holdings
        prev_closing_holdings = {}  # (symbol, currency) -> PositionHistory data
        
        for month in months:
            month_end = get_last_day_of_month(month)
            f.write(f"## {month.strftime('%Y-%m')}\n\n")
            
            for currency in currencies:
                f.write(f"### {currency}\n\n")
                
                # Opening balance from previous month's closing
                if prev_closing_holdings:
                    opening = {sym: data for (sym, cur), data in prev_closing_holdings.items() 
                               if cur == currency}
                    if opening:
                        f.write(f"**Opening Balances:**\n\n")
                        f.write("| Symbol | Quantity | Cost Basis | Market Value |\n")
                        f.write("|--------|----------|------------|-------------|\n")
                        total_mv = Decimal('0')
                        for sym, data in sorted(opening.items()):
                            mv = data.get('market_value', Decimal('0'))
                            total_mv += mv
                            f.write(f"| {sym} | {data['quantity']} | "
                                    f"{data['total_cost_basis']} | {mv:.2f} |\n")
                        f.write(f"| **Total** | | | **{total_mv:.2f}** |\n\n")
                
                # Transactions
                txs = get_transactions_in_month(account, month)
                currency_txs = [tx for tx in txs if tx.currency == currency]
                
                if currency_txs:
                    f.write(f"**Transactions ({len(currency_txs)}):**\n\n")
                    f.write("| Date | Type | Symbol | Qty | Price | Amount | Amount CAD |\n")
                    f.write("|------|------|--------|-----|-------|--------|-----------|\n")
                    for tx in currency_txs:
                        f.write(f"| {tx.date} | {tx.type} | {tx.symbol} | "
                                f"{tx.quantity} | {tx.price} | {tx.amount} | {tx.amount_cad} |\n")
                    f.write("\n")
                
                # Holdings at month end (active positions during this month)
                all_holdings = get_holdings_active_at_month(account, month)
                currency_holdings = {k: v for k, v in all_holdings.items() if k[1] == currency}
                
                if currency_holdings:
                    f.write(f"**Holdings at Month End ({len(currency_holdings)}):**\n\n")
                    f.write("| Symbol | Qty | Cost Basis | CSV MV | RBC Qty | RBC MV | Status |\n")
                    f.write("|--------|-----|----------|--------|---------|--------|--------|\n")
                    
                    total_csv_mv = Decimal('0')
                    total_rbc_mv = Decimal('0')
                    
                    for sym, holding in sorted(currency_holdings.items()):
                        rbc = get_rbc_holding_for_month(
                            account, month, sym, currency
                        )
                        
                        mv = holding.market_value
                        total_csv_mv += mv
                        
                        if rbc:
                            rbc_mv = rbc.market_value
                            rbc_qty = rbc.quantity
                            total_rbc_mv += rbc_mv
                            
                            # Check quantity discrepancy
                            qty_diff = abs(holding.quantity - rbc_qty)
                            if qty_diff > Decimal('0.01'):
                                status = f"⚠️ Qty diff"
                            else:
                                status = "✓ Matched"
                        else:
                            rbc_mv = Decimal('0')
                            rbc_qty = Decimal('0')
                            status = "No RBC"
                        
                        f.write(f"| {sym[0]} | {holding.quantity} | "
                                f"{holding.total_cost_basis:.2f} | {mv:.2f} | "
                                f"{rbc_qty} | {rbc_mv:.2f} | {status} |\n")
                    
                    f.write(f"| **Total** | | | **{total_csv_mv:.2f}** | | **{total_rbc_mv:.2f}** | |\n")
                    
                    rbc_end = get_rbc_end_value_for_month(account, month, currency)
                    if rbc_end:
                        f.write(f"\nRBC end_total_market_value: **{rbc_end:.2f}**\n")
                        diff = abs(total_csv_mv - rbc_end)
                        if diff > Decimal('1.00'):
                            f.write(f"> ⚠️ CSV holdings total (${total_csv_mv:.2f}) vs RBC total (${rbc_end:.2f}): diff ${diff:.2f}\n")
                    f.write("\n")
                
                # Update closing balance for next month's opening
                prev_closing_holdings.clear()
                for (sym, cur), h in all_holdings.items():
                    prev_closing_holdings[(sym, cur)] = {
                        'quantity': h.quantity,
                        'total_cost_basis': h.total_cost_basis,
                        'market_value': h.market_value,
                    }
                
                f.write("---\n\n")
    
    print(f"Generated: {path}")
    return path


def main():
    logger.info("Generating monthly accounting ledgers...")
    
    for account in Account.objects.filter(account_id__in=RBC_ACCOUNT_IDS):
        try:
            generate_ledger_for_account(account)
        except Exception as e:
            logger.error(f"Error processing {account.account_id}: {e}")
            import traceback
            traceback.print_exc()
    
    logger.info("Done.")


if __name__ == '__main__':
    main()
