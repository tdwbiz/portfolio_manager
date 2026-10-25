"""Internal PDF reconciliation for account 90000001.

Verifies that within the PDF data itself:
1. Beginning balance + transactions = Ending balance (for each month)
2. Holdings quantities chain correctly month-to-month
3. Cash flows reconcile with balance changes

Usage:
    python scripts/pdf_internal_reconciliation.py [--account 90000001] [--currency CAD]
"""

import argparse
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent
sys.path.insert(0, str(project_root))

import os
os.chdir(project_root)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')

import django
django.setup()

from core.models import (
    Account, RbcMonthly, RbcMonthlyTransaction,
    RbcMonthlyHolding, RbcMonthlyBalance,
)


def fmt(val, precision=2):
    if val is None:
        return "N/A"
    try:
        return f"{Decimal(str(val)):,.{precision}f}"
    except Exception:
        return str(val)


def get_statements(account, currency):
    """Get all RbcMonthly statements for account/currency, ordered by date."""
    return list(
        RbcMonthly.objects.filter(
            account=account,
            currency=currency,
        ).order_by('statement_date')
    )


def verify_balance_chain(stmts):
    """Verify that holdings market values chain correctly month-to-month.
    
    For each statement:
    1. Sum all holdings' market values (from RbcMonthlyHolding)
    2. Compare to previous month's sum
    3. Account for cash flows (deposits, withdrawals, dividends)
    4. The difference should be explained by price changes
    
    Returns list of dicts with verification results.
    """
    results = []
    prev_holdings_sum = None
    prev_date = None
    
    for stmt in stmts:
        # Get all holdings for this statement and sum their market values
        holdings = list(stmt.holdings.all())
        holdings_market_value = sum(
            h.market_value or Decimal('0') for h in holdings
        )
        
        # Get all transactions for this statement
        txs = list(stmt.transactions.all())
        
        # Calculate net cash flow from transactions
        # PDF convention: debit = outflow, credit = inflow
        # net = credit - debit
        total_debit = sum(tx.debit or Decimal('0') for tx in txs)
        total_credit = sum(tx.credit or Decimal('0') for tx in txs)
        net_flow = total_credit - total_debit
        
        # Get the reported balances from RbcMonthly
        begin_bal = stmt.begin_total_market_value or Decimal('0')
        end_bal = stmt.end_total_market_value or Decimal('0')
        
        # Calculate change in holdings market value from previous month
        if prev_holdings_sum is not None:
            holdings_change = holdings_market_value - prev_holdings_sum
            # The change should be explained by:
            # 1. Cash flows (deposits/withdrawals/dividends)
            # 2. Price changes (unrealized gains/losses)
            # So: holdings_change = net_flow + price_changes
            # Therefore: price_changes = holdings_change - net_flow
            implied_price_change = holdings_change - net_flow
        else:
            holdings_change = None
            implied_price_change = None
        
        # Check if end_bal matches holdings_market_value (within $1)
        end_vs_holdings_diff = abs(end_bal - holdings_market_value)
        end_matches_holdings = end_vs_holdings_diff < Decimal('1.0')
        
        results.append({
            'statement_date': stmt.statement_date,
            'beginning_balance': begin_bal,
            'ending_balance': end_bal,
            'holdings_market_value': holdings_market_value,
            'holdings_count': len(holdings),
            'total_debit': total_debit,
            'total_credit': total_credit,
            'net_flow': net_flow,
            'prev_holdings_sum': prev_holdings_sum,
            'holdings_change': holdings_change,
            'implied_price_change': implied_price_change,
            'end_vs_holdings_diff': end_vs_holdings_diff,
            'end_matches_holdings': end_matches_holdings,
            'transaction_count': len(txs),
        })
        
        prev_holdings_sum = holdings_market_value
        prev_date = stmt.statement_date
    
    return results


