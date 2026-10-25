"""Reconcile position holdings between PDF (RbcMonthlyHolding) and CSV (PositionHistory) for a specific account and year.

PDF is the master. We compare end-of-month holdings from both sources.

Usage:
    pwsh -Command "python scripts/reconcile_holdings.py [--account 90000001] [--year 2026] [--currency CAD]"
"""

import argparse
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
    Account, RbcMonthly, RbcMonthlyHolding,
    PositionHistory,
)


def fmt(val, precision=2):
    if val is None:
        return "N/A"
    try:
        return f"{Decimal(str(val)):,.{precision}f}"
    except Exception:
        return str(val)


def get_pdf_holdings_by_month(account, year, currency):
    """Get RbcMonthlyHolding grouped by statement month.

    Returns dict: { (year, month): { symbol: {quantity, market_value, book_cost, ...} } }
    """
    stmts = RbcMonthly.objects.filter(
        account=account,
        currency=currency,
        statement_date__year=year,
    ).order_by('statement_date')

    result = {}
    for stmt in stmts:
        mk = (stmt.statement_date.year, stmt.statement_date.month)
        holdings = {}
        for h in stmt.holdings.all():
            sym = h.symbol.strip().upper() if h.symbol.strip() else h.security.strip()[:10].upper()
            if sym:
                holdings[sym] = {
                    'security': h.security.strip(),
                    'symbol': h.symbol.strip(),
                    'quantity': h.quantity,
                    'book_cost': h.book_cost,
                    'market_price': h.market_price,
                    'market_value': h.market_value,
                    'stmt_date': stmt.statement_date,
                }
        result[mk] = holdings
    return result


def get_csv_holdings_by_month(account, year, currency):
    """Get PositionHistory snapshot at end of each month.

    For each month in the year, find all positions that are active
    (date_from <= month_end and (date_to is None or date_to >= month_start)).
    """
    result = {}

    # Get all active positions for this account/currency in the year range
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)

    positions = PositionHistory.objects.filter(
        account=account,
        currency=currency,
        date_from__lte=year_end,
    ).filter(
        date_to__isnull=True
    ).union(
        PositionHistory.objects.filter(
            account=account,
            currency=currency,
            date_from__lte=year_end,
            date_to__gte=year_start,
        )
    ).order_by('symbol', 'date_from')

    # For each month, determine which positions are active
    for month in range(1, 13):
        month_start = date(year, month, 1)
        if month == 12:
            month_end = date(year, 12, 31)
        else:
            month_end = date(year, month + 1, 1)

        mk = (year, month)
        holdings = {}

        for pos in positions:
            # Check if position is active during this month
            if pos.date_from <= month_end:
                if pos.date_to is None or pos.date_to >= month_start:
                    sym = pos.symbol.strip().upper()
                    if sym:
                        holdings[sym] = {
                            'symbol': pos.symbol.strip(),
                            'quantity': pos.quantity,
                            'cost_basis_per_unit': pos.cost_basis_per_unit,
                            'total_cost_basis': pos.total_cost_basis,
                            'date_from': pos.date_from,
                            'date_to': pos.date_to,
                        }

        result[mk] = holdings

    return result


def month_label(ym):
    return f"{ym[0]:04d}-{ym[1]:02d}"


