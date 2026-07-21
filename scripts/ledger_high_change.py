"""Generate detailed ledger-style .md report for months with >5% price change.

For each qualifying month, shows:
  Opening Balance (from previous statement's end balance)
  Opening Holdings (from previous statement)
  Transaction Ledger (current statement)
  Closing Holdings (from current statement)
  Closing Balance (from current statement's end balance)

RBC PDF convention:
  - BOUGHT with debit = money OUT (purchase cost)
  - SOLD with debit = money IN (proceeds)
  - DIVIDEND with credit = money IN
  - DIV REIN with debit = reinvestment (shares, not cash flow)

Usage:
    python scripts/ledger_high_change.py --account 90000001 --currency CAD [--threshold 5]
"""

import argparse
import os
import re
import sys
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


def resolve_symbol(tx, all_symbols):
    """Extract symbol from transaction, trying DB field first then description."""
    sym = tx.symbol.strip().upper() if tx.symbol and tx.symbol.strip() else ''
    if sym:
        return sym
    desc_upper = tx.description.upper()
    for s in sorted(all_symbols, key=lambda x: -len(x)):
        if s in desc_upper:
            return s
    return '-'


def main():
    parser = argparse.ArgumentParser(description='Ledger report for high-change months')
    parser.add_argument('--account', default='90000001', help='Account ID')
    parser.add_argument('--currency', default='CAD', help='Currency')
    parser.add_argument('--start-date', default=None, help='Start date filter (YYYY-MM-DD)')
    parser.add_argument('--threshold', type=float, default=5.0, help='Min price change %% of opening to include (default: 5)')
    args = parser.parse_args()

    account = Account.objects.get(account_id=args.account)
    currency = args.currency

    # Load all statements ordered by date
    all_stmts = list(RbcMonthly.objects.filter(
        account=account,
        currency=currency,
    ).order_by('statement_date'))

    # Filter by start date
    if args.start_date:
        from datetime import datetime
        sd = datetime.strptime(args.start_date, '%Y-%m-%d').date()
        stmts = [s for s in all_stmts if s.statement_date >= sd]
    else:
        stmts = all_stmts

    # Collect all symbols across all holdings
    all_symbols = set()
    for stmt in all_stmts:
        for h in stmt.holdings.all():
            s = h.symbol.strip().upper() if h.symbol and h.symbol.strip() else h.security.strip()[:10].upper()
            if s:
                all_symbols.add(s)

    # Build index: statement -> previous statement
    stmt_by_index = {i: stmts[i] for i in range(len(stmts))}

    # Find high-change months using price change % (unrealized gain/loss as % of opening)
    high_change_months = []
    for idx, stmt in enumerate(stmts):
        begin = stmt.begin_total_market_value or Decimal('0')
        end = stmt.end_total_market_value or Decimal('0')

        # Net cash flow calculation:
        #   SOLD debit = proceeds (money IN from selling)
        #   BOUGHT debit = cost (money OUT from buying)
        #   DIVIDEND credit = income (money IN)
        #   WIRE TFR credit = transfer IN
        #   BUY (DRIP) debit = reinvestment (NOT cash flow — shares purchased with dividend)
        #   DIV REIN = reinvestment (NOT cash flow)
        #   INTEREST debit for RBF2010 = reinvestment (NOT cash flow)
        net_inflow = Decimal('0')
        net_outflow = Decimal('0')
        for tx in stmt.transactions.all():
            atype = tx.activity_type.upper()
            if atype == 'SOLD':
                net_inflow += (tx.debit - tx.credit)
            elif atype == 'DIVIDEND':
                net_inflow += tx.credit
            elif atype == 'WIRE TFR':
                net_inflow += tx.credit
            elif atype == 'BOUGHT':
                net_outflow += tx.debit
            # BUY (DRIP), DIV REIN, INTEREST = reinvestments, skip
            # ADJUST, RTC = adjustments, skip

        net_cash_flow = net_inflow - net_outflow
        bal_change = end - begin
        price_change = bal_change - net_cash_flow

        if begin > 0:
            pct = float(price_change / begin * 100)
        else:
            pct = 0

        if abs(pct) >= args.threshold:
            high_change_months.append((idx, stmt, pct, net_cash_flow, bal_change, price_change, net_inflow, net_outflow))

    lines = []
    lines.append(f"# Detailed Ledger Report: {account.account_id} ({account.name}) — {currency}\n")
    lines.append(f"Threshold: price change >{args.threshold}% of opening balance\n")
    if args.start_date:
        lines.append(f"Start date filter: {args.start_date}\n")
    lines.append(f"Statements analyzed: {len(stmts)} ({month_label(stmts[0].statement_date)} to {month_label(stmts[-1].statement_date)})")
    lines.append(f"High-change months found: {len(high_change_months)}\n")

    lines.append("**% Change** = Price change (unrealized gain/loss) as % of opening balance.\n")
    lines.append("Large values often indicate portfolio rebalancing where transaction debits/credits\n")
    lines.append("don't match the balance change because the PDF records buy/sell amounts as debits\n")
    lines.append("regardless of direction.\n")
    lines.append("---\n")

    # Summary table
    lines.append("## Summary of High-Change Months\n")
    lines.append(f"{'Month':>10} | {'Opening':>14} | {'Closing':>14} | {'Net In':>14} | {'Net Out':>14} | {'Net Flow':>14} | {'Bal Δ':>14} | {'Price Δ':>14} | % Price")
    lines.append(f"{'-'*10}-|{'-'*14}-|{'-'*14}-|{'-'*14}-|{'-'*14}-|{'-'*14}-|{'-'*14}-|{'-'*14}-|{'-'*7}")
    for idx, stmt, pct, net_cf, bal_chg, price_chg, net_in, net_out in sorted(high_change_months, key=lambda x: x[1].statement_date):
        begin = stmt.begin_total_market_value or Decimal('0')
        end = stmt.end_total_market_value or Decimal('0')
        lines.append(
            f"{month_label(stmt.statement_date):>10} | {fmt(begin):>14} | {fmt(end):>14} | {fmt(net_in):>14} | {fmt(net_out):>14} | {fmt(net_cf):>14} | {fmt(bal_chg):>14} | {fmt(price_chg):>14} | {pct:>6.1f}%"
        )
    lines.append("")

    # Detailed ledger for each month
    lines.append("---\n")
    for idx, stmt, pct, net_cf, bal_chg, price_chg, net_in, net_out in sorted(high_change_months, key=lambda x: x[1].statement_date):
        begin = stmt.begin_total_market_value or Decimal('0')
        end = stmt.end_total_market_value or Decimal('0')
        # Find the previous statement in all_stmts (not just the filtered list)
        all_idx = all_stmts.index(stmt) if stmt in all_stmts else -1
        prev_stmt = all_stmts[all_idx - 1] if all_idx > 0 else None

        lines.append(f"## {month_label(stmt.statement_date)} — Price change: {pct:+.1f}%\n")

        # Opening holdings from PREVIOUS statement
        if prev_stmt:
            prev_holdings = list(prev_stmt.holdings.all())
            if prev_holdings:
                lines.append(f"### Opening Holdings (from {month_label(prev_stmt.statement_date)})\n")
                lines.append(f"{'Symbol':>10} | {'Qty':>10} | {'Book Cost':>12} | {'Price':>10} | {'Mkt Value':>14}")
                lines.append(f"{'-'*10}-|{'-'*10}-|{'-'*12}-|{'-'*10}-|{'-'*14}")
                total_mv = Decimal('0')
                for h in sorted(prev_holdings, key=lambda x: x.symbol or x.security):
                    sym = h.symbol.strip().upper() if h.symbol and h.symbol.strip() else h.security.strip()[:10].upper()
                    mv = h.market_value or Decimal('0')
                    total_mv += mv
                    lines.append(
                        f"{sym:>10} | {float(h.quantity):>10.2f} | {fmt(h.book_cost):>12} | {fmt(h.market_price):>10} | {fmt(mv):>14}"
                    )
                lines.append(f"{'TOTAL':>10} | {'':>10} | {'':>12} | {'':>10} | {fmt(total_mv):>14}")
                lines.append("")
        else:
            lines.append("### Opening Holdings\n")
            lines.append("*(No previous statement available)*\n")

        # Transaction Ledger
        lines.append(f"### Transaction Ledger\n")
        lines.append(f"**Opening Balance: {fmt(begin)}**\n")

        txs = list(stmt.transactions.all())
        if txs:
            lines.append(f"{'Type':>10} | {'Symbol':>10} | {'Qty':>10} | {'Debit':>12} | {'Credit':>12} | {'Direction':>12} | Description")
            lines.append(f"{'-'*10}-|{'-'*10}-|{'-'*10}-|{'-'*12}-|{'-'*12}-|{'-'*12}-|{'-'*40}")

            total_buy_debit = Decimal('0')
            total_sell_debit = Decimal('0')
            total_div_credit = Decimal('0')

            for tx in txs:
                sym = resolve_symbol(tx, all_symbols)
                qty = float(tx.quantity)
                atype = tx.activity_type.upper()

                # Determine direction
                if atype == 'BOUGHT':
                    direction = 'OUT'
                    total_buy_debit += tx.debit
                elif atype == 'SOLD':
                    direction = 'IN'
                    total_sell_debit += (tx.debit - tx.credit)
                elif atype == 'DIVIDEND':
                    direction = 'IN' if tx.credit > 0 else '-'
                    if tx.credit > 0:
                        total_div_credit += tx.credit
                elif atype == 'BUY':
                    desc = tx.description.upper()
                    if 'DIST' in desc or 'REINVEST' in desc:
                        direction = 'REINVEST'
                    else:
                        direction = 'OUT'
                        total_buy_debit += tx.debit
                elif atype == 'DIV REIN':
                    direction = 'REINVEST'
                elif atype == 'INTEREST':
                    desc = tx.description.upper()
                    if '2010' in desc or 'REINVEST' in desc:
                        direction = 'REINVEST'
                    else:
                        direction = 'OUT'
                        total_buy_debit += tx.debit
                elif atype == 'WIRE TFR':
                    direction = 'IN'
                else:
                    direction = '-'

                lines.append(
                    f"{tx.activity_type:>10} | {sym:>10} | {qty:>10.2f} | {fmt(tx.debit):>12} | {fmt(tx.credit):>12} | {direction:>12} | {tx.description[:55]}"
                )

            lines.append(f"{'-'*10}-|{'-'*10}-|{'-'*10}-|{'-'*12}-|{'-'*12}-|{'-'*12}-|{'-'*40}")
            lines.append(f"\n**Transaction Summary:**")
            lines.append(f"- Sell proceeds (SOLD): {fmt(total_sell_debit)} (money IN)")
            lines.append(f"- Buy cost (BOUGHT/BUY): {fmt(total_buy_debit)} (money OUT)")
            lines.append(f"- Dividends received: {fmt(total_div_credit)} (money IN)")
            lines.append(f"- Net cash flow: {fmt(net_cf)}")
        else:
            lines.append("*(No transactions this period)*\n")

        # Closing holdings from CURRENT statement
        curr_holdings = list(stmt.holdings.all())
        if curr_holdings:
            lines.append(f"\n### Closing Holdings ({month_label(stmt.statement_date)})\n")
            lines.append(f"{'Symbol':>10} | {'Qty':>10} | {'Book Cost':>12} | {'Price':>10} | {'Mkt Value':>14}")
            lines.append(f"{'-'*10}-|{'-'*10}-|{'-'*12}-|{'-'*10}-|{'-'*14}")
            total_mv = Decimal('0')
            for h in sorted(curr_holdings, key=lambda x: x.symbol or x.security):
                sym = h.symbol.strip().upper() if h.symbol and h.symbol.strip() else h.security.strip()[:10].upper()
                mv = h.market_value or Decimal('0')
                total_mv += mv
                lines.append(
                    f"{sym:>10} | {float(h.quantity):>10.2f} | {fmt(h.book_cost):>12} | {fmt(h.market_price):>10} | {fmt(mv):>14}"
                )
            lines.append(f"{'TOTAL':>10} | {'':>10} | {'':>12} | {'':>10} | {fmt(total_mv):>14}")
        else:
            lines.append("\n### Closing Holdings\n*(No holdings data for this statement)*\n")

        # Reconciliation
        lines.append(f"\n### Reconciliation\n")
        lines.append(f"- Opening Balance (begin_total_market_value): {fmt(begin)}")
        lines.append(f"- Net Cash Flow (IN - OUT): {fmt(net_cf)}")
        lines.append(f"- Expected Closing (Opening + Net Flow): {fmt(begin + net_cf)}")
        lines.append(f"- Actual Closing (end_total_market_value): {fmt(end)}")
        lines.append(f"- Unrealized Gain/Loss (Price Δ): {fmt(price_chg)} ({pct:+.1f}% of opening)\n")
        lines.append("---\n")

    # Write report
    output_dir = Path(__file__).parent.parent / "docs" / "reconciliation"
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"ledger_high_change_{args.account}_{args.currency}.md"
    path = output_dir / filename
    path.write_text("\n".join(lines), encoding='utf-8')
    print(f"Written: {path}")
    print(f"\nTotal high-change months (price Δ > {args.threshold}% of opening): {len(high_change_months)}")
    for idx, stmt, pct, net_cf, bal_chg, price_chg, net_in, net_out in sorted(high_change_months, key=lambda x: x[1].statement_date):
        print(f"  {month_label(stmt.statement_date)}: price Δ {pct:+.1f}% | net flow {float(net_cf):+.0f} | bal change {float(bal_chg):+.0f}")


if __name__ == '__main__':
    main()
