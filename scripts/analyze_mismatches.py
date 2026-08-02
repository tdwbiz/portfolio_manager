"""Analyze the mismatches to understand what's missing."""
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

from core.models import Account, RbcMonthly, RbcMonthlyBalance, Transaction, RbcMonthlyTransaction

account = Account.objects.get(account_id='90000001')

# Analyze specific mismatches
print('=== Analyzing Mismatches ===\n')

# 2025-01: Expected 7003.12, Actual 3.12, Diff 7000
print('2025-01-31: Diff = 7000.00')
stmt = RbcMonthly.objects.get(account=account, currency='CAD', statement_date='2025-01-31')
print('RBC PDF transactions:')
for tx in stmt.transactions.all():
    print(f'  {tx.date} {tx.activity_type:15} Debit={tx.debit:>10} Credit={tx.credit:>10}')

print('\nCSV transactions:')
csv_txns = Transaction.objects.filter(account=account, currency='CAD', date__year=2025, date__month=1)
for tx in csv_txns:
    print(f'  {tx.date} {tx.type:10} {tx.amount_cad:>10}')

print('\nAnalysis: CSV has WDRAW -7000 that PDF is missing')
print()

# 2021-11: Expected 637082.88, Actual 156.52, Diff 636926.36
print('2021-11-30: Diff = 636926.36')
stmt = RbcMonthly.objects.get(account=account, currency='CAD', statement_date='2021-11-30')
cash_bal = stmt.balances.filter(account_type='Cash').first()
print(f'Cash opening: {cash_bal.opening_balance}')
print(f'Cash closing: {cash_bal.closing_balance}')

print('\nRBC PDF transactions:')
for tx in stmt.transactions.all():
    print(f'  {tx.date} {tx.activity_type:15} Debit={tx.debit:>10} Credit={tx.credit:>10}')

print('\nCSV transactions:')
csv_txns = Transaction.objects.filter(account=account, currency='CAD', date__year=2021, date__month=11)
for tx in csv_txns:
    print(f'  {tx.date} {tx.type:10} {tx.amount_cad:>10}')

print('\nAnalysis: Huge mismatch - likely a data entry error in RBC balance')
print()

# 2023-08: Expected 27.88, Actual 7762.80, Diff 7734.92
print('2023-08-31: Diff = 7734.92')
stmt = RbcMonthly.objects.get(account=account, currency='CAD', statement_date='2023-08-31')
cash_bal = stmt.balances.filter(account_type='Cash').first()
print(f'Cash opening: {cash_bal.opening_balance}')
print(f'Cash closing: {cash_bal.closing_balance}')

print('\nPrevious month (2023-07):')
prev_stmt = RbcMonthly.objects.get(account=account, currency='CAD', statement_date='2023-07-31')
prev_cash = prev_stmt.balances.filter(account_type='Cash').first()
print(f'Cash closing: {prev_cash.closing_balance}')

print('\nAnalysis: Opening balance mismatch - RBC data has error')
