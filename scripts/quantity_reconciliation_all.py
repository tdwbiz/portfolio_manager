#!/usr/bin/env python
"""
Quantity reconciliation for ALL accounts with holdings snapshots.

For each account: earliest PositionHistory snapshot = anchor (start),
latest snapshot = end. Replays every quantity-affecting transaction in
between and compares the computed end balance to the actual end snapshot.

Rule: `start + sum(activity) = end`, per symbol.

Quantity rules (same as scripts/quantity_reconciliation.py):
  BUY +qty | SELL -qty | INT qty>0 +qty (RBF interest shares)
  REORGANIZATION: EXP +qty, STK SPLIT/STK DIV +qty (the addition), CIL 0
  OTHER ASN +qty | TRANSFER 0 | everything else 0
Options are SHORT CALLS (symbols starting with '9'): negative = open short,
must close to exactly 0.

Writes: docs/reconciliation/quantity_recon_all_accounts.md
"""
import os
import sys

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portfolio_manager.settings")

import django
django.setup()

from datetime import date
from decimal import Decimal

from core.models import Account, PositionHistory, Transaction

# Activity-CSV symbol -> holdings-CSV symbol
SYMBOL_ALIASES = {
    'NVRU': 'NVU.UN',   # Northview Apartment REIT (TSX NVU.UN)
}

REPORT_PATH = os.path.join(_project_root, 'docs', 'reconciliation',
                           'quantity_recon_all_accounts.md')


def fmt_qty(q):
    if q == int(q):
        return str(int(q))
    return f"{float(q):,.4f}".rstrip('0').rstrip('.')


def classify(tx):
    """Return (delta_shares, category) for a transaction."""
    notes = (tx.notes or '').upper()
    if tx.type == 'BUY':
        return tx.quantity, 'BUY'
    if tx.type == 'SELL':
        return -tx.quantity, 'SELL'
    if tx.type == 'INT' and tx.quantity != 0:
        return tx.quantity, 'INT-SHARES'
    if tx.type in ('DIV', 'DIST') and tx.quantity != 0:
        # Dividend reinvestment: CSV posts REI as a DIV/DIST row with
        # qty = shares bought and a NEGATIVE amount (cash out).
        return tx.quantity, 'DIV-REIN'
    if tx.type == 'REORGANIZATION':
        if 'EXP' in notes:
            # Short calls ('9…') expire worthless: +qty. Long options
            # (e.g. 8UJDMB6 put) expire: -qty.
            if tx.symbol.startswith('9'):
                return tx.quantity, 'OPT-EXP'
            return -tx.quantity, 'LONG-OPT-EXP'
        if 'CIL' in notes:
            return Decimal('0'), 'CIL'
        if 'SPLIT' in notes or 'STK DIV' in notes:
            return tx.quantity, 'SPLIT/DIV'
        return Decimal('0'), f'REORG-OTHER:{notes[:40]}'
    if tx.type == 'OTHER':
        if 'ASN' in notes:
            return tx.quantity, 'OPT-ASN'
        return Decimal('0'), f'OTHER:{notes[:40]}'
    if tx.type == 'TRANSFER':
        return Decimal('0'), 'TRANSFER'
    return Decimal('0'), f'{tx.type} (no qty effect)'


