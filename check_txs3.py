import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
import django
django.setup()
from core.models import Transaction

# Check a few transactions to understand the data model
txs = Transaction.objects.filter(
    account__account_id='90000002',
    date__year=2026,
    date__month=1,
    type='BUY'
)[:5]

print("Sample BUY transactions:")
for t in txs:
    print(f"  {t.date} | {t.symbol:8s} | qty={t.quantity:12,.4f} | price={t.price:10,.4f}")
    print(f"    amount={t.amount:>14,.2f} | amount_cad={t.amount_cad:>14,.2f} | comm={t.commission:>8,.2f} | currency={t.currency}")

# Check SELL transactions
print("\nSample SELL transactions:")
txs = Transaction.objects.filter(
    account__account_id='90000002',
    date__year=2026,
    date__month=1,
    type='SELL'
)[:5]
for t in txs:
    print(f"  {t.date} | {t.symbol:8s} | qty={t.quantity:12,.4f} | price={t.price:10,.4f}")
    print(f"    amount={t.amount:>14,.2f} | amount_cad={t.amount_cad:>14,.2f} | comm={t.commission:>8,.2f} | currency={t.currency}")

# Check DIV transactions
print("\nSample DIV transactions:")
txs = Transaction.objects.filter(
    account__account_id='90000002',
    date__year=2026,
    date__month=1,
    type='DIV'
)[:5]
for t in txs:
    print(f"  {t.date} | {t.symbol:8s} | qty={t.quantity:12,.4f} | price={t.price:10,.4f}")
    print(f"    amount={t.amount:>14,.2f} | amount_cad={t.amount_cad:>14,.2f} | comm={t.commission:>8,.2f} | currency={t.currency}")
