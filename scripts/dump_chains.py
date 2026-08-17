#!/usr/bin/env python
"""Dump per-symbol transaction chains with running balance.

Usage: python scripts/dump_chains.py <account_id> <SYM1> [SYM2 ...]

Prints every quantity-affecting trade (delta + running balance) plus
DIV/DIST rows with their "ON X SHS" share counts as checkpoints.
"""
import os
import re
import sys

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portfolio_manager.settings")

import django
django.setup()

from decimal import Decimal

sys.path.insert(0, os.path.join(_project_root, 'scripts'))

from core.models import Transaction  # noqa: E402
from quantity_reconciliation_all import classify, fmt_qty  # noqa: E402

DIV_SHS = re.compile(r'ON\s+([\d,]+)\s+SHS')


def main():
    account_id = int(sys.argv[1])
    symbols = sys.argv[2:]
    print(f"===== account {account_id} =====")
    for sym in symbols:
        txs = list(Transaction.objects.filter(
            account_id=account_id, symbol=sym).order_by('date', 'id'))
        if not txs:
            print(f"\n--- {sym}: NO TRANSACTIONS ---")
            continue
        print(f"\n--- {sym} ({len(txs)} rows) ---")
        bal = Decimal('0')
        for t in txs:
            delta, cat = classify(t)
            if delta != 0:
                bal += delta
                note = (t.notes or '')[:70]
                print(f"{t.date} {cat:12s} qty={fmt_qty(t.quantity):>12} "
                      f"delta={fmt_qty(delta):>12} bal={fmt_qty(bal):>12} | {note}")
            elif t.type in ('DIV', 'DIST'):
                m = DIV_SHS.search(t.notes or '')
                shs = m.group(1) if m else '?'
                print(f"{t.date} {t.type:12s} div ON {shs:>8} SHS "
                      f"(bal at this point: {fmt_qty(bal)})")


if __name__ == '__main__':
    main()
