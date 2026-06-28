"""Quick test of the monthly VL parser — run from the project root."""
import os, sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'portfolio_manager.settings'

import django
django.setup()

import logging
logging.disable(logging.CRITICAL)

from imports.parsers_monthly_vl import parse_rbc_monthly_vl

pdf = r'inbox\for test\90000004-2014Apr30-2014Apr30_20260526_192902.pdf'
print(f"Parsing: {pdf}")

try:
    result = parse_rbc_monthly_vl(pdf)
    for sec in result:
        print(f"  currency={sec['currency']}")
        print(f"  statement_date={sec['statement_date']}")
        print(f"  account_number={sec['account_number']}")
        print(f"  fx_rate={sec['fx_rate']}")
        print(f"  balances={len(sec['balances'])}")
        print(f"  transactions={len(sec['transactions'])}")
        if sec['transactions']:
            tx = sec['transactions'][0]
            print(f"  first tx: {tx['date']} {tx['activity_type']} debit={tx['debit']} credit={tx['credit']} desc={tx['description'][:60]}")
    print("PASS")
except Exception as e:
    print(f"FAIL: {e}")
    import traceback; traceback.print_exc()
