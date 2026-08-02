"""Test the plan: CSV activity + RBC balances."""
import os
import sys
from pathlib import Path
from decimal import Decimal

script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')

import django
django.setup()

from core.models import Account, RbcMonthly, RbcMonthlyBalance, Transaction

account = Account.objects.get(account_id='90000001')

# Test the plan for 2020 onwards (when CSV data starts)
print('=== Testing Plan: CSV Activity + RBC Balances (2020+) ===\n')

stmts = RbcMonthly.objects.filter(
    account=account,
    currency='CAD',
    statement_date__year__gte=2020
).order_by('statement_date')

matches = 0
mismatches = 0
mismatch_details = []

for stmt in stmts:
    cash_bal = stmt.balances.filter(account_type='Cash').first()
    if not cash_bal:
        continue
    
    # Get CSV transactions for this month
    txns = Transaction.objects.filter(
        account=account,
        currency='CAD',
        date__year=stmt.statement_date.year,
        date__month=stmt.statement_date.month
    )
    
    csv_flow = sum(tx.amount_cad for tx in txns)
    expected_closing = cash_bal.opening_balance + csv_flow
    actual_closing = cash_bal.closing_balance
    diff = abs(expected_closing - actual_closing)
    
    if diff < 1:
        matches += 1
    else:
        mismatches += 1
        mismatch_details.append({
            'date': stmt.statement_date,
            'opening': cash_bal.opening_balance,
            'csv_flow': csv_flow,
            'expected': expected_closing,
            'actual': actual_closing,
            'diff': diff
        })

print(f'Total statements (2020+): {matches + mismatches}')
print(f'Matches: {matches} ({matches / (matches + mismatches) * 100:.1f}%)')
print(f'Mismatches: {mismatches} ({mismatches / (matches + mismatches) * 100:.1f}%)')

if mismatch_details:
    print(f'\n=== All Mismatches ===')
    for m in mismatch_details:
        print(f'{m["date"]}: Expected={m["expected"]:>10} Actual={m["actual"]:>10} Diff={m["diff"]:>8}')
