"""Generate detailed ledger-style .md report for months with >5% balance change.

For each qualifying month, shows:
  Opening Balance | Transactions (Dt/Ct) | Closing Balance

Usage:
    python scripts/ledger_high_change.py --account 90000001 --currency CAD [--threshold 5]
"""

import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent
sys.path.insert(0, str(project_root))

os.chdir(project_root)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')

import django
django.setup()

from core.models import Account, RbcMonthly, RbcMonthlyTransaction, RbcMonthlyHolding


def fmt(val, precision=2):
    if val is None:
        return "N/A"
    try:
        return f"{Decimal(str(val)):,.{precision}f}"
    except Exception:
        return str(val)


def month_label(d):
    return f"{d.year:04d}-{d.month:02d}"


def main():
    parser = argparse.ArgumentParser(description='Ledger report for high-change months')
    parser.add_argument('--account', default='90000001', help='Account ID')
    parser.add_argument('--currency', default='CAD', help='Currency')
    parser.add_argument('--start-date', default=None, help='Start date filter (YYYY-MM-DD)')
    parser.add_argument('--threshold', type=float, default=5.0, help='Min %% balance change to include (default: 5)')
    args = parser.parse_args()

    account = Account.objects.get(account_id=args.account)
    currency = args.currency

    # Load statements
    all_stmts = list(RbcMonthly.objects.filter(
        account=account,
        currency=currency,
    ).order_by('statement_date'))

    if args.start_date:
        sd = datetime.strptime(args.start_date, '%Y-%m-%d').date()
        stmts = [s for s in all_stmts if s.statement_date >= sd]
    else:
        stmts = all_stmts

    # Find high-change months — use price change % (unrealized gain/loss as % of opening)
    # This matches the integrity report's % Change column:
    #   price_change = bal_change - net_cash_flow
    #   pct = price_change / begin * 100
    high_change_months = []
    for stmt in stmts:
        begin = stmt.begin_total_market_value or Decimal('0')
        end = stmt.end_total_market_value or Decimal('0')
        txs = stmt.transactions.all()
        net_flow = sum(tx.credit - tx.debit for tx in txs)
        bal_change = end - begin
        price_change = bal_change - net_flow
        if begin > 0:
            pct = float(price_change / begin * 100)
        else:
            pct = 0
        if abs(pct) >= args.threshold:
            high_change_months.append((stmt, pct, net_flow, bal_change, price_change))

    lines = []
    lines.append(f"# Detailed Ledger Report: {account.account_id} ({account.name}) — {currency}\n")
    lines.append(f"Threshold: >{args.threshold}% balance change\n")
    if args.start_date:
        lines.append(f"Start date filter: {args.start_date}\n")
    lines.append(f"Statements analyzed: {len(stmts)} ({month_label(stmts[0].statement_date)} to {month_label(stmts[-1].statement_date)})")
    lines.append(f"High-change months found: {len(high_change_months)}\n")
    lines.append("**% Change** = Price change (unrealized gain/loss) as % of opening balance.\n")
    lines.append("This isolates months where market price movement alone exceeded the threshold,\n")
    lines.append("excluding cash flow effects. Large positive values often indicate portfolio\n")
    lines.append("rebalancing where transaction debits/credits don't match the balance change.\n")
    lines.append("---\n")

    # Summary table
    lines.append("## Summary of High-Change Months\n")
    lines.append(f"{'Month':>10} | {'Opening':>14} | {'Closing':>14} | {'Net Flow':>14} | {'Bal Change':>14} | {'Price Δ':>14} | % Price")
    lines.append(f"{'-'*10}-|{'-'*14}-|{'-'*14}-|{'-'*14}-|{'-'*14}-|{'-'*14}-|{'-'*7}")
    for stmt, pct, net_flow, bal_change, price_change in sorted(high_change_months, key=lambda x: x[0].statement_date):
        begin = stmt.begin_total_market_value or Decimal('0')
        end = stmt.end_total_market_value or Decimal('0')
        lines.append(
            f"{month_label(stmt.statement_date):>10} | {fmt(begin):>14} | {fmt(end):>14} | {fmt(net_flow):>14} | {fmt(bal_change):>14} | {fmt(price_change):>14} | {pct:>6.1f}%"
        )
    lines.append("")

    # Detailed ledger for each month
    lines.append("---\n")
    for stmt, pct, net_flow, bal_change, price_change in sorted(high_change_months, key=lambda x: x[0].statement_date):
        begin = stmt.begin_total_market_value or Decimal('0')
        end = stmt.end_total_market_value or Decimal('0')

        lines.append(f"## {month_label(stmt.statement_date)} — Price change: {pct:+.1f}%\n")

        # Holdings snapshot (opening)
        holdings = list(stmt.holdings.all())
        if holdings:
            lines.append("### Opening Holdings\n")
            lines.append(f"{'Symbol':>10} | {'Qty':>10} | {'Book Cost':>12} | {'Price':>10} | {'Mkt Value':>14}")
            lines.append(f"{'-'*10}-|{'-'*10}-|{'-'*12}-|{'-'*10}-|{'-'*14}")
            total_mv = Decimal('0')
            for h in sorted(holdings, key=lambda x: x.symbol or x.security):
                sym = h.symbol.strip().upper() if h.symbol.strip() else h.security.strip()[:10].upper()
                mv = h.market_value or Decimal('0')
                total_mv += mv
                lines.append(
                    f"{sym:>10} | {float(h.quantity):>10.2f} | {fmt(h.book_cost):>12} | {fmt(h.market_price):>10} | {fmt(mv):>14}"
                )
            lines.append(f"{'TOTAL':>10} | {'':>10} | {'':>12} | {'':>10} | {fmt(total_mv):>14}")
            lines.append("")

        # Transaction ledger
        txs = list(stmt.transactions.all())
        if txs:
            lines.append("### Transaction Ledger\n")
            lines.append(f"**Opening Balance: {fmt(begin)}**\n")

            # Group transactions by type for cleaner display
            # Show: Activity Type | Symbol | Description | Debit | Credit
            lines.append(f"{'Type':>10} | {'Symbol':>10} | {'Debit':>12} | {'Credit':>12} | Description")
            lines.append(f"{'-'*10}-|{'-'*10}-|{'-'*12}-|{'-'*12}-|{'-'*30}")

            total_debit = Decimal('0')
            total_credit = Decimal('0')
            for tx in txs:
                sym = tx.symbol.strip().upper() if tx.symbol.strip() else ''
                # Try to extract symbol from description if empty
                if not sym:
                    desc_upper = tx.description.upper()
                    # Check known symbols
                    known_symbols = ['RBF2010', 'VDY', 'VFV', 'XDIV', 'BMO', 'BNS', 'CM', 'MFC', 'RY', 'SLF', 'TD']
                    for s in known_symbols:
                        if s in desc_upper:
                            sym = s
                            break
                    if not sym:
                        sym = '-'

                lines.append(
                    f"{tx.activity_type:>10} | {sym:>10} | {fmt(tx.debit):>12} | {fmt(tx.credit):>12} | {tx.description[:70]}"
                )
                total_debit += tx.debit
                total_credit += tx.credit

            lines.append(f"{'-'*10}-|{'-'*10}-|{'-'*12}-|{'-'*12}-|{'-'*30}")
            lines.append(f"{'TOTAL':>10} | {'':>10} | {fmt(total_debit):>12} | {fmt(total_credit):>12} |")
            lines.append(f"\nNet Cash Flow: {fmt(total_credit - total_debit)} (Credit - Debit)")

            # Verify closing balance
            expected_end = begin + (total_credit - total_debit)
            actual_end = stmt.end_total_market_value or Decimal('0')
            unrealized = actual_end - expected_end
            lines.append(f"\n**Reconciliation:**")
            lines.append(f"- Opening Balance: {fmt(begin)}")
            lines.append(f"- Net Cash Flow (Cr - Dr): {fmt(total_credit - total_debit)}")
            lines.append(f"- Expected Closing (Opening + Net Flow): {fmt(expected_end)}")
            lines.append(f"- Actual Closing (from PDF): {fmt(actual_end)}")
            lines.append(f"- Unrealized Gain/Loss (Price Δ): {fmt(unrealized)} ({pct:+.1f}% of opening)")
        else:
            lines.append("### No transactions this period\n")
            lines.append(f"**Opening Balance: {fmt(begin)}**")
            lines.append(f"**Closing Balance: {fmt(end)}**")
            lines.append(f"Change (market movement): {fmt(end - begin)}\n")

        # Holdings snapshot (closing) — from the NEXT statement's holdings if available
        stmt_idx = stmts.index(stmt)
        if stmt_idx + 1 < len(stmts):
            next_stmt = stmts[stmt_idx + 1]
            next_holdings = list(next_stmt.holdings.all())
            if next_holdings:
                lines.append("### Closing Holdings (from next statement)\n")
                lines.append(f"{'Symbol':>10} | {'Qty':>10} | {'Book Cost':>12} | {'Price':>10} | {'Mkt Value':>14}")
                lines.append(f"{'-'*10}-|{'-'*10}-|{'-'*12}-|{'-'*10}-|{'-'*14}")
                total_mv = Decimal('0')
                for h in sorted(next_holdings, key=lambda x: x.symbol or x.security):
                    sym = h.symbol.strip().upper() if h.symbol.strip() else h.security.strip()[:10].upper()
                    mv = h.market_value or Decimal('0')
                    total_mv += mv
                    lines.append(
                        f"{sym:>10} | {float(h.quantity):>10.2f} | {fmt(h.book_cost):>12} | {fmt(h.market_price):>10} | {fmt(mv):>14}"
                    )
                lines.append(f"{'TOTAL':>10} | {'':>10} | {'':>12} | {'':>10} | {fmt(total_mv):>14}")
                lines.append(f"\n**Note:** Closing holdings are from {month_label(next_stmt.statement_date)} statement.\n")

        lines.append("---\n")

    # Write report
    output_dir = Path(__file__).parent.parent / "docs" / "reconciliation"
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"ledger_high_change_{args.account}_{args.currency}.md"
    path = output_dir / filename
    path.write_text("\n".join(lines), encoding='utf-8')
    print(f"Written: {path}")
    print(f"\nTotal high-change months (price Δ > {args.threshold}% of opening): {len(high_change_months)}")
    for stmt, pct, net_flow, bal_change, price_change in sorted(high_change_months, key=lambda x: x[0].statement_date):
        print(f"  {month_label(stmt.statement_date)}: price Δ {pct:+.1f}% | net flow {float(net_flow):+.0f} | bal change {float(bal_change):+.0f}")


if __name__ == '__main__':
    main()
