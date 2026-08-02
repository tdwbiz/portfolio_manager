import os
import sys
import django

sys.path.insert(0, r'C:\AI-DevTeam\projects\portfolio_manager')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
django.setup()

from core.models import LedgerAccount, LedgerEntry, MonthlyLedgerBalance

print('LedgerAccount:', [PERSON_NAME]')
print('LedgerEntry:', LedgerEntry.objects.count())
print('MonthlyLedgerBalance:', MonthlyLedgerBalance.objects.count())

print()
print('--- Ledger Accounts ---')
for la in LedgerAccount.objects.all():
    print(f'{la.account_type:10} {la.currency:3} {la.symbol:10} qty={la.quantity} cost={la.cost_basis_total}')

print()
print('--- [PERSON_NAME] (first 20) ---')
for le in LedgerEntry.objects.all()[:20]:
    print(f'{le.entry_date} {le.entry_type:6} {le.ledger_account} cash={le.cash_amount} qty={le.quantity_change}')