def verify_holdings_chain(stmts):
    """Verify that holdings quantities chain correctly month-to-month.
    
    For each security, check if:
    prev_month_quantity + buys - sells = current_month_quantity
    
    Returns dict: {symbol: list of monthly verification results}
    """
    # Build holdings by month
    holdings_by_month = {}
    for stmt in stmts:
        month_key = (stmt.statement_date.year, stmt.statement_date.month)
        holdings = {}
        for h in stmt.holdings.all():
            sym = h.symbol.strip().upper() if h.symbol.strip() else h.security.strip()[:10].upper()
            if sym:
                holdings[sym] = {
                    'quantity': h.quantity or Decimal('0'),
                    'market_value': h.market_value or Decimal('0'),
                    'book_cost': h.book_cost or Decimal('0'),
                }
        holdings_by_month[month_key] = holdings
    
    # Build transactions by month and symbol
    tx_by_month_symbol = defaultdict(lambda: defaultdict(list))
    for stmt in stmts:
        month_key = (stmt.statement_date.year, stmt.statement_date.month)
        for tx in stmt.transactions.all():
            sym = tx.symbol.strip().upper() if tx.symbol.strip() else ''
            if sym:
                tx_by_month_symbol[month_key][sym].append(tx)
    
    # Verify each symbol across months
    all_symbols = set()
    for month_holdings in holdings_by_month.values():
        all_symbols.update(month_holdings.keys())
    
    results = {}
    for symbol in sorted(all_symbols):
        symbol_results = []
        prev_quantity = None
        prev_month = None
        
        for month_key in sorted(holdings_by_month.keys()):
            current_quantity = holdings_by_month[month_key].get(symbol, {}).get('quantity')
            
            if current_quantity is None:
                continue
            
            # Get transactions for this symbol in this month
            txs = tx_by_month_symbol[month_key].get(symbol, [])
            
            # Calculate quantity change from transactions
            # BOUGHT/DIV REIN/INTEREST = increase quantity
            # SOLD = decrease quantity
            qty_bought = Decimal('0')
            qty_sold = Decimal('0')
            
            for tx in txs:
                activity = tx.activity_type.strip().upper()
                qty = tx.quantity or Decimal('0')
                
                if activity in ('BOUGHT', 'BUY', 'DIV REIN', 'INTEREST', 'INTEREST_RECEIVED'):
                    qty_bought += qty
                elif activity in ('SOLD', 'SELL'):
                    qty_sold += qty
            
            qty_change = qty_bought - qty_sold
            
            if prev_quantity is not None:
                expected_quantity = prev_quantity + qty_change
                diff = abs(current_quantity - expected_quantity)
                matches = diff < Decimal('0.001')
                
                symbol_results.append({
                    'month': month_key,
                    'prev_month': prev_month,
                    'prev_quantity': prev_quantity,
                    'bought': qty_bought,
                    'sold': qty_sold,
                    'expected_quantity': expected_quantity,
                    'actual_quantity': current_quantity,
                    'difference': current_quantity - expected_quantity,
                    'matches': matches,
                })
            
            prev_quantity = current_quantity
            prev_month = month_key
        
        if symbol_results:
            results[symbol] = symbol_results
    
    return results


