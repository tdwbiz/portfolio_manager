"""Verify that all PositionHistory chains are correct after fix."""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
sys.path.insert(0, r'C:\AI-DevTeam\projects\portfolio_manager')
django.setup()

import django.db.models as models
from core.models import Account, PositionHistory

print("=" * 80)
print("POSITION CHAIN VERIFICATION")
print("=" * 80)

# Check each chain for continuity
errors = []

for account in Account.objects.all():
    pairs = list(PositionHistory.objects.filter(
        account=account
    ).values_list('symbol', 'currency').distinct())
    
    for symbol, currency in pairs:
        records = list(PositionHistory.objects.filter(
            account=account,
            symbol=symbol,
            currency=currency,
        ).order_by('date_from'))
        
        for i, record in enumerate(records):
            # All but last should be closed
            if i + 1 < len(records):
                if record.date_to is None:
                    errors.append(
                        f"OPEN: {account.account_id} {symbol} {currency} "
                        f"{record.date_from} (should be closed)"
                    )
                else:
                    expected_to = records[i + 1].date_from - __import__('datetime').timedelta(days=1)
                    if record.date_to != expected_to:
                        errors.append(
                            f"GAP: {account.account_id} {symbol} {currency} "
                            f"{record.date_from} -> {record.date_to}, "
                            f"next: {records[i+1].date_from}, expected: {expected_to}"
                        )
            else:
                # Last should be open
                if record.date_to is not None:
                    errors.append(
                        f"CLOSED: {account.account_id} {symbol} {currency} "
                        f"{record.date_from} -> {record.date_to} (should be open)"
                    )

if errors:
    print(f"\nERRORS FOUND: {len(errors)}")
    for e in errors[:20]:
        print(f"  {e}")
else:
    print("\nALL CHAINS CORRECT - No gaps, overlaps, or incorrect dates")

# Summary stats
print(f"\nTotal PositionHistory records: {PositionHistory.objects.count()}")
print(f"Open positions: {PositionHistory.objects.filter(date_to__isnull=True).count()}")
print(f"Closed positions: {PositionHistory.objects.filter(date_to__isnull=False).count()}")

# Check for date overlaps
overlaps = list(PositionHistory.objects.filter(
    date_to__isnull=True
).values('account__account_id', 'symbol', 'currency').annotate(
    count=models.Count('id')
).filter(count__gt=1))

print(f"Overlapping open positions: {len(overlaps)}")

# Sample a few chains
print("\n--- Sample chains ---")
for account in Account.objects.filter(account_id='90000001')[:1]:
    for symbol, currency in [('VFV', 'CAD'), ('RBF2010', 'CAD')]:
        records = list(PositionHistory.objects.filter(
            account=account,
            symbol=symbol,
            currency=currency,
        ).order_by('date_from'))
        if records:
            print(f"\n{account.account_id} {symbol} {currency}:")
            for r in records:
                status = "OPEN" if r.date_to is None else f"-> {r.date_to}"
                print(f"  {r.date_from} {status}  qty: {r.quantity}")

print("\n" + "=" * 80)
