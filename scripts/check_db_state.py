import os
import sys
import django

sys.path.insert(0, r'C:\AI-DevTeam\projects\portfolio_manager')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
django.setup()

from core.models import PositionHistory, Account, Transaction

print("=" * 70)
print("DATABASE STATE CHECK")
print("=" * 70)
print(f"\nPositionHistory records: {PositionHistory.objects.count()}")
print(f"Account records: {Account.objects.count()}")
print(f"Transaction records: {Transaction.objects.count()}")

print("\n--- Accounts ---")
for acct in Account.objects.all()[:10]:
    ph_count = PositionHistory.objects.filter(account=acct).count()
    tx_count = Transaction.objects.filter(account=acct).count()
    print(f"  {acct.account_id} ({acct.name}): {ph_count} positions, {tx_count} transactions")

print("\n--- Sample PositionHistory ---")
for ph in PositionHistory.objects.all()[:5]:
    print(f"  {ph.account.account_id} | {ph.symbol} | {ph.currency} | "
          f"qty={ph.quantity} | {ph.date_from} to {ph.date_to}")

print("\n" + "=" * 70)