def generate_report(account_id, year, currency, pdf_data, csv_data):
    from datetime import datetime
    lines = []

    lines.append(f"# Holdings Reconciliation: {account_id} — {currency} — {year}\n")
    lines.append(f"**Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}**\n")
    lines.append(f"**PDF is the master source.** CSV positions are compared against PDF holdings.\n")

    pdf_months = sorted(pdf_data.keys())
    csv_months = sorted(csv_data.keys())

    lines.append(f"PDF months with holdings data: {', '.join([month_label(m) for m in pdf_months])}")
    lines.append(f"CSV months with position data: {len(csv_months)} months\n")
    lines.append("")

    # Overall symbol comparison (latest PDF month vs latest CSV month)
    if pdf_months:
        latest_pdf_month = pdf_months[-1]
        latest_pdf = pdf_data[latest_pdf_month]
        lines.append(f"## Latest PDF Holdings ({month_label(latest_pdf_month)})\n")

        if latest_pdf:
            lines.append(f"{'Symbol':>10} | {'Security':>30} | {'Quantity':>12} | {'Book Cost':>14} | {'Market Price':>12} | {'Market Value':>14}")
            lines.append(f"{'-'*10}-|{'-'*30}-|{'-'*12}-|{'-'*14}-|{'-'*12}-|{'-'*14}")
            total_mv = Decimal('0')
            total_bc = Decimal('0')
            for sym in sorted(latest_pdf.keys()):
                h = latest_pdf[sym]
                mv = h['market_value'] or Decimal('0')
                bc = h['book_cost'] or Decimal('0')
                total_mv += mv
                total_bc += bc
                sec = h['security'][:30]
                lines.append(
                    f"{h['symbol']:>10} | {sec:>30} | {fmt(h['quantity']):>12} | {fmt(bc):>14} | {fmt(h['market_price'], 4):>12} | {fmt(mv):>14}"
                )
            lines.append(f"\n**Total Market Value: {fmt(total_mv)}**  **Total Book Cost: {fmt(total_bc)}**\n")
        else:
            lines.append("No PDF holdings for this month.\n")

    # CSV positions active in 2026
    lines.append("## CSV PositionHistory (Active positions during 2026)\n")
    all_csv_positions = set()
    for month_holdings in csv_data.values():
        all_csv_positions.update(month_holdings.keys())

    if all_csv_positions:
        # Show the latest snapshot
        latest_csv = csv_data.get((year, 12), csv_data.get(list(csv_data.keys())[-1], {}))
        if latest_csv:
            lines.append(f"{'Symbol':>10} | {'Quantity':>12} | {'Cost/Unit':>12} | {'Total Cost':>14} | {'Date From':>12} | {'Date To':>12}")
            lines.append(f"{'-'*10}-|{'-'*12}-|{'-'*12}-|{'-'*14}-|{'-'*12}-|{'-'*12}")
            for sym in sorted(latest_csv.keys()):
                p = latest_csv[sym]
                lines.append(
                    f"{p['symbol']:>10} | {fmt(p['quantity']):>12} | {fmt(p['cost_basis_per_unit']):>12} | {fmt(p['total_cost_basis']):>14} | {p['date_from'].strftime('%Y-%m-%d') if p['date_from'] else 'N/A':>12} | {p['date_to'].strftime('%Y-%m-%d') if p['date_to'] else 'Open':>12}"
                )
            lines.append("")

    # Month-by-month comparison (only for months where both sources have data)
    common_months = sorted(set(pdf_months) & set(csv_months))
    if common_months:
        lines.append("---\n")
        lines.append("## Month-by-Month Quantity Comparison\n")
        lines.append("Compares quantity per symbol between PDF and CSV for each month.\n")

        for mk in common_months:
            pdf_h = pdf_data.get(mk, {})
            csv_h = csv_data.get(mk, {})

            all_symbols = sorted(set(pdf_h.keys()) | set(csv_h.keys()))
            if not all_symbols:
                continue

            # Only show this month if there are differences
            has_diff = False
            for sym in all_symbols:
                p_qty = float(pdf_h.get(sym, {}).get('quantity', 0))
                c_qty = float(csv_h.get(sym, {}).get('quantity', 0))
                if abs(p_qty - c_qty) > 0.001:
                    has_diff = True
                    break

            if not has_diff and len(pdf_h) != len(csv_h):
                has_diff = True

            if not has_diff:
                continue

            lines.append(f"### {month_label(mk)} — DIFFERENCES\n")
            lines.append(f"{'Symbol':>10} | {'PDF Qty':>10} | {'CSV Qty':>10} | {'Diff':>10} | Source")
            lines.append(f"{'-'*10}-|{'-'*10}-|{'-'*10}-|{'-'*10}-|{'-'*15}")

            for sym in all_symbols:
                p_qty = pdf_h.get(sym, {}).get('quantity', Decimal('0')) or Decimal('0')
                c_qty = csv_h.get(sym, {}).get('quantity', Decimal('0')) or Decimal('0')
                diff = p_qty - c_qty

                if sym in pdf_h and sym not in csv_h:
                    source = "PDF only"
                elif sym not in pdf_h and sym in csv_h:
                    source = "CSV only"
                elif abs(float(diff)) > 0.001:
                    source = "MISMATCH"
                else:
                    continue  # Skip matching rows

                lines.append(
                    f"{sym:>10} | {fmt(p_qty):>10} | {fmt(c_qty):>10} | {fmt(diff):>10} | {source}"
                )
            lines.append("")

    # Symbols that appear in PDF but never in CSV (or vice versa)
    lines.append("---\n")
    lines.append("## Symbol Coverage Summary\n\n")

    all_pdf_symbols = set()
    for month_holdings in pdf_data.values():
        all_pdf_symbols.update(month_holdings.keys())

    lines.append(f"PDF symbols seen during {year}: {', '.join(sorted(all_pdf_symbols)) if all_pdf_symbols else 'None'}")
    lines.append(f"CSV symbols seen during {year}: {', '.join(sorted(all_csv_positions)) if all_csv_positions else 'None'}")
    lines.append("")

    pdf_only = all_pdf_symbols - all_csv_positions
    csv_only = all_csv_positions - all_pdf_symbols

    if pdf_only:
        lines.append(f"**PDF-only symbols:** {', '.join(sorted(pdf_only))}")
    if csv_only:
        lines.append(f"**CSV-only symbols:** {', '.join(sorted(csv_only))}")
    if not pdf_only and not csv_only:
        lines.append("Symbol sets match exactly.")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description='Reconcile PDF vs CSV holdings')
    parser.add_argument('--account', required=True, help='Account ID (required — no default)')
    parser.add_argument('--year', type=int, default=2026, help='Year to reconcile')
    parser.add_argument('--currency', default='CAD', help='Currency')
    args = parser.parse_args()

    account = Account.objects.get(account_id=args.account)
    print(f"Reconciling holdings for {account.account_id} ({account.name}) — {args.currency} — {args.year}")

    pdf_data = get_pdf_holdings_by_month(account, args.year, args.currency)
    csv_data = get_csv_holdings_by_month(account, args.year, args.currency)

    print(f"  PDF months: {len(pdf_data)}")
    for mk in sorted(pdf_data.keys()):
        print(f"    {month_label(mk)}: {len(pdf_data[mk])} symbols")

    print(f"  CSV months: {len(csv_data)}")

    report = generate_report(args.account, args.year, args.currency, pdf_data, csv_data)

    output_dir = Path(__file__).parent.parent / "docs" / "reconciliation"
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"holdings_recon_{args.account}_{args.currency}_{args.year}.md"
    path = output_dir / filename
    path.write_text(report, encoding='utf-8')
    print(f"\n  Written: {path}")


if __name__ == "__main__":
    main()
