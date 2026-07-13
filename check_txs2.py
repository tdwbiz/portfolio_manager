import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
import django
django.setup()
from core.models import Transaction

# Show first 5 BUY transactions for this account in Jan 2026
txs = Transaction.objects.filter(
    account__account_id='90000002',
    date__year=2026,
    date__month=1,
    type='BUY'
)[:5]

print("First 5 BUY transactions for TOM_LIF in Jan 2026:")
for t in txs:
    print(f"  {t.date} | {t.symbol:8s} | qty={t.quantity:12,.4f} | price={t.price:10,.4f} | amount={t.amount:>12,.2f} | amount_cad={t.amount_cad:>12,.2f} | currency={t.currency}")
