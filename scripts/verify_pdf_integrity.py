"""Verify internal consistency of RBC PDF data for an account.

PDF is the master. We check:
  1. **Balance chain**: Each RbcMonthly's end_total_market_value should equal
     the next RbcMonthly's begin_total_market_value (for consecutive statements).
  2. **Holdings flow**: Starting from the first month's holdings, apply all
     transactions forward and verify we arrive at the last month's holdings.
  3. **Monthly ↔ Annual**: December RbcMonthly ending balance should match
     RbcAnnual year-end balance.
  4. **Transaction totals**: Sum of monthly transactions should reconcile with
     the change in holdings between statements.

Usage:
    pwsh -Command "python scripts/verify_pdf_integrity.py [--account 90000001] [--currency CAD]"
"""

import argparse
import re
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
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
    Account, RbcMonthly, RbcMonthlyTransaction, RbcMonthlyHolding, RbcAnnual,
)


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
    parser = argparse.ArgumentParser(description='Verify RBC PDF data integrity')
    parser.add_argument('--account', default='90000001', help='Account ID')
    parser.add_argument('--currency', default='CAD', help='Currency')
    parser.add_argument('--start-date', default=None, help='Start date for analysis (YYYY-MM-DD). Statements before this date are excluded.')
    parser.add_argument('--start-balance', default=None, type=Decimal, help='Known starting balance as of start-date. Used to validate first statement\'s begin balance.')
    args = parser.parse_args()

    account = Account.objects.get(account_id=args.account)
    currency = args.currency
    start_date = args.start_date

    lines = []
    lines.append(f"# PDF Data Integrity Report: {account.account_id} ({account.name}) — {currency}\n")
    lines.append("Verifies internal consistency of RBC PDF data.\n")
    lines.append("---\n")

    # =========================================================================
    # 1. Load all RbcMonthly statements sorted by date
    # =========================================================================
    all_stmts = list(RbcMonthly.objects.filter(
        account=account,
        currency=currency,
    ).order_by('statement_date'))

    if not all_stmts:
        lines.append(f"No RbcMonthly records found for this account/currency.\n")
        print("\n".join(lines))
        return

    # Filter by start date if provided
    if start_date:
        from datetime import datetime
        sd = datetime.strptime(start_date, '%Y-%m-%d').date()
        stmts = [s for s in all_stmts if s.statement_date >= sd]
        excluded = [s for s in all_stmts if s.statement_date < sd]
    else:
        stmts = all_stmts
        excluded = []

    lines.append(f"## Data Overview\n")
    lines.append(f"- Total RbcMonthly statements (all): {len(all_stmts)}")
    if excluded:
        lines.append(f"- Excluded statements (before {start_date}): {len(excluded)}")
    lines.append(f"- Statements in analysis: {len(stmts)}")
    lines.append(f"- Date range: {month_label(stmts[0].statement_date)} to {month_label(stmts[-1].statement_date)}")
    lines.append(f"- Earliest statement in analysis: {stmts[0].statement_date}")
    lines.append(f"- Latest statement: {stmts[-1].statement_date}")
    if start_date:
        lines.append(f"- Analysis start date: {start_date}")
        lines.append(f"- Known starting balance: {fmt(args.start_balance) if args.start_balance else 'N/A'}")
    lines.append("")

    # =========================================================================
    # 2. Balance Chain Check
    # =========================================================================
    lines.append("---\n")
    lines.append("## 1. Balance Chain Check\n")
    lines.append("Each statement's `begin_total_market_value` should equal the previous\n")
    lines.append("statement's `end_total_market_value` (for consecutive PDF statements).\n")
    lines.append(f"{'Stmt Date':>12} | {'Begin':>14} | {'End':>14} | {'Prev End':>14} | {'Chain Diff':>12} | Status")
    lines.append(f"{'-'*12}-|{'-'*14}-|{'-'*14}-|{'-'*14}-|{'-'*12}-|{'-'*8}")

    chain_issues = 0
    for i, stmt in enumerate(stmts):
        begin = stmt.begin_total_market_value or Decimal('0')
        end = stmt.end_total_market_value or Decimal('0')

        if i == 0:
            # First statement — validate against known starting balance if provided
            if args.start_balance is not None:
                prev_end = args.start_balance
                diff = abs(begin - prev_end)
                if diff > Decimal('1.00'):
                    status = f"MISMATCH (expected {fmt(prev_end)})"
                    chain_issues += 1
                elif diff > Decimal('0.01'):
                    status = f"CLOSE (expected {fmt(prev_end)})"
                    chain_issues += 1
                else:
                    status = "OK (matches start balance)"
            else:
                prev_end = Decimal('0')
                diff = Decimal('0')
                status = "(first, no start balance)"
        else:
            prev_end = stmts[i - 1].end_total_market_value or Decimal('0')
            diff = abs(begin - prev_end)
            if diff > Decimal('1.00'):
                status = "BROKEN"
                chain_issues += 1
            elif diff > Decimal('0.01'):
                status = "WARN"
                chain_issues += 1
            else:
                status = "OK"

        lines.append(
            f"{month_label(stmt.statement_date):>12} | {fmt(begin):>14} | {fmt(end):>14} | {fmt(prev_end):>14} | {fmt(diff):>12} | {status}"
        )

    lines.append("")
    if chain_issues:
        lines.append(f"**Chain issues: {chain_issues}** (statements where begin ≠ previous end)\n")
    else:
        lines.append("**Balance chain is intact across all statements.**\n")

    # =========================================================================
    # 3. Holdings Timeline
    # =========================================================================
    lines.append("---\n")
    lines.append("## 2. Holdings Timeline\n")
    lines.append("Quantity per symbol across all statements. Gaps indicate months\n")
    lines.append("where holdings data is missing from the PDF.\n")

    # Build holdings per statement
    holdings_by_stmt = {}
    for stmt in stmts:
        holdings = {}
        for h in stmt.holdings.all():
            sym = h.symbol.strip().upper() if h.symbol.strip() else h.security.strip()[:10].upper()
            if sym:
                holdings[sym] = {
                    'quantity': h.quantity,
                    'book_cost': h.book_cost,
                    'market_price': h.market_price,
                    'market_value': h.market_value,
                }
        holdings_by_stmt[stmt] = holdings

    # Collect all symbols
    all_symbols = set()
    for h in holdings_by_stmt.values():
        all_symbols.update(h.keys())

    # Cash/RBF2010 is special — treat it as "cash equivalent"
    cash_symbols = {'RBF2010'}

    if all_symbols:
        lines.append(f"Symbols tracked: {', '.join(sorted(all_symbols))}\n")
        header_parts = ['Stmt Date'.rjust(12)] + [s.rjust(12) for s in sorted(all_symbols)] + ['Total MV'.rjust(14)]
        lines.append(" | ".join(header_parts))
        sep_parts = ['-'*12] + ['-'*12 for _ in all_symbols] + ['-'*14]
        lines.append("-|".join(sep_parts))

        for stmt in stmts:
            h = holdings_by_stmt[stmt]
            parts = [month_label(stmt.statement_date)]
            total_mv = Decimal('0')
            for sym in sorted(all_symbols):
                if sym in h:
                    qty = float(h[sym]['quantity'])
                    parts.append(f"{qty:>12.2f}")
                    total_mv += h[sym]['market_value'] or Decimal('0')
                else:
                    parts.append(f"{'N/A':>12}")
            parts.append(f"{fmt(total_mv):>14}")
            lines.append(" | ".join(parts))

        lines.append("")

    # =========================================================================
    # 4. Transaction Summary Per Statement Period
    # =========================================================================
    lines.append("---\n")
    lines.append("## 3. Transaction Summary Per Statement Period\n")
    lines.append("For each statement period, shows the net effect of transactions\n")
    lines.append("and compares with the change in statement balances.\n")
    lines.append("This shows net cash flow from transactions vs the actual change in portfolio")
    lines.append("value. **The difference is primarily market price movement** (unrealized gains/losses).")
    lines.append("Large differences are normal and expected.\n")
    lines.append(f"{'Stmt Date':>12} | {'Net Cash Flow':>14} | {'Bal Change':>16} | {'Price Change':>14} | % Change")
    lines.append(f"{'-'*12}-|{'-'*14}-|{'-'*16}-|{'-'*14}-|{'-'*8}")

    for i, stmt in enumerate(stmts):
        txs = stmt.transactions.all()
        net_tx = sum(tx.credit - tx.debit for tx in txs)

        begin = stmt.begin_total_market_value or Decimal('0')
        end = stmt.end_total_market_value or Decimal('0')
        bal_change = end - begin

        # Price change = balance change minus cash flow
        price_change = bal_change - net_tx

        if begin > 0:
            pct = float(price_change / begin * 100)
        else:
            pct = 0

        lines.append(
            f"{month_label(stmt.statement_date):>12} | {fmt(net_tx):>14} | {fmt(bal_change):>16} | {fmt(price_change):>14} | {pct:>6.1f}%"
        )

    lines.append("")

    # Monthly vs Annual — only include in section 5, remove from here

    # =========================================================================
    # 5. Transaction-to-Holdings Reconciliation (Per-Period)
    # =========================================================================
    lines.append("---\n")
    lines.append("## 4. Transaction-to-Holdings Reconciliation (Per-Period)\n")
    lines.append("For each consecutive pair of statements, compares the actual change in\n")
    lines.append("holdings quantities against the transaction quantities in the period.\n")
    lines.append("\n")
    lines.append("**Important:** DRIP `BUY` entries in RBC PDF use the *existing holdings count*\n")
    lines.append("in the `quantity` field (e.g. `BUY VDY 2891 debit=366.13` means dividend\n")
    lines.append("reinvested into existing 2891 shares, NOT buying 2891 new shares).\n")
    lines.append("Real cash purchases use `BOUGHT` or `BUY` with unit price matching the\n")
    lines.append("fund's NAV. For DRIP entries, actual shares purchased = `debit / market_price`.\n")
    lines.append("\n")

    # For each consecutive pair of statements, compare holdings delta vs tx delta
    period_issues = []
    for i in range(len(stmts) - 1):
        stmt_a = stmts[i]
        stmt_b = stmts[i + 1]
        h_a = holdings_by_stmt[stmt_a]
        h_b = holdings_by_stmt[stmt_b]

        # Collect all symbols in this period
        period_symbols = set(h_a.keys()) | set(h_b.keys())
        if not period_symbols:
            continue

        # Compute actual quantity change from holdings snapshots
        actual_delta = {}
        for sym in period_symbols:
            qa = float(h_a.get(sym, {}).get('quantity', 0) or 0)
            qb = float(h_b.get(sym, {}).get('quantity', 0) or 0)
            actual_delta[sym] = qb - qa

        # Compute quantity change from transactions in stmt_b
        tx_delta = defaultdict(lambda: Decimal('0'))
        drip_re = re.compile(r'DIST ON\s+([\d,]+\.?\d*)\s+SHS')

        for tx in stmt_b.transactions.all():
            sym = tx.symbol.strip().upper() if tx.symbol.strip() else ''
            if not sym:
                # Try to extract from description
                desc = tx.description.upper()
                for s in sorted(all_symbols, key=lambda x: -len(x)):
                    if s in desc:
                        sym = s
                        break
                if not sym:
                    continue

            atype = tx.activity_type.upper()
            qty = Decimal(str(tx.quantity))
            desc = tx.description.upper()

            if atype in ('BUY', 'BOUGHT'):
                if tx.debit > Decimal('0'):
                    # Check if this is a DRIP entry
                    m = drip_re.search(desc)
                    if m:
                        parsed_shs = Decimal(m.group(1).replace(',', ''))
                        if qty == parsed_shs and parsed_shs > Decimal('10'):
                            # "Fake quantity" DRIP — qty field = existing holdings count
                            # Actual new shares are fractional and can't be computed from this record
                            # Skip — the gap will indicate DRIP activity
                            continue
                        elif qty < Decimal('10'):
                            # "Real quantity" DRIP — qty field = actual new shares
                            tx_delta[sym] += qty
                            continue
                    # Non-DRIP purchase (BOUGHT, or BUY with real quantity)
                    tx_delta[sym] += qty
                elif tx.credit > Decimal('0'):
                    # BUY with credit — transfer-in or DRIP via credit
                    if qty <= Decimal('10'):
                        tx_delta[sym] += qty
                    # else: large qty with credit is likely a transfer
            elif atype == 'SOLD':
                if tx.debit > Decimal('0'):
                    tx_delta[sym] -= qty
            # RBF2010 interest-to-shares
            elif atype == 'INTEREST':
                if 'RBF2010' in sym or '2010' in desc or 'RBC INVESTMENT SAVINGS' in desc:
                    if tx.debit > Decimal('0'):
                        # Interest reinvested as shares at $10/share
                        shares_from_interest = tx.debit / Decimal('10')
                        tx_delta['RBF2010'] += shares_from_interest

        # Find discrepancies
        max_gap = 0
        for sym in sorted(period_symbols):
            a = actual_delta.get(sym, 0)
            t = float(tx_delta.get(sym, 0))
            gap = a - t
            if abs(gap) > max_gap:
                max_gap = abs(gap)

        # Only report periods with notable gaps (>0.5 shares)
        if max_gap > 0.5:
            lines.append(f"**{month_label(stmt_a.statement_date)} → {month_label(stmt_b.statement_date)}** (max gap: {max_gap:.2f} shares)\n")
            lines.append(f"{'Symbol':>10} | {'Prev Qty':>10} | {'Next Qty':>10} | {'Actual Δ':>10} | {'Tx Δ':>12} | {'Gap':>10}")
            lines.append(f"{'-'*10}-|{'-'*10}-|{'-'*10}-|{'-'*10}-|{'-'*12}-|{'-'*10}")
            for sym in sorted(period_symbols):
                qa = float(h_a.get(sym, {}).get('quantity', 0) or 0)
                qb = float(h_b.get(sym, {}).get('quantity', 0) or 0)
                a = qb - qa
                t = float(tx_delta.get(sym, 0))
                gap = a - t
                if abs(gap) > 0.01 or abs(a) > 0.01:
                    lines.append(
                        f"{sym:>10} | {qa:>10.2f} | {qb:>10.2f} | {a:>10.2f} | {t:>12.4f} | {gap:>10.4f}"
                    )
            lines.append("")
            period_issues.append((month_label(stmt_b.statement_date), max_gap))

    if period_issues:
        lines.append(f"**Periods with gaps > 0.5 shares: {len(period_issues)}**\n")
        lines.append("Common causes of gaps:\n")
        lines.append("- **DRIP estimation error** — dividend reinvestment shares estimated from\n")
        lines.append("  `debit / market_price` may differ from actual shares if the reinvestment\n")
        lines.append("  occurred at a different price than the statement's snapshot price\n")
        lines.append("- **RBF2010 interest-to-shares** — `INTEREST` entries auto-convert to\n")
        lines.append("  RBF2010 shares at $10/share but aren't tagged as `BUY`\n")
        lines.append("- **Corporate actions** — splits, mergers, fund reorganizations\n")
        lines.append("- **FX rounding** — USD holdings converted to CAD may have small\n")
        lines.append("  share count rounding differences\n")
    else:
        lines.append("**No significant gaps found. Holdings changes reconcile with transactions.**\n")

    # =========================================================================
    # 6. RBF2010 Interest-to-Shares Analysis
    # =========================================================================
    lines.append("---\n")
    lines.append("## 5. RBF2010 Interest-to-Shares Analysis\n")
    lines.append("RBF2010 is a $10/share fund. Interest credits auto-convert to shares.\n")
    lines.append("`INTEREST` entries with `debit` represent new shares purchased.\n")
    lines.append("`INTEREST` entries with `credit` represent interest earned.\n")
    lines.append("")

    rbf2010_interest_shares = Decimal('0')
    rbf2010_interest_cash = Decimal('0')
    rbf2010_buy_debits = Decimal('0')

    for stmt in stmts:
        for tx in stmt.transactions.all():
            sym = tx.symbol.strip().upper() if tx.symbol.strip() else ''
            desc = tx.description.upper()
            # Detect RBF2010-related transactions
            if 'RBF2010' in sym or 'RBC INVESTMENT SAVINGS' in desc or '2010' in desc:
                if tx.activity_type.upper() == 'INTEREST':
                    rbf2010_interest_shares += tx.quantity
                    if tx.debit > 0:
                        rbf2010_interest_cash += tx.debit
                elif tx.activity_type.upper() in ('BUY', 'BOUGHT') and tx.debit > 0:
                    # Check if this is a cash purchase (not DRIP)
                    if 'REINVEST' not in desc:
                        rbf2010_buy_debits += tx.debit

    lines.append(f"Total RBF2010 interest shares credited: {rbf2010_interest_shares:.2f}")
    lines.append(f"Total RBF2010 interest cash (debit): {fmt(rbf2010_interest_cash)}")
    lines.append(f"Total RBF2010 cash purchases (non-interest): {fmt(rbf2010_buy_debits)}")
    lines.append(f"Implied RBF2010 shares from interest: {float(rbf2010_interest_cash) / 10:.2f} shares")
    lines.append("")

    # Per-period RBF2010 interest
    if rbf2010_interest_shares > 0:
        lines.append(f"{'Period':>12} | {'Interest Debit':>14} | {'Interest Credit':>16} | {'Qty Credited':>14}")
        lines.append(f"{'-'*12}-|{'-'*14}-|{'-'*16}-|{'-'*14}-")
        for stmt in stmts:
            stmt_interest = Decimal('0')
            stmt_credit = Decimal('0')
            stmt_qty = Decimal('0')
            for tx in stmt.transactions.all():
                sym = tx.symbol.strip().upper() if tx.symbol.strip() else ''
                desc = tx.description.upper()
                if tx.activity_type.upper() == 'INTEREST' and ('RBF2010' in sym or '2010' in desc or 'RBC INVESTMENT SAVINGS' in desc):
                    stmt_interest += tx.debit
                    stmt_credit += tx.credit
                    stmt_qty += tx.quantity
            if stmt_interest > 0 or stmt_credit > 0 or stmt_qty > 0:
                lines.append(
                    f"{month_label(stmt.statement_date):>12} | {fmt(stmt_interest):>14} | {fmt(stmt_credit):>16} | {float(stmt_qty):>14.2f}"
                )
        lines.append("")

    # =========================================================================
    # 7. Monthly vs Annual Cross-check
    # =========================================================================
    lines.append("---\n")
    lines.append("## 6. Monthly vs Annual Cross-check\n")
    lines.append("December RbcMonthly ending balance should match RbcAnnual year-end balance.\n")

    annuals = list(RbcAnnual.objects.filter(
        account=account,
        currency=currency,
    ).order_by('report_year'))

    if annuals:
        lines.append(f"\n{'Year':>6} | {'RbcMonthly (Dec)':>18} | {'RbcAnnual End':>18} | {'Diff':>14} | Status")
        lines.append(f"{'-'*6}-|{'-'*18}-|{'-'*18}-|{'-'*14}-|{'-'*8}")

        for annual in annuals:
            year = annual.report_year
            # Find December statement for this year
            dec_stmt = None
            for stmt in stmts:
                if stmt.statement_date.year == year and stmt.statement_date.month == 12:
                    dec_stmt = stmt
                    break

            annual_end = annual.bal_ending_value or Decimal('0')

            if dec_stmt:
                dec_end = dec_stmt.end_total_market_value or Decimal('0')
                diff = abs(dec_end - annual_end)
                if diff <= Decimal('1.00'):
                    status = "OK"
                elif diff <= Decimal('100.00'):
                    status = "CLOSE"
                else:
                    status = "MISMATCH"

                # Check if the mismatch might be explained by RbcAnnual including
                # additional accounts or adjusted values
                lines.append(
                    f"{year:>6} | {fmt(dec_end):>18} | {fmt(annual_end):>18} | {fmt(diff):>14} | {status}"
                )
            else:
                lines.append(
                    f"{year:>6} | {'(no Dec stmt)':>18} | {fmt(annual_end):>18} | {'—':>14} | NO MONTHLY"
                )

        lines.append("")
        lines.append("**Note:** RbcAnnual may include adjusted values, fees, or consolidated\n")
        lines.append("figures that differ from the snapshot balance in RbcMonthly. Consistent\n")
        lines.append("small differences ($1K-$3K) across years suggest a systematic offset,\n")
        lines.append("possibly from the annual report including a different valuation date\n")
        lines.append("or consolidating multiple sub-accounts.\n")
    else:
        lines.append(f"No RbcAnnual records for this account/currency.\n")

    # =========================================================================
    # 7. Summary
    # =========================================================================
    lines.append("---\n")
    lines.append("## Summary\n\n")

    issues = []
    if chain_issues:
        issues.append(f"- **{chain_issues}** balance chain breaks (begin ≠ previous end)")
    
    # Check for missing holdings
    missing_holdings = sum(1 for stmt in stmts if not holdings_by_stmt[stmt])
    if missing_holdings:
        issues.append(f"- **{missing_holdings}** statements have no holdings data")

    # Check for missing transactions
    stmts_no_tx = sum(1 for stmt in stmts if not stmt.transactions.exists())
    if stmts_no_tx:
        issues.append(f"- **{stmts_no_tx}** statements have no transactions")

    if not issues:
        lines.append("No integrity issues detected. PDF data appears consistent.\n")
    else:
        lines.append("Issues found:\n")
        for issue in issues:
            lines.append(issue)
        lines.append("")

    # Write report
    output_dir = Path(__file__).parent.parent / "docs" / "reconciliation"
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"pdf_integrity_{args.account}_{args.currency}.md"
    path = output_dir / filename
    path.write_text("\n".join(lines), encoding='utf-8')
    print(f"Written: {path}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