def reconcile_account(acct):
    """Return a result dict for one account."""
    ph = list(PositionHistory.objects.filter(account=acct).order_by('date_from'))
    if not ph:
        return None

    dates = sorted({p.date_from for p in ph})
    anchor_date, end_date = dates[0], dates[-1]
    anchor_ph = {p.symbol: p.quantity for p in ph if p.date_from == anchor_date}
    end_ph = {p.symbol: p.quantity for p in ph if p.date_from == end_date}

    result = {
        'account': acct,
        'anchor_date': anchor_date,
        'end_date': end_date,
        'single_snapshot': len(dates) == 1,
        'anchor_count': len(anchor_ph),
        'end_count': len(end_ph),
        'rows': [],          # (symbol, start, computed, actual, diff)
        'problems': [],      # negative-balance events on real positions
        'option_errors': [],
        'applied': 0,
    }

    # Single snapshot: no anchor. Assume the account started with no
    # positions (e.g. opened after the global anchor date — TS_CAR_LIVING
    # opened 2020-07-31) and replay ALL of its transactions.
    if result['single_snapshot']:
        state = {}
        txs = Transaction.objects.filter(account=acct).order_by('date', 'id')
    else:
        state = dict(anchor_ph)
        txs = Transaction.objects.filter(
            account=acct, date__gte=anchor_date).order_by('date', 'id')

    for tx in txs:
        delta, cat = classify(tx)
        if delta == 0:
            continue
        sym = SYMBOL_ALIASES.get(tx.symbol, tx.symbol) or '(blank)'
        is_option = sym.startswith('9')
        state[sym] = state.get(sym, Decimal('0')) + delta
        result['applied'] += 1
        if is_option:
            if state[sym] > 0:
                result['option_errors'].append(
                    f"{tx.date}: {sym} went POSITIVE ({fmt_qty(state[sym])}) "
                    f"after {cat} — shorts should never go positive")
        elif state[sym] < 0:
            result['problems'].append(f"{tx.date}: {sym} went negative "
                                      f"({fmt_qty(state[sym])}) after {cat} {delta:+}")

    for sym, q in state.items():
        if sym.startswith('9') and q != 0:
            result['option_errors'].append(
                f"{sym}: option position not closed (final {q})")

    all_syms = sorted(set(list(state.keys()) + list(end_ph.keys())))
    for sym in all_syms:
        computed = state.get(sym, Decimal('0'))
        actual = end_ph.get(sym, Decimal('0'))
        if result['single_snapshot']:
            start = Decimal('0')  # zero-start replay; anchor IS the end snapshot
        else:
            start = anchor_ph.get(sym, Decimal('0'))
        result['rows'].append((sym, start, computed, actual, computed - actual))

    return result


