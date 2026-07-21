"""Generate detailed ledger-style .md report for months with >5% price change.

For each qualifying month, shows:
  Opening Balance (from begin_total_market_value)
  Opening Holdings (from previous statement's holdings)
  Transaction Ledger (current statement — raw DB data with correct interpretation)
  Closing Holdings (from current statement's holdings)
  Closing Balance (from end_total_market_value)

RBC PDF convention (parsed by VL model):
  - SOLD/SELL: debit or credit = proceeds (money in from sale)
  - BOUGHT/BUY: debit or credit = cost (money out for purchase)
  - DIVIDEND/DIV: credit = cash dividend
  - DIV REIN: dividend reinvested (no net cash flow — div -> shares)
  - INTEREST: debit or credit = interest amount
  - TRANSFER/TFR OUT: money in/out
  - DRIP (BUY/SOLD with "DIST ON X SHS"): reinvestment, qty field unreliable
  - OPENING BALANCE: not a real transaction

Parser quirks:
  - Sometimes `price` = per-share price, sometimes `price` = total amount
  - Sometimes the amount is in `debit`, sometimes in `credit`
  - For DRIP, `quantity` often = existing holdings count, actual new shares in `price` or fractional

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

# Pattern to extract "DIST ON X SHS"
DIST_RE = re.compile(r'DIST ON\s+([\d,]+\.?\d*)\s+SHS', re.IGNORECASE)


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
    """Extract symbol from transaction."""
    sym = tx.symbol.strip().upper() if tx.symbol and tx.symbol.strip() else ''
    if sym:
        return sym
    desc_upper = tx.description.upper()
    for s in sorted(all_symbols, key=lambda x: -len(x)):
        if s in desc_upper:
            return s
    return '-'


def get_amount(tx):
    """Return the monetary amount of a transaction (always positive magnitude)."""
    debit = tx.debit or Decimal('0')
    credit = tx.credit or Decimal('0')
    return max(debit, credit)


def interpret_transaction(tx, all_symbols):
    """Return (label, cash_flow_effect).
    
    cash_flow_effect: positive = cash in, negative = cash out, zero = no cash effect
    """
    atype = tx.activity_type.upper().strip()
    desc = tx.description.strip()
    sym = resolve_symbol(tx, all_symbols)
    
    debit = tx.debit or Decimal('0')
    credit = tx.credit or Decimal('0')
    qty = tx.quantity or Decimal('0')
    price = tx.price or Decimal('0')
    amount = get_amount(tx)
    
    # --- Skip non-transactions ---
    if atype in ('OPENING BALANCE', 'RTC', 'ADJUST'):
        return (f"{atype}: {fmt(amount)} [{desc[:40]}]", 0)
    
    # --- Check for DRIP ("DIST ON X SHS" in description) ---
    drip_match = DIST_RE.search(desc)
    if drip_match:
        existing_shares = float(drip_match.group(1).replace(',', ''))
        # For DRIP, the cash amount is the dividend being reinvested (no net cash flow)
        if credit > 0:
            return (f"DRIP {sym} on {existing_shares:.0f} shs, reinvested {fmt(credit)}", 0)
        elif debit > 0:
            return (f"DRIP {sym} on {existing_shares:.0f} shs, reinvested {fmt(debit)}", 0)
        return (f"DRIP {sym} on {existing_shares:.0f} shs (no amt)", 0)
    
    # --- DIV REIN (Dividend Reinvestment) ---
    if atype == 'DIV REIN':
        # Reinvestment: dividend automatically bought as shares. No net cash flow.
        return (f"DIV REIN {sym} {fmt(amount)}", 0)
    
    # --- DIVIDEND / DIV (Cash dividend) ---
    if atype in ('DIVIDEND', 'DIV'):
        cash = credit if credit > 0 else debit
        return (f"Dividend {sym} {fmt(cash)}", cash)
    
    # --- INTEREST ---
    if atype == 'INTEREST':
        return (f"Interest {sym} {fmt(amount)}", amount)
    
    # --- SOLD / SELL ---
    if atype in ('SELL', 'SOLD'):
        return (f"Sold {qty:.0f} {sym}, proceeds {fmt(amount)}", amount)
    
    # --- BOUGHT / BUY ---
    if atype in ('BUY', 'BOUGHT'):
        return (f"Bought {qty:.0f} {sym}, cost {fmt(amount)}", -amount)
    
    # --- TRANSFER / TFR OUT / WIRE TFR / CONTRIB ---
    if atype in ('TRANSFER', 'TFR OUT', 'TRANSFER OUT', 'WIRE TFR'):
        if credit > debit:
            return (f"Transfer IN {fmt(credit)}", credit)
        elif debit > credit:
            return (f"Transfer OUT {fmt(debit)}", -debit)
        return (f"{atype} {fmt(amount)}", 0)
    
    if atype == 'CONTRIB':
        return (f"Contribution {fmt(amount)}", amount)
    
    # --- DIST (Distribution) ---
    if atype == 'DIST':
        return (f"Distribution {sym} {fmt(amount)}", amount)
    
    # --- Fallback ---
    net = credit - debit
    return (f"{atype} {sym} D:{fmt(debit)} C:{fmt(credit)} [{desc[:40]}]", net)


def compute_cash_flow(stmt):
    """Compute net cash flow for a statement period.
    
    Returns net cash flow (positive = money in, negative = money out).
    """
    total = Decimal('0')
    for tx in stmt.transactions.all():
        _, cf = interpret_transaction(tx, set())
        total += cf
    return total


def main():
    parser = argparse.ArgumentParser(description='Ledger report for high-change months')
    parser.add_argument('--account', default='90000001', help='Account ID')
    parser.add_argument('--currency', default='CAD', help='Currency')
    parser.add_argument('--start-date', default=None, help='Start date filter (YYYY-MM-DD)')
    parser.add_argument('--threshold', type=float, default=5.0, help='Min price change %% of opening to include')
    args = parser.parse_args()

    account = Account.objects.get(account_id=args.account)
    currency = args.currency

    # Load all statements ordered by date
    all_stmts = list(RbcMonthly.objects.filter(
        account=account,
        currency=currency,
    ).order_by('statement_date'))

    if not all_stmts:
        print(f"No statements found for {account.account_id} {currency}")
        return

    # Filter by start date
    if args.start_date:
        from datetime import datetime
        sd = datetime.strptime(args.start_date, '%Y-%m-%d').date()
        stmts = [s for s in all_stmts if s.statement_date >= sd]
    else:
        stmts = all_stmts

    # Collect all symbols
    all_symbols = set()
    for stmt in all_stmts:
        for h in stmt.holdings.all():
            s = h.symbol.strip().upper() if h.symbol and h.symbol.strip() else h.security.strip()[:10].upper()
            if s:
                all_symbols.add(s)

    # Find high-change months
    high_change_months = []
    for stmt in stmts:
        begin = stmt.begin_total_market_value or Decimal('0')
        end = stmt.end_total_market_value or Decimal('0')
        bal_change = end - begin
        
        net_cash = compute_cash_flow(stmt)
        price_change = bal_change - net_cash
        
        if begin > 0:
            pct = float(price_change / begin * 100)
        else:
            pct = 0
        
        if abs(pct) >= args.threshold:
            high_change_months.append((stmt, pct, bal_change, net_cash))

    lines = []
    lines.append(f"# Detailed Ledger Report: {account.account_id} ({account.name}) — {currency}\n")
    lines.append(f"Threshold: price change >{args.threshold}% of opening balance\n")
    if args.start_date:
        lines.append(f"Start date filter: {args.start_date}\n")
    lines.append(f"Statements analyzed: {len(stmts)} ({month_label(stmts[0].statement_date)} to {month_label(stmts[-1].statement_date)})")
    lines.append(f"High-change months found: {len(high_change_months)}\n")

    lines.append("---\n")

    # Summary table
    lines.append("## Summary of High-Change Months\n")
    lines.append(f"{'Month':>10} | {'Opening':>14} | {'Closing':>14} | {'Bal Change':>14} | {'Cash Flow':>14} | {'Price Chg':>14} | % Change")
    lines.append(f"{'-'*10}-|{'-'*14}-|{'-'*14}-|{'-'*14}-|{'-'*14}-|{'-'*14}-|{'-'*8}")
    for stmt, pct, bal_chg, net_cash in sorted(high_change_months, key=lambda x: x[0].statement_date):
        begin = stmt.begin_total_market_value or Decimal('0')
        end = stmt.end_total_market_value or Decimal('0')
        price_chg = bal_chg - net_cash
        lines.append(
            f"{month_label(stmt.statement_date):>10} | {fmt(begin):>14} | {fmt(end):>14} | {fmt(bal_chg):>14} | {fmt(net_cash):>14} | {fmt(price_chg):>14} | {pct:>7.1f}%"
        )
    lines.append("")

    # Detailed ledger for each high-change month
    lines.append("---\n")
    for stmt, pct, bal_chg, net_cash in sorted(high_change_months, key=lambda x: x[0].statement_date):
        begin = stmt.begin_total_market_value or Decimal('0')
        end = stmt.end_total_market_value or Decimal('0')
        price_chg = bal_chg - net_cash

        # Find previous statement (from ALL statements)
        all_idx = None
        for i, s in enumerate(all_stmts):
            if s.pk == stmt.pk:
                all_idx = i
                break
        prev_stmt = all_stmts[all_idx - 1] if all_idx and all_idx > 0 else None

        lines.append(f"## {month_label(stmt.statement_date)} — {pct:+.1f}%\n")

        # Opening holdings from PREVIOUS statement
        if prev_stmt:
            prev_holdings = list(prev_stmt.holdings.all())
            if prev_holdings:
                lines.append(f"### Opening Holdings (from {month_label(prev_stmt.statement_date)} statement)\n")
                lines.append(f"{'Symbol':>10} | {'Qty':>10} | {'Price':>10} | {'Mkt Value':>14}")
                lines.append(f"{'-'*10}-|{'-'*10}-|{'-'*10}-|{'-'*14}-")
                total_open_mv = Decimal('0')
                for h in sorted(prev_holdings, key=lambda x: x.symbol or x.security):
                    sym = h.symbol.strip().upper() if h.symbol and h.symbol.strip() else h.security.strip()[:10].upper()
                    mv = h.market_value or Decimal('0')
                    total_open_mv += mv
                    lines.append(
                        f"{sym:>10} | {float(h.quantity):>10.2f} | {fmt(h.market_price):>10} | {fmt(mv):>14}"
                    )
                lines.append(f"{'TOTAL':>10} | {'':>10} | {'':>10} | {fmt(total_open_mv):>14}")
                lines.append("")

        # Transaction Ledger
        lines.append(f"### Transaction Ledger\n")
        lines.append(f"**Opening Balance: {fmt(begin)}**\n")

        txs = list(stmt.transactions.all())
        if txs:
            lines.append(f"{'Date':>10} | {'Type':>12} | {'Debit':>12} | {'Credit':>12} | Interpretation")
            lines.append(f"{'-'*10}-|{'-'*12}-|{'-'*12}-|{'-'*12}-|{'-'*55}")

            for tx in txs:
                date_str = tx.date.strftime('%Y-%m-%d') if tx.date else 'N/A'
                label, _ = interpret_transaction(tx, all_symbols)
                lines.append(
                    f"{date_str:>10} | {tx.activity_type:>12} | {fmt(tx.debit):>12} | {fmt(tx.credit):>12} | {label}"
                )

            # Totals
            total_debit = sum(tx.debit or Decimal('0') for tx in txs)
            total_credit = sum(tx.credit or Decimal('0') for tx in txs)
            lines.append(f"{'':>10} | {'TOTAL':>12} | {fmt(total_debit):>12} | {fmt(total_credit):>12} |")
            lines.append("")
            lines.append(f"**Net Cash Flow: {fmt(net_cash)}**")
            lines.append(f"**Expected Closing (Opening + Cash Flow): {fmt(begin + net_cash)}**")
            lines.append(f"**Actual Closing: {fmt(end)}**")
            lines.append(f"**Unrealized Gain/Loss: {fmt(price_chg)}**\n")
        else:
            lines.append("*(No transactions this period)*\n")
            lines.append(f"**Unrealized Gain/Loss: {fmt(bal_chg)}**\n")

        # Closing holdings from CURRENT statement
        curr_holdings = list(stmt.holdings.all())
        if curr_holdings:
            lines.append(f"### Closing Holdings ({month_label(stmt.statement_date)} statement)\n")
            lines.append(f"{'Symbol':>10} | {'Qty':>10} | {'Price':>10} | {'Mkt Value':>14}")
            lines.append(f"{'-'*10}-|{'-'*10}-|{'-'*10}-|{'-'*14}-")
            total_close_mv = Decimal('0')
            for h in sorted(curr_holdings, key=lambda x: x.symbol or x.security):
                sym = h.symbol.strip().upper() if h.symbol and h.symbol.strip() else h.security.strip()[:10].upper()
                mv = h.market_value or Decimal('0')
                total_close_mv += mv
                lines.append(
                    f"{sym:>10} | {float(h.quantity):>10.2f} | {fmt(h.market_price):>10} | {fmt(mv):>14}"
                )
            lines.append(f"{'TOTAL':>10} | {'':>10} | {'':>10} | {fmt(total_close_mv):>14}")
            lines.append("")

        lines.append("---\n")

    # Write report
    output_dir = Path(__file__).parent.parent / "docs" / "reconciliation"
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"ledger_high_change_{args.account}_{args.currency}.md"
    path = output_dir / filename
    path.write_text("\n".join(lines), encoding='utf-8')
    print(f"Written: {path}")
    print(f"\nTotal high-change months: {len(high_change_months)}")
    for stmt, pct, bal_chg, net_cash in sorted(high_change_months, key=lambda x: x[0].statement_date):
        print(f"  {month_label(stmt.statement_date)}: {pct:+.1f}% (cash flow: {float(net_cash):+.0f})")


if __name__ == '__main__':
    main()
