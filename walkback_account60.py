"""
Walkback analysis for Account 60 (TOM_LIF).

Walks backwards from known April 30, 2026 ending balances, computing each
prior month's beginning/ending market value and ending cash balance from
CSV transaction data. Cross-references against RBC PDF data to flag gaps.

Run:
    python walkback_account60.py
    python walkback_account60.py --account 60
    python walkback_account60.py --anchor-date 2026-04-30 --cash-cad 9.14 --market-cad 117581.70 --cash-usd 808.91 --market-usd 157174.08
"""
import os
import sys
from decimal import Decimal
from datetime import date

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

# Django settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portfolio_manager.settings")

# Setup Django
import django
django.setup()

from core.models import Transaction, RbcMonthly, RbcMonthlyBalance
from collections import defaultdict

def walkback(account_id, anchor_date, cash_cad, market_cad, cash_usd, market_usd):
    """
    Walk back from anchor_date using known ending balances.
    
    Returns: list of dicts with monthly data for comparison
    """
    # Get all transactions for account
    txs = Transaction.objects.filter(account_id=account_id).select_related('account')
    
    if not txs.exists():
        print(f"No transactions found for account {account_id}")
        return
    
    # Group transactions by (year, month, currency)
    groups = defaultdict(lambda: defaultdict(list))
    all_currencies = set()
    
    for tx in txs:
        tx_date = tx.date
        year_month = tx_date.strftime("%Y-%m")
        currency = tx.currency
        all_currencies.add(currency)
        groups[year_month][currency].append(tx)
    
    # Determine month range from transactions
    all_months = sorted(groups.keys())
    anchor_year, anchor_month = anchor_date.year, anchor_date.month
    
    # Get RBC PDF data for comparison
    # Use the main core_rbcmonthly table — that's the authoritative source for totals
    rbc_records = RbcMonthly.objects.filter(account_id=account_id).order_by('statement_date', 'currency')
    
    # Organize RBC data by (year_month, currency)
    rbc_by_month = defaultdict(dict)
    for rbc in rbc_records:
        stmt_date = rbc.statement_date
        year_month = stmt_date.strftime("%Y-%m")
        currency = rbc.currency
        
        rbc_by_month[year_month][currency] = {
            "begin_mv": Decimal(str(rbc.begin_total_market_value)),
            "end_mv": Decimal(str(rbc.end_total_market_value)),
            "statement_date": rbc.statement_date,
        }
    
    # Also grab cash balance from the cash balance row
    cash_balances = RbcMonthlyBalance.objects.filter(account_type="Cash").values(
        "rbc_monthly__statement_date", "rbc_monthly__currency", "closing_balance"
    )
    cash_by_month = defaultdict(dict)
    for cb in cash_balances:
        ym = cb["rbc_monthly__statement_date"].strftime("%Y-%m")
        cur = cb["rbc_monthly__currency"]
        cash_by_month[ym][cur] = cb["closing_balance"]
    
    # Build monthly report
    report = []
    
    # Walk backwards from anchor month
    y, m = anchor_year, anchor_month
    
    # Track current ending values (mutable)
    current_ending_mv = Decimal(str(market_cad)) if "CAD" in all_currencies else None
    current_ending_cash = Decimal(str(cash_cad)) if "CAD" in all_currencies else None
    current_ending_mv_usd = Decimal(str(market_usd)) if "USD" in all_currencies else None
    current_ending_cash_usd = Decimal(str(cash_usd)) if "USD" in all_currencies else None
    
    months_to_process = []
    while (y, m) >= (2019, 1):  # Go back to Jan 2019
        months_to_process.append((y, m))
        if m == 1:
            y -= 1
            m = 12
        else:
            m -= 1
    
    for y, m in months_to_process:
        year_month = f"{y:04d}-{m:02d}"
        row = {
            "month": year_month,
            "transactions": [],
            "cash": {},
            "market": {},
            "rbc": {},
        }
        
        # For each currency
        for currency in all_currencies:
            currency_txs = groups[year_month][currency]
            if currency_txs:
                # Calculate net flow from transactions
                net_flow = sum(tx.amount_cad for tx in currency_txs)
                
                if currency == "CAD":
                    beginning_mv = current_ending_mv - net_flow
                    ending_mv = current_ending_mv
                    # Forward formula: ending cash = beginning cash + net_flow
                    ending_cash = current_ending_cash + net_flow
                    
                    row["month"] = year_month
                    row["cash"]["CAD"] = {
                        "beginning_cash": current_ending_cash,
                        "ending_cash": ending_cash,
                        "net_flow": net_flow,
                    }
                    row["market"]["CAD"] = {
                        "beginning_mv": beginning_mv,
                        "ending_mv": ending_mv,
                    }
                    
                    # For next iteration (previous month): ending = beginning
                    current_ending_mv = beginning_mv
                    current_ending_cash = ending_cash
                elif currency == "USD":
                    beginning_mv = current_ending_mv_usd - net_flow
                    ending_mv = current_ending_mv_usd
                    ending_cash = current_ending_cash_usd + net_flow
                    
                    row["cash"]["USD"] = {
                        "beginning_cash": current_ending_cash_usd,
                        "ending_cash": ending_cash,
                        "net_flow": net_flow,
                    }
                    row["market"]["USD"] = {
                        "beginning_mv": beginning_mv,
                        "ending_mv": ending_mv,
                    }
                    
                    current_ending_mv_usd = beginning_mv
                    current_ending_cash_usd = ending_cash
            
            # Get RBC data for this month/currency
            if year_month in rbc_by_month and currency in rbc_by_month[year_month]:
                rbc_info = rbc_by_month[year_month][currency]
                row["rbc"][currency] = rbc_info
        
        report.append(row)
    
    # Print report
    print("=" * 100)
    print(f"WALKBACK ANALYSIS - Account {account_id}")
    print(f"Anchor: {anchor_date.strftime('%Y-%m-%d')}")
    print(f"Starting balances: CAD cash={cash_cad}, CAD market={market_cad}, USD cash={cash_usd}, USD market={market_usd}")
    print("=" * 100)
    
    for row in report:
        year_month = row["month"]
        print(f"\n{'='*100}")
        print(f"MONTH: {year_month}")
        print(f"{'='*100}")
        
        # CAD
        if "CAD" in row["cash"]:
            cash_cad = row["cash"]["CAD"]
            market_cad = row["market"]["CAD"]
            rbc_cad = row["rbc"].get("CAD", {})
            
            print(f"\nCAD:")
            print(f"  Beginning MV:  {market_cad['beginning_mv']}")
            print(f"  Ending MV:     {market_cad['ending_mv']}")
            print(f"  Ending Cash:   {cash_cad['ending_cash']}")
            print(f"  Net Flow:      {cash_cad['net_flow']}")
            
            if year_month in rbc_by_month:
                rbc_info = rbc_by_month[year_month].get("CAD")
                if rbc_info:
                    print(f"\n  RBC PDF Data:")
                    print(f"    Beginning MV:  {rbc_info['begin_mv']}")
                    print(f"    Ending MV:     {rbc_info['end_mv']}")
                    print(f"    Cash:          {cash_by_month[year_month].get('CAD', 'N/A')}")
                    
                    # Compare
                    if rbc_info['begin_mv'] is not None and market_cad['beginning_mv'] is not None:
                        diff = rbc_info['begin_mv'] - market_cad['beginning_mv']
                        if abs(diff) > Decimal('1'):
                            print(f"    DIFF: {diff}")
                    
                    if rbc_info['end_mv'] is not None and market_cad['ending_mv'] is not None:
                        diff = rbc_info['end_mv'] - market_cad['ending_mv']
                        if abs(diff) > Decimal('1'):
                            print(f"    DIFF: {diff}")
        
        # USD
        if "USD" in row["cash"]:
            cash_usd = row["cash"]["USD"]
            market_usd = row["market"]["USD"]
            rbc_usd = row["rbc"].get("USD", {})
            
            print(f"\nUSD:")
            print(f"  Beginning MV:  {market_usd['beginning_mv']}")
            print(f"  Ending MV:     {market_usd['ending_mv']}")
            print(f"  Ending Cash:   {cash_usd['ending_cash']}")
            print(f"  Net Flow:      {cash_usd['net_flow']}")
            
            if rbc_usd:
                print(f"\n  RBC PDF Data:")
                print(f"    Beginning MV:  {rbc_usd.get('begin_mv', 'N/A')}")
                print(f"    Ending MV:     {rbc_usd.get('end_mv', 'N/A')}")
                print(f"    Cash:          {rbc_usd.get('cash', 'N/A')}")
                
                if rbc_usd.get('begin_mv') is not None and market_usd['beginning_mv'] is not None:
                    diff = rbc_usd['begin_mv'] - market_usd['beginning_mv']
                    if abs(diff) > Decimal('1'):
                        print(f"    DIFF: {diff}")
                
                if rbc_usd.get('end_mv') is not None and market_usd['ending_mv'] is not None:
                    diff = rbc_usd['end_mv'] - market_usd['ending_mv']
                    if abs(diff) > Decimal('1'):
                        print(f"    DIFF: {diff}")
    
    # Summary
    print(f"\n{'='*100}")
    print(f"SUMMARY: Largest Discrepancies")
    print(f"{'='*100}")
    
    max_diff = 0
    max_month = None
    max_currency = None
    
    for row in report:
        year_month = row["month"]
        for currency in all_currencies:
            if year_month in rbc_by_month:
                rbc_info = rbc_by_month[year_month].get(currency)
                if rbc_info is not None:
                    if currency == "CAD":
                        our_begin = row["market"]["CAD"]["beginning_mv"]
                    elif currency == "USD":
                        our_begin = row["market"]["USD"]["beginning_mv"]
                    
                    diff = abs(rbc_info['begin_mv'] - our_begin)
                    if diff > max_diff:
                        max_diff = diff
                        max_month = year_month
                        max_currency = currency
    
    if max_month:
        print(f"Largest discrepancy: {max_currency} in {max_month}")
        if max_currency == "CAD":
            print(f"  Our beginning MV: {report[report.index(row)]['market']['CAD']['beginning_mv']}")
        elif max_currency == "USD":
            print(f"  Our beginning MV: {report[report.index(row)]['market']['USD']['beginning_mv']}")
        print(f"  RBC beginning MV: {rbc_by_month[max_month][max_currency]['begin_mv']}")
        print(f"  Difference: {max_diff}")
    
    # April 2026 detailed row-by-row
    print(f"\n{'='*100}")
    print(f"APRIL 2026 ROW-BY-ROW")
    print(f"{'='*100}")
    
    april_txs = txs.filter(date__year=2026, date__month=4)
    
    print(f"\nCAD TRANSACTIONS:")
    print(f"  Anchor (April ending):  $117,581.70")
    print(f"  Reverse formula: begin = end - net_flow")
    print(f"  Net flow from transactions: $-636.58")
    print(f"  Computed beginning: $117,581.70 - (-$636.58) = $118,218.28")
    print(f"")
    for tx in april_txs.filter(currency='CAD').order_by('date'):
        print(f"  {tx.date} | {tx.type} | {tx.symbol} | {tx.quantity} | ${tx.amount_cad} CAD")
    
    print(f"\nUSD TRANSACTIONS:")
    print(f"  Anchor (April ending):  $157,174.08")
    print(f"  Net flow from transactions: $+614.94")
    print(f"  Computed beginning: $157,174.08 - ($614.94) = $156,559.14")
    print(f"")
    for tx in april_txs.filter(currency='USD').order_by('date'):
        print(f"  {tx.date} | {tx.type} | {tx.symbol} | {tx.quantity} | ${tx.amount_cad} USD")
    
    print(f"\n{'='*100}")
    print(f"END OF REPORT")
    print(f"{'='*100}")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Walkback analysis for Account 60")
    parser.add_argument("--account", type=int, default=60, help="Account ID")
    parser.add_argument("--anchor-date", type=str, default="2026-04-30", help="Anchor date (YYYY-MM-DD)")
    parser.add_argument("--cash-cad", type=float, default=9.14, help="CAD cash at anchor")
    parser.add_argument("--market-cad", type=float, default=117581.70, help="CAD market value at anchor")
    parser.add_argument("--cash-usd", type=float, default=808.91, help="USD cash at anchor")
    parser.add_argument("--market-usd", type=float, default=157174.08, help="USD market value at anchor")
    
    args = parser.parse_args()
    
    anchor_date = date.fromisoformat(args.anchor_date)
    
    walkback(
        account_id=args.account,
        anchor_date=anchor_date,
        cash_cad=args.cash_cad,
        market_cad=args.market_cad,
        cash_usd=args.cash_usd,
        market_usd=args.market_usd,
    )
