import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
import django
django.setup()
from core.models import Transaction
from django.db.models import Sum

txs = Transaction.objects.filter(
    account__account_id='90000002',
    date__year=2026,
    date__month=1
).values('currency', 'type').annotate(
    total_cad=Sum('amount_cad'),
    total_comm=Sum('commission'),
    count=Sum('id')
).order_by('currency', 'type')

for t in txs:
    print(f"{t['currency']} | {t['type']:10s} | total_cad={t['total_cad']:>12,.2f} | comm={t['total_comm']:>8,.2f} | count={t['count']}")

# Also show the raw query
print("\n--- Raw SQL ---")
from django.db import connection
sql = str(Transaction.objects.filter(
    account__account_id='90000002',
    date__year=2026,
    date__month=1
).values('currency', 'type').annotate(
    total_cad=Sum('amount_cad'),
    total_comm=Sum('commission'),
).query)
print(sql)
