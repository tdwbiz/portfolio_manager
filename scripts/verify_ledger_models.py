"""Verify ledger models are working correctly."""
import os
import sys
import django

sys.path.insert(0, r'C:\AI-DevTeam\projects\portfolio_manager')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
django.setup()

from core.models import (
    Account, Transaction, LedgerAccount, LedgerEntry,
    MonthlyLedgerBalance, LedgerMonthClosure
)

print("=" * 70)
print("LEDGER MODELS VERIFICATION")
print("=" * 70)

# Check if models exist
print("\n✓ Models imported successfully")
print(f"  LedgerAccount table: {LedgerAccount._meta.db_table}")
print(f"  LedgerEntry table: {LedgerEntry._meta.db_table}")
print(f"  MonthlyLedgerBalance table: {MonthlyLedgerBalance._meta.db_table}")
print(f"  LedgerMonthClosure table: {LedgerMonthClosure._meta.db_table}")

# Check current counts
print("\n--- Current Counts ---")
print(f"  LedgerAccount: {LedgerAccount.objects.count()}")
print(f"  LedgerEntry: {LedgerEntry.objects.count()}")
print(f"  MonthlyLedgerBalance: {MonthlyLedgerBalance.objects.count()}")
print(f"  LedgerMonthClosure: {LedgerMonthClosure.objects.count()}")

# Check if we have transactions to work with
print("\n--- Transaction Data ---")
print(f"  Total Transactions: {Transaction.objects.count()}")
print(f"  Total Accounts: {Account.objects.count()}")

# Show sample accounts
print("\n--- Sample Accounts ---")
for acct in Account.objects.all()[:5]:
    tx_count = Transaction.objects.filter(account=acct).count()
    print(f"  {acct.account_id} ({acct.name}): {tx_count} transactions")

print("\n" + "=" * 70)
print("VERIFICATION COMPLETE")
print("=" * 70)