def main():
    results = []
    for acct in Account.objects.order_by('id'):
        r = reconcile_account(acct)
        if r:
            results.append(r)

    # ---- Console summary -------------------------------------------------
    print(f"{'Account':16s} {'Anchor':10s} {'End':10s} "
          f"{'Exact':>6s} {'Gaps':>5s}  Options")
    for r in results:
        real = [x for x in r['rows'] if not x[0].startswith('9')]
        ok = sum(1 for x in real if x[4] == 0)
        bad = len(real) - ok
        opts_ok = not r['option_errors']
        print(f"{r['account'].name[:16]:16s} {str(r['anchor_date']):10s} "
              f"{str(r['end_date']):10s} {ok:>5d} {bad:>5d}  "
              f"{'OK' if opts_ok else 'ERRORS'}")

    # ---- Report -----------------------------------------------------------
    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
        f.write("# Quantity Reconciliation — All Accounts\n\n")
        f.write(f"**Generated:** {date.today()}  \n")
        f.write("**Rule:** `start snapshot + Σ(activity) = end snapshot`, per "
                "symbol. Quantities only (no cash).  \n")
        f.write("**Quantity rules:** BUY +qty, SELL −qty, INT qty>0 +qty (RBF "
                "interest shares), REORG EXP/SPLIT/STK DIV +qty, CIL 0, OTHER "
                "ASN +qty, TRANSFER 0. Options (9…) are short calls and must "
                "close to exactly 0. DLR currency conversions are paired "
                "BUY+SELL (net 0). NVRU aliased to NVU.UN.\n\n")

        # --- Summary table ---
        f.write("## Summary\n\n")
        f.write("| Account | Broker ID | Anchor | End | Positions exact | Gaps | Options |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for r in results:
            name, bid = r['account'].name, r['account'].account_id
            if r['single_snapshot']:
                anchor_s = '— (zero start)'
            else:
                anchor_s = str(r['anchor_date'])
            real = [x for x in r['rows'] if not x[0].startswith('9')]
            ok = sum(1 for x in real if x[4] == 0)
            bad = len(real) - ok
            opts = '✅' if not r['option_errors'] else '❌'
            f.write(f"| {name} | {bid} | {anchor_s} | "
                    f"{r['end_date']} | {ok}/{len(real)} | {bad} | {opts} |\n")
        f.write("\n")

        # --- Items to investigate (consolidated) ---
        f.write("## Items to Investigate\n\n")
        any_item = False
        for r in results:
            real = [x for x in r['rows'] if not x[0].startswith('9')]
            gaps = [x for x in real if x[4] != 0]
            if not gaps and not r['problems'] and not r['option_errors']:
                continue
            any_item = True
            f.write(f"### {r['account'].name} ({r['account'].account_id})\n\n")
            for sym, start, computed, actual, diff in sorted(
                    gaps, key=lambda x: -abs(x[4])):
                hint = []
                if diff < 0:
                    hint.append(f"short {fmt_qty(-diff)} shares — missing "
                                f"BUY(s) after {r['anchor_date']}, or anchor "
                                f"quantity too low (anchor has "
                                f"{fmt_qty(start)})")
                else:
                    hint.append(f"over {fmt_qty(diff)} shares — missing "
                                f"SELL(s), or anchor quantity too high "
                                f"(anchor has {fmt_qty(start)})")
                negs = [p for p in r['problems'] if p.split(':')[1].strip().split()[0] == sym]
                if negs:
                    first = min(negs)
                    hint.append(f"first went negative at {first}")
                f.write(f"- **{sym}**: start {fmt_qty(start)} → computed "
                        f"{fmt_qty(computed)} vs actual {fmt_qty(actual)} "
                        f"(diff {fmt_qty(diff)}) — {'; '.join(hint)}\n")
            for e in r['option_errors']:
                f.write(f"- **OPTION**: {e}\n")
            f.write("\n")
        if not any_item:
            f.write("_All accounts reconcile exactly. Nothing to investigate._\n\n")

        # --- Per-account detail ---
        f.write("## Per-Account Detail\n\n")
        for r in results:
            f.write(f"### {r['account'].name} ({r['account'].account_id})\n\n")
            real = [x for x in r['rows'] if not x[0].startswith('9')]
            ok = sum(1 for x in real if x[4] == 0)
            opts_ok = not r['option_errors']
            tail = (f"**{ok}/{len(real)} exact**, options "
                    f"{'all closed to 0 ✅' if opts_ok else 'ERRORS ❌'}.\n\n")
            if r['single_snapshot']:
                f.write(f"No anchor snapshot — assumed **zero positions at "
                        f"account start** (opened after the global anchor "
                        f"date). End {r['end_date']} ({r['end_count']} "
                        f"positions). " + tail)
            else:
                f.write(f"Anchor {r['anchor_date']} ({r['anchor_count']} positions) "
                    f"→ End {r['end_date']} ({r['end_count']} positions). "
                    + tail)
            if all(x[4] == 0 for x in real) and not r['problems'] \
                    and not r['option_errors']:
                f.write("_All positions reconcile exactly, no state errors._\n\n")
                continue
            f.write("| Symbol | Start | Computed | Actual | Diff |\n")
            f.write("|---|---|---|---|---|\n")
            for sym, start, computed, actual, diff in real:
                mark = '' if diff == 0 else ' ❌'
                f.write(f"| {sym} | {fmt_qty(start)} | {fmt_qty(computed)} | "
                        f"{fmt_qty(actual)} | {fmt_qty(diff)}{mark} |\n")
            if r['problems']:
                f.write("\n**State errors (balance went negative):**\n\n")
                for p in r['problems']:
                    f.write(f"- {p}\n")
            if r['option_errors']:
                f.write("\n**Option errors:**\n\n")
                for e in r['option_errors']:
                    f.write(f"- {e}\n")
            f.write("\n")

        f.write("---\n*Generated by `scripts/quantity_reconciliation_all.py`*\n")

    print(f"\nReport written to: {REPORT_PATH}")


if __name__ == '__main__':
    main()