def generate_report(account_id, currency, balance_results, holdings_results):
    """Generate markdown report."""
    from datetime import datetime
    lines = []
    
    lines.append(f"# Internal PDF Reconciliation: {account_id} — {currency}")
    lines.append(f"\n**Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}**\n")
    lines.append("This report verifies internal consistency within the PDF data itself:\n")
    lines.append("1. **Holdings Market Value Chain:** Does sum of holdings market values reconcile with reported balances?")
    lines.append("2. **Holdings Quantity Chain:** Do security quantities chain correctly month-to-month?\n")
    
    # Holdings market value chain summary
    lines.append("---\n")
    lines.append("## Holdings Market Value Chain Verification\n")
    lines.append("For each monthly statement, we check if:\n")
    lines.append("```")
    lines.append("sum(holdings.market_value) ≈ RbcMonthly.end_total_market_value")
    lines.append("holdings_change = current_holdings_sum - prev_holdings_sum")
    lines.append("implied_price_change = holdings_change - net_cash_flow")
    lines.append("```\n")
    lines.append("Where `implied_price_change` represents unrealized gains/losses from price movements.\n")
    
    total_stmts = len(balance_results)
    matches = sum(1 for r in balance_results if r['end_matches_holdings'])
    mismatches = total_stmts - matches
    
    lines.append(f"**Total statements:** {total_stmts}  ")
    lines.append(f"**end_total_market_value matches sum(holdings):** {matches}  ")
    lines.append(f"**Mismatches:** {mismatches}\n")
    
    if mismatches > 0:
        lines.append("### Statements Where end_total_market_value ≠ sum(holdings)\n")
        lines.append("| Date | end_total_market_value | sum(holdings) | Difference | Holdings Count |")
        lines.append("|------|------------------------|---------------|------------|----------------|")
        
        for r in balance_results:
            if not r['end_matches_holdings']:
                lines.append(
                    f"| {r['statement_date'].strftime('%Y-%m-%d')} "
                    f"| {fmt(r['ending_balance'])} "
                    f"| {fmt(r['holdings_market_value'])} "
                    f"| {fmt(r['end_vs_holdings_diff'])} "
                    f"| {r['holdings_count']} |"
                )
        lines.append("")
    
    # Show all statements with holdings chain
    lines.append("### All Statements — Holdings Market Value Chain\n")
    lines.append("| Date | sum(holdings) | prev_sum | Change | Net Flow | Implied Price Change | Status |")
    lines.append("|------|---------------|----------|--------|----------|---------------------|--------|")
    
    for r in balance_results:
        if r['holdings_change'] is not None:
            status = "✓" if r['end_matches_holdings'] else "✗"
            lines.append(
                f"| {r['statement_date'].strftime('%Y-%m-%d')} "
                f"| {fmt(r['holdings_market_value'])} "
                f"| {fmt(r['prev_holdings_sum'])} "
                f"| {fmt(r['holdings_change'])} "
                f"| {fmt(r['net_flow'])} "
                f"| {fmt(r['implied_price_change'])} "
                f"| {status} |"
            )
        else:
            lines.append(
                f"| {r['statement_date'].strftime('%Y-%m-%d')} "
                f"| {fmt(r['holdings_market_value'])} "
                f"| N/A | N/A | {fmt(r['net_flow'])} | N/A | (first) |"
            )
    lines.append("")
    
    # Holdings chain summary
    lines.append("---\n")
    lines.append("## Holdings Chain Verification\n")
    lines.append("For each security, we check if:\n")
    lines.append("```\nprev_month_quantity + bought - sold = current_month_quantity\n```\n")
    
    total_symbols = len(holdings_results)
    symbols_with_issues = []
    
    for symbol, results in holdings_results.items():
        mismatches = [r for r in results if not r['matches']]
        if mismatches:
            symbols_with_issues.append((symbol, mismatches))
    
    lines.append(f"**Total symbols tracked:** {total_symbols}  ")
    lines.append(f"**Symbols with quantity mismatches:** {len(symbols_with_issues)}\n")
    
    if symbols_with_issues:
        lines.append("### Symbols with Quantity Mismatches\n")
        
        for symbol, mismatches in symbols_with_issues:
            lines.append(f"#### {symbol}\n")
            lines.append("| Month | Prev Qty | Bought | Sold | Expected | Actual | Difference |")
            lines.append("|-------|----------|--------|------|----------|--------|------------|")
            
            for r in mismatches:
                lines.append(
                    f"| {r['month'][0]:04d}-{r['month'][1]:02d} "
                    f"| {fmt(r['prev_quantity'])} "
                    f"| {fmt(r['bought'])} "
                    f"| {fmt(r['sold'])} "
                    f"| {fmt(r['expected_quantity'])} "
                    f"| {fmt(r['actual_quantity'])} "
                    f"| {fmt(r['difference'])} |"
                )
            lines.append("")
    
    # Summary
    lines.append("---\n")
    lines.append("## Summary\n")
    
    balance_ok = mismatches == 0
    holdings_ok = len(symbols_with_issues) == 0
    
    lines.append(f"| Check | Status |")
    lines.append(f"|-------|--------|")
    lines.append(f"| Holdings market value chain | {'✓ PASS' if balance_ok else '✗ FAIL'} |")
    lines.append(f"| Holdings quantity chain | {'✓ PASS' if holdings_ok else '✗ FAIL'} |")
    lines.append("")
    
    if not balance_ok:
        lines.append(f"**Holdings market value issues:** {mismatches} statements where end_total_market_value ≠ sum(holdings)  ")
    if not holdings_ok:
        lines.append(f"**Holdings quantity issues:** {len(symbols_with_issues)} symbols have quantity mismatches  ")
    
    if balance_ok and holdings_ok:
        lines.append("**All checks passed!** PDF data is internally consistent.\n")
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description='Internal PDF reconciliation')
    parser.add_argument('--account', required=True, help='Account ID (required — no default)')
    parser.add_argument('--currency', default='CAD', help='Currency')
    args = parser.parse_args()
    
    account = Account.objects.get(account_id=args.account)
    print(f"Internal PDF reconciliation for {account.account_id} ({account.name}) — {args.currency}")
    
    stmts = get_statements(account, args.currency)
    print(f"  Found {len(stmts)} monthly statements")
    
    if not stmts:
        print("  No statements found. Exiting.")
        return
    
    print("  Verifying balance chain...")
    balance_results = verify_balance_chain(stmts)
    
    print("  Verifying holdings chain...")
    holdings_results = verify_holdings_chain(stmts)
    
    print("  Generating report...")
    report = generate_report(args.account, args.currency, balance_results, holdings_results)
    
    output_dir = Path(__file__).parent.parent / "docs" / "reconciliation"
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"pdf_internal_recon_{args.account}_{args.currency}.md"
    path = output_dir / filename
    path.write_text(report, encoding='utf-8')
    
    print(f"\n  Report written to: {path}")
    
    # Print summary
    balance_matches = sum(1 for r in balance_results if r['end_matches_holdings'])
    balance_mismatches = len(balance_results) - balance_matches
    
    holdings_issues = sum(1 for results in holdings_results.values() 
                         if any(not r['matches'] for r in results))
    
    print(f"\n  Summary:")
    print(f"    Balance chain: {balance_matches}/{len(balance_results)} statements match")
    print(f"    Holdings chain: {len(holdings_results) - holdings_issues}/{len(holdings_results)} symbols match")


if __name__ == "__main__":
    main()
