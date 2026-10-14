"""Generate detailed ledger-style .md report for months with >5% price change.

RBC PDF Transaction Convention (CORRECT):
  - The PDF has columns: DATE | ACTIVITY | DESCRIPTION | QTY | RATE | DEBIT | CREDIT
  - "DIST ON X SHS" in description = CASH DIVIDEND (money IN). Amount = qty × rate.
  - INTEREST = RBF2010/RBF2014 savings interest. Shares increase by qty. Cash = qty × $10.
  - DIVIDEND = Cash dividend received. Credit column has amount.
  - DIV REIN = Dividend used to buy shares. Has REINV@C$XX showing purchase price.
  - BUY/BOUGHT = Purchase of shares. Debit = cost.
  - SOLD/SELL = Sale of shares. Debit or credit = proceeds.
  
  Parser bugs:
  - "DIST ON X SHS" is often labeled as BUY instead of DIST
  - Opening cash balance sometimes merged into first transaction's credit field
  - quantity field for DIST entries may be existing holdings count OR per-share dividend
  
  Cash Flow:
  - Positive = cash coming into the account (sell proceeds, dividends)
  - Negative = cash leaving (buy cost, transfers out)
  - DIV REIN = 0 (dividend immediately reinvested as shares)
  - INTEREST = 0 (interest immediately reinvested as RBF shares)

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
from django.conf import settings

from core.models import Account, RbcMonthly

# Patterns
DIST_RE = re.compile(r'DIST ON\s+([\d,]+\.?\d*)\s+SHS', re.IGNORECASE)
REINV_RE = re.compile(r'REINV@C?\$?([\d,]+\.?\d*)', re.IGNORECASE)


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
    """Extract symbol from transaction description."""
    sym = tx.symbol.strip().upper() if tx.symbol and tx.symbol.strip() else ''
    if sym:
        return sym
    desc_upper = tx.description.upper()
    for s in sorted(all_symbols, key=lambda x: -len(x)):
        if s in desc_upper:
            return s
    return '-'


def interpret_transaction(tx, all_symbols):
    """Return (label, cash_flow).
    
    cash_flow: positive = cash in, negative = cash out, 0 = no net cash effect
    """
    atype = tx.activity_type.upper().strip()
    desc = tx.description.strip()
    sym = resolve_symbol(tx, all_symbols)
    
    debit = tx.debit or Decimal('0')
    credit = tx.credit or Decimal('0')
    qty = tx.quantity or Decimal('0')
    price = tx.price or Decimal('0')
    
    # --- Non-cash entries ---
    if atype in ('OPENING BALANCE',):
        return (f"Opening Balance: {fmt(max(debit, credit))}", 0)
    
    if atype in ('RTC', 'ADJUST'):
        amount = max(debit, credit)
        if amount > 0:
            return (f"Book adj {sym}: {fmt(amount)}", 0)
        return (f"Book adj {sym}", 0)
    
    # --- Check for "DIST ON X SHS" — this is a cash dividend ---
    drip_match = DIST_RE.search(desc)
    if drip_match:
        existing_shares = float(drip_match.group(1).replace(',', ''))
        
        # Check if this is a DIV REIN (reinvestment)
        reinv_match = REINV_RE.search(desc)
        if reinv_match and atype == 'DIV REIN':
            # True reinvestment — dividend used to buy shares
            cost = debit if debit > 0 else credit
            return (f"DIV REIN {sym}: {fmt(cost)} reinvested", 0)
        
        # DIST (cash dividend) — money IN
        # Parser bug: When qty == existing_shares (large number), the debit field
        # contains the MARKET VALUE of holdings, NOT the dividend amount.
        # When qty is small (< 10), it's the per-share dividend rate, and
        # debit = qty * existing_shares = actual dividend amount.
        if existing_shares > 10:
            # "Fake quantity" — qty = existing holdings count
            # When price > $5, it's the market price, not the dividend rate.
            # In this case, debit = market value of holdings (NOT the dividend).
            if price > 5:
                # Price is market price — debit is market value, NOT dividend
                return (f"DIST {sym}: {existing_shares:.0f} shs (div amt unknown, parser stored MV={fmt(debit)})", 0)
            
            # Price is per-share dividend rate (typically $0.10-$2.00)
            # Actual dividend = price × existing_shares
            # But the debit field may contain the opening cash balance (parser bug)
            actual_div = Decimal(str(price)) * Decimal(str(existing_shares))
            
            # If debit ≈ actual_div, use debit; otherwise use credit or compute actual
            if abs(float(debit - actual_div)) < 1.0:
                return (f"DIST {sym}: {fmt(debit)} ({existing_shares:.0f} shs × {fmt(price)}/share)", debit)
            elif credit > 0 and abs(float(credit - actual_div)) < 1.0:
                return (f"DIST {sym}: {fmt(credit)} ({existing_shares:.0f} shs × {fmt(price)}/share)", credit)
            else:
                # Use computed actual dividend
                return (f"DIST {sym}: {fmt(actual_div)} ({existing_shares:.0f} shs × {fmt(price)}/share)", actual_div)
        else:
            # Small qty — this IS the per-share dividend rate
            cash = debit if debit > 0 else credit
            return (f"DIST {sym}: {fmt(cash)} ({float(qty):.3f}/share)", cash)
    
    # --- DIV REIN (Dividend Reinvestment) ---
    if atype == 'DIV REIN':
        reinv_match = REINV_RE.search(desc)
        if reinv_match:
            cost = debit if debit > 0 else credit
            return (f"DIV REIN {sym}: bought shares @ {fmt(Decimal(reinv_match.group(1).replace(',', '')))}, cost {fmt(cost)}", 0)
        cost = max(debit, credit)
        return (f"DIV REIN {sym}: {fmt(cost)}", 0)
    
    # --- DIVIDEND / DIV / DIST (Cash dividend) ---
    if atype in ('DIVIDEND', 'DIV', 'DIST'):
        cash = credit if credit > 0 else debit
        return (f"Div {sym}: {fmt(cash)}", cash)
    
    # --- INTEREST (RBF2010/RBF2014) ---
    if atype == 'INTEREST':
        # Interest is qty × $10 (REINVEST @ $10.00)
        # The credit field may contain the opening cash balance (parser bug)
        interest_cash = qty * Decimal('10')
        return (f"Interest {sym}: {fmt(interest_cash)} ({float(qty):.3f} shs × $10)", 0)
    
    # --- BUY / BOUGHT (Purchase of shares) ---
    if atype in ('BUY', 'BOUGHT'):
        cost = debit if debit > 0 else credit
        return (f"Buy {sym}: {fmt(cost)}", -cost)
    
    # --- SOLD / SELL (Proceeds from sale) ---
    if atype in ('SELL', 'SOLD'):
        proceeds = debit if debit > 0 else credit
        return (f"Sold {qty:.0f} {sym}: {fmt(proceeds)}", proceeds)
    
    # --- TRANSFER / TFR OUT / WIRE TFR / CONTRIB ---
    if atype in ('TRANSFER', 'TFR OUT', 'TRANSFER OUT', 'WIRE TFR', 'CONTRIB'):
        if credit > debit:
            return (f"Transfer IN: {fmt(credit)}", credit)
        elif debit > credit:
            return (f"Transfer OUT: {fmt(debit)}", -debit)
        return (f"{atype}: {fmt(max(debit, credit))}", 0)
    
    # --- Fallback ---
    net = credit - debit
    return (f"{atype} {sym} D:{fmt(debit)} C:{fmt(credit)} [{desc[:40]}]", net)


def compute_cash_flow(stmt, all_symbols):
    """Compute net cash flow for a statement period."""
    total = Decimal('0')
    for tx in stmt.transactions.all():
        _, cf = interpret_transaction(tx, all_symbols)
        total += cf
    return total


def main():
    parser = argparse.ArgumentParser(description='Ledger report for high-change months')
    parser.add_argument('--account', default=settings.RBC_DEFAULT_ACCOUNT, help='Account ID')
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

    # Filter out annual report summary rows that were mistakenly stored in RbcMonthly
    def _is_annual_summary(stmt):
        """Return True if this RbcMonthly row is an annual report summary."""
        annual_keywords = [
            'deposits and transfers-in',
            'net investment return',
            'deposit of $',
            'withdrawal of $',
            'withdrawals and transfers-out',
        ]
        for tx in stmt.transactions.all():
            desc = (tx.description or '').lower()
            if any(kw in desc for kw in annual_keywords):
                return True
        return False

    # Filter by start date
    if args.start_date:
        from datetime import datetime
        sd = datetime.strptime(args.start_date, '%Y-%m-%d').date()
        stmts = [s for s in all_stmts if s.statement_date >= sd and not _is_annual_summary(s)]
    else:
        stmts = [s for s in all_stmts if not _is_annual_summary(s)]

    # If multiple statements exist for the same month, keep only the non-annual one
    from collections import defaultdict
    month_groups = defaultdict(list)
    for s in stmts:
        key = (s.statement_date.year, s.statement_date.month)
        month_groups[key].append(s)
    
    final_stmts = []
    for key in sorted(month_groups.keys()):
        group = month_groups[key]
        if len(group) == 1:
            final_stmts.append(group[0])
        else:
            group.sort(key=lambda s: s.transactions.count(), reverse=True)
            final_stmts.append(group[0])
    stmts = final_stmts

    # Collect all symbols from holdings (using filtered stmts)
    all_symbols = set()
    for stmt in stmts:
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
        
        net_cash = compute_cash_flow(stmt, all_symbols)
        price_change = bal_change - net_cash
        
        if begin > 0:
            pct = float(price_change / begin * 100)
        else:
            pct = 0
        
        if abs(pct) >= args.threshold:
            high_change_months.append((stmt, pct, bal_change, net_cash))

    from datetime import datetime
    lines = []
    lines.append(f"# Detailed Ledger Report: {account.account_id} ({account.name}) — {currency}\n")
    lines.append(f"**Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}**\n")
    lines.append(f"Threshold: price change >{args.threshold}% of opening balance\n")
    if args.start_date:
        lines.append(f"Start date filter: {args.start_date}\n")
    lines.append(f"Statements analyzed: {len(stmts)} ({month_label(stmts[0].statement_date)} to {month_label(stmts[-1].statement_date)})")
    lines.append(f"High-change months found: {len(high_change_months)}\n")
    lines.append("---\n")

    # Summary table
    lines.append("## Summary of High-Change Months\n")
    lines.append(f"{'Month':>10} | {'Opening':>14} | {'Closing':>14} | {'Bal Δ':>14} | {'Cash Flow':>14} | {'Price Δ':>14} | % Change")
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

        # Find previous statement (in filtered list)
        stmt_idx = None
        for i, s in enumerate(stmts):
            if s.pk == stmt.pk:
                stmt_idx = i
                break
        prev_stmt = stmts[stmt_idx - 1] if stmt_idx and stmt_idx > 0 else None

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
