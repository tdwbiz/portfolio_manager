"""Reconcile transactions between PDF (RbcMonthlyTransaction) and CSV (Transaction) for a specific account and year.

PDF is the master. We check:
  1. Transactions in PDF but missing from CSV
  2. Transactions in CSV but missing from PDF
  3. Transactions present in both but with discrepancies (amount, date, etc.)

Usage:
    pwsh -Command "python scripts/reconcile_tx.py [--account 90000001] [--year 2026] [--currency CAD]"
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
    Account, RbcMonthly, RbcMonthlyTransaction,
    Transaction, BrokerStatement,
)


def fmt(val, precision=2):
    if val is None:
        return "N/A"
    try:
        return f"{Decimal(str(val)):,.{precision}f}"
    except Exception:
        return str(val)


def get_pdf_transactions(account, year, currency):
    """Get all RbcMonthlyTransaction records for the given account/year/currency."""
    stmts = RbcMonthly.objects.filter(
        account=account,
        currency=currency,
        statement_date__year=year,
    ).order_by('statement_date')

    txs = []
    for stmt in stmts:
        for tx in stmt.transactions.all():
            # PDF: debit = money going out (negative), credit = money coming in (positive)
            # net = credit - debit (positive = inflow)
            txs.append({
                'source': 'PDF',
                'date': tx.date,
                'type': tx.activity_type.strip(),
                'symbol': tx.symbol.strip(),
                'quantity': tx.quantity,
                'price': tx.price,
                'debit': tx.debit,
                'credit': tx.credit,
                'currency': tx.currency,
                'commission': tx.commission,
                'description': tx.description.strip(),
                'net': tx.credit - tx.debit,
                'stmt_date': stmt.statement_date,
            })
    return txs


def get_csv_transactions(account, year, currency):
    """Get all Transaction (CSV) records for the given account/year/currency."""
    txs = []
    for tx in Transaction.objects.filter(
        account=account,
        date__year=year,
        currency=currency,
    ).order_by('date'):
        # CSV sign convention: BUY/WDRAW/TAX = negative amount_cad, SELL/DEP/DIV/INT = positive
        txs.append({
            'source': 'CSV',
            'date': tx.date,
            'type': tx.type.strip(),
            'symbol': tx.symbol.strip(),
            'quantity': tx.quantity,
            'price': tx.price,
            'amount_cad': tx.amount_cad,
            'currency': tx.currency,
            'commission': tx.commission,
            'net': tx.amount_cad,  # negative = outflow
            'notes': tx.notes,
        })
    return txs


def types_compatible(pdf_type, csv_type):
    """Check if a PDF activity type could match a CSV type."""
    pt = pdf_type.upper()
    ct = csv_type.upper()

    # Direct matches
    if pt == ct:
        return True

    # PDF BUY/BOUGHT -> CSV BUY
    if pt in ('BUY', 'BOUGHT') and ct == 'BUY':
        return True

    # PDF SELL -> CSV SELL
    if pt == 'SELL' and ct == 'SELL':
        return True

    # PDF INTEREST -> CSV INT
    if pt in ('INTEREST', 'INTEREST PAID') and ct == 'INT':
        return True

    # PDF BUY with credit (dividend reinvestment) -> CSV DIST/DIV
    if pt == 'BUY' and ct in ('DIST', 'DIV'):
        return True

    # PDF TRANSFER -> CSV TRANSFER/TFO
    if pt == 'TRANSFER' and ct in ('TRANSFER', 'TFO'):
        return True

    return False


def abs_net(tx):
    """Absolute value of net amount."""
    return abs(float(tx['net']))


def match_transactions(pdf_txs, csv_txs):
    """Greedy matching: for each PDF tx, find best CSV match by date proximity + amount + description."""
    csv_used = set()
    matched = []

    for i, ptx in enumerate(pdf_txs):
        best_j = None
        best_score = None

        for j, ctx in enumerate(csv_txs):
            if j in csv_used:
                continue

            # Must have compatible types
            if not types_compatible(ptx['type'], ctx['type']):
                continue

            # Date within 5 days
            if ptx['date'] and ctx['date']:
                date_diff = abs((ptx['date'] - ctx['date']).days)
                if date_diff > 5:
                    continue
            else:
                continue

            # Amount within 20% or $5 (whichever larger)
            p_abs = abs_net(ptx)
            c_abs = abs_net(ctx)
            if p_abs > 0 or c_abs > 0:
                threshold = max(5.0, max(p_abs, c_abs) * 0.20)
                amt_diff = abs(p_abs - c_abs)
                if amt_diff > threshold:
                    continue

            # Description/symbol overlap
            p_desc = ptx['description'].upper()
            c_notes = (ctx.get('notes') or '').upper()
            p_sym = ptx['symbol'].upper()
            c_sym = ctx['symbol'].upper()

            has_text_match = False
            if p_sym and c_sym and (p_sym in c_sym or c_sym in p_sym):
                has_text_match = True
            if p_desc and c_notes:
                # Check if any word from PDF description appears in CSV notes
                p_words = set(p_desc.split())
                c_words = set(c_notes.split())
                common = p_words & c_words
                if len(common) >= 2:
                    has_text_match = True

            # Score: lower is better
            # Priority: amount match > date proximity > text match
            if p_abs > 0 or c_abs > 0:
                p_net = float(ptx['net'])
                c_net = float(ctx['net'])
                # Compare absolute amounts — signs may differ for some types
                amt_score = abs(p_abs - c_abs)
            else:
                amt_score = 0

            date_score = abs((ptx['date'] - ctx['date']).days)
            text_score = 0 if has_text_match else 100

            score = amt_score * 10 + date_score + text_score

            if best_j is None or score < best_score:
                best_j = j
                best_score = score

        if best_j is not None:
            csv_used.add(best_j)
            matched.append((i, best_j))

    unmatched_pdf = [i for i in range(len(pdf_txs)) if i not in {m[0] for m in matched}]
    unmatched_csv = [j for j in range(len(csv_txs)) if j not in {m[1] for m in matched}]

    return matched, unmatched_pdf, unmatched_csv


def generate_report(account_id, year, currency, pdf_txs, csv_txs, matched, unmatched_pdf, unmatched_csv):
    from datetime import datetime
    lines = []

    lines.append(f"# Transaction Reconciliation: {account_id} — {currency} — {year}\n")
    lines.append(f"**Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}**\n")
    lines.append(f"**PDF is the master source.** CSV transactions are compared against PDF.\n")
    lines.append(f"PDF is master — any CSV-only transactions should be reviewed for accuracy.\n")
    lines.append(f"- PDF transactions: {len(pdf_txs)}")
    lines.append(f"- CSV transactions: {len(csv_txs)}")
    lines.append(f"- Matched: {len(matched)}")
    lines.append(f"- PDF-only (missing from CSV): {len(unmatched_pdf)}")
    lines.append(f"- CSV-only (not in PDF): {len(unmatched_csv)}")
    lines.append("")

    # Show all matched pairs with details
    lines.append("## Matched Transactions\n")
    lines.append(f"{'#':>3} | {'PDF Date':>10} | {'PDF Type':>12} | {'CSV Date':>10} | {'CSV Type':>12} | {'PDF Net':>12} | {'CSV Net':>12} | {'Diff':>10} | Symbol")
    lines.append(f"{'-'*3}-|{'-'*10}-|{'-'*12}-|{'-'*10}-|{'-'*12}-|{'-'*12}-|{'-'*12}-|{'-'*10}-|{'-'*10}")

    for idx, (pi, ci) in enumerate(matched, 1):
        ptx = pdf_txs[pi]
        ctx = csv_txs[ci]
        p_net = float(ptx['net'])
        c_net = float(ctx['net'])
        diff = p_net - c_net
        sym = ptx['symbol'] or ctx['symbol'] or ''
        lines.append(
            f"{idx:>3} | {ptx['date'].strftime('%Y-%m-%d'):>10} | {ptx['type']:>12} | {ctx['date'].strftime('%Y-%m-%d'):>10} | {ctx['type']:>12} | {fmt(ptx['net']):>12} | {fmt(ctx['net']):>12} | {fmt(Decimal(str(diff))):>10} | {sym}"
        )
    lines.append("")

    # Highlight discrepancies
    discrepant = []
    for pi, ci in matched:
        ptx = pdf_txs[pi]
        ctx = csv_txs[ci]
        diff = abs(float(ptx['net']) - float(ctx['net']))
        if diff > 0.01:
            discrepant.append((pi, ci, diff))

    if discrepant:
        lines.append("## Amount Discrepancies (> $0.01)\n")
        lines.append(f"{'PDF Date':>10} | {'PDF Type':>12} | {'CSV Type':>12} | {'PDF Net':>12} | {'CSV Net':>12} | {'Diff':>10} | Description")
        lines.append(f"{'-'*10}-|{'-'*12}-|{'-'*12}-|{'-'*12}-|{'-'*12}-|{'-'*10}-|{'-'*40}")
        for pi, ci, diff in sorted(discrepant, key=lambda x: x[2], reverse=True):
            ptx = pdf_txs[pi]
            ctx = csv_txs[ci]
            lines.append(
                f"{ptx['date'].strftime('%Y-%m-%d'):>10} | {ptx['type']:>12} | {ctx['type']:>12} | {fmt(ptx['net']):>12} | {fmt(ctx['net']):>12} | {fmt(Decimal(str(diff))):>10} | {ptx['description'][:40]}"
            )
        lines.append("")

    # PDF-only
    if unmatched_pdf:
        lines.append("## PDF Transactions Missing from CSV\n")
        lines.append("These exist in the PDF but were not matched to any CSV transaction.\n")
        lines.append(f"{'Date':>10} | {'Type':>12} | {'Symbol':>8} | {'Debit':>12} | {'Credit':>12} | {'Net':>12} | {'Stmt':>10} | Description")
        lines.append(f"{'-'*10}-|{'-'*12}-|{'-'*8}-|{'-'*12}-|{'-'*12}-|{'-'*12}-|{'-'*10}-|{'-'*40}")
        for pi in unmatched_pdf:
            ptx = pdf_txs[pi]
            lines.append(
                f"{ptx['date'].strftime('%Y-%m-%d'):>10} | {ptx['type']:>12} | {ptx['symbol']:>8} | {fmt(ptx['debit']):>12} | {fmt(ptx['credit']):>12} | {fmt(ptx['net']):>12} | {ptx['stmt_date'].strftime('%Y-%m'):>10} | {ptx['description'][:40]}"
            )
        lines.append("")

    # CSV-only
    if unmatched_csv:
        lines.append("## CSV Transactions Not in PDF\n")
        lines.append("These exist in CSV but were not matched to any PDF transaction.\n")
        lines.append(f"{'Date':>10} | {'Type':>10} | {'Symbol':>8} | {'Amount_CAD':>12} | Notes")
        lines.append(f"{'-'*10}-|{'-'*10}-|{'-'*8}-|{'-'*12}-|{'-'*40}")
        for ci in unmatched_csv:
            ctx = csv_txs[ci]
            notes = ctx.get('notes', '') or ''
            lines.append(
                f"{ctx['date'].strftime('%Y-%m-%d'):>10} | {ctx['type']:>10} | {ctx['symbol']:>8} | {fmt(ctx['net']):>12} | {notes[:40]}"
            )
        lines.append("")

    # Summary by type
    lines.append("---\n")
    lines.append("## Summary by PDF Type\n\n")
    pdf_by_type = defaultdict(list)
    for ptx in pdf_txs:
        pdf_by_type[ptx['type']].append(ptx)

    for t in sorted(pdf_by_type.keys()):
        tx_list = pdf_by_type[t]
        total = sum(float(x['net']) for x in tx_list)
        lines.append(f"### {t} ({len(tx_list)} transactions, net {fmt(Decimal(str(total)))})")
        for x in tx_list:
            lines.append(f"- {x['date'].strftime('%Y-%m-%d')} {x['description'][:60]} | Debit {fmt(x['debit'])} Credit {fmt(x['credit'])} Net {fmt(x['net'])}")
        lines.append("")

    # Grand totals
    p_grand = sum(float(x['net']) for x in pdf_txs)
    c_grand = sum(float(x['net']) for x in csv_txs)
    lines.append("---\n")
    lines.append(f"**PDF net total: {fmt(Decimal(str(p_grand)))}**  ")
    lines.append(f"**CSV net total: {fmt(Decimal(str(c_grand)))}**  ")
    lines.append(f"**Difference: {fmt(Decimal(str(p_grand - c_grand)))}**\n")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description='Reconcile PDF vs CSV transactions')
    parser.add_argument('--account', default='90000001', help='Account ID')
    parser.add_argument('--year', type=int, default=2026, help='Year to reconcile')
    parser.add_argument('--currency', default='CAD', help='Currency')
    args = parser.parse_args()

    account = Account.objects.get(account_id=args.account)
    print(f"Reconciling {account.account_id} ({account.name}) — {args.currency} — {args.year}")

    pdf_txs = get_pdf_transactions(account, args.year, args.currency)
    csv_txs = get_csv_transactions(account, args.year, args.currency)

    print(f"  PDF transactions: {len(pdf_txs)}")
    print(f"  CSV transactions: {len(csv_txs)}")

    matched, unmatched_pdf, unmatched_csv = match_transactions(pdf_txs, csv_txs)

    print(f"  Matched: {len(matched)}")
    print(f"  PDF-only: {len(unmatched_pdf)}")
    print(f"  CSV-only: {len(unmatched_csv)}")

    report = generate_report(args.account, args.year, args.currency, pdf_txs, csv_txs, matched, unmatched_pdf, unmatched_csv)

    output_dir = Path(__file__).parent.parent / "docs" / "reconciliation"
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"tx_recon_{args.account}_{args.currency}_{args.year}.md"
    path = output_dir / filename
    path.write_text(report, encoding='utf-8')
    print(f"\n  Written: {path}")


if __name__ == "__main__":
    main()
