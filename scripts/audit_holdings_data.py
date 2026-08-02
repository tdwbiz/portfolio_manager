"""Audit current holdings data state.

Checks PositionHistory, RbcMonthlyHolding, and Transaction counts.
Identifies stale open positions and discrepancies.
"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
sys.path.insert(0, r'C:\AI-DevTeam\projects\portfolio_manager')
django.setup()

from decimal import Decimal
from datetime import date
from django.db.models import Count, Q

from core.models import (
    Account, PositionHistory, Transaction, 
    RbcMonthlyHolding, RbcMonthly
)

print("=" * 80)
print("HOLDINGS DATA AUDIT")
print("=" * 80)

# Basic counts
ph = PositionHistory.objects.all()
print(f"\nPositionHistory total: {ph.count()}")
print(f"  Open (date_to=None): {ph.filter(date_to__isnull=True).count()}")
print(f"  Closed: {ph.filter(date_to__isnull=False).count()}")

# Open positions by account
print("\n--- Open positions per account ---")
for account in Account.objects.all():
    count = PositionHistory.objects.filter(
        account=account, date_to__isnull=True
    ).count()
    if count:
        print(f"  {account.account_id} ({account.name}): {count}")

# Overlapping open positions (same account+symbol both open)
print("\n--- Overlapping open positions ---")
overlaps = PositionHistory.objects.filter(
    date_to__isnull=True
).values('account', 'symbol', 'currency').annotate(
    count=Count('id')
).filter(count__gt=1)
for o in overlaps:
    print(f"  {o['account']} {o['symbol']} {o['currency']}: {o['count']} open records")

# Snapshot dates per account
print("\n--- Snapshot dates per account ---")
for account in Account.objects.all():
    dates = PositionHistory.objects.filter(
        account=account
    ).values_list('date_from').distinct().order_by('-date_from')
    if dates:
        print(f"  {account.account_id}: {[d[0] for d in dates[:5]]}...")

# RbcMonthlyHolding stats
print(f"\nRbcMonthlyHolding total: {RbcMonthlyHolding.objects.count()}")
rbc_by_account = RbcMonthlyHolding.objects.values(
    'rbc_monthly__account__account_id'
).annotate(count=Count('id')).order_by('rbc_monthly__account__account_id')
for r in rbc_by_account:
    print(f"  {r['rbc_monthly__account__account_id']}: {r['count']} holdings")

# Transaction stats
print(f"\nTransaction total: {Transaction.objects.count()}")
tx_by_type = Transaction.objects.values('type').annotate(
    count=Count('id')
).order_by('-count')
for t in tx_by_type:
    print(f"  {t['type']}: {t['count']}")

# Check for symbols in CSV holdings but not in RBC
print("\n--- Symbols in PositionHistory (open) ---")
open_syms = PositionHistory.objects.filter(
    date_to__isnull=True
).values_list('symbol').distinct().order_by('symbol')
for sym in open_syms:
    print(f"  {sym[0]}")

# Example of stale position
print("\n--- Example stale positions (open but from old snapshots) ---")
stale = PositionHistory.objects.filter(
    date_to__isnull=True,
    date_from__lt=date(2024, 1, 1)
).order_by('account', 'date_from')
for pos in stale[:20]:
    # Check if there's a newer snapshot for same account+symbol
    newer = PositionHistory.objects.filter(
        account=pos.account,
        symbol=pos.symbol,
        date_from__gt=pos.date_from
    ).first()
    if newer:
        print(f"  {pos.account.account_id} {pos.symbol} "
              f"open since {pos.date_from}, newer: {newer.date_from} "
              f"(closed: {newer.date_to})")

print(f"\nTotal stale (open before 2024 with newer snapshot): "
      f"{PositionHistory.objects.filter(date_to__isnull=True, date_from__lt=date(2024,1,1)).count()}")

# RbcMonthly count
print(f"\nRbcMonthly total: {RbcMonthly.objects.count()}")
rbc_months = RbcMonthly.objects.values('account__account_id', 'currency').annotate(
    count=Count('id')
).order_by('account__account_id')
for r in rbc_months:
    print(f"  {r['account__account_id']} {r['currency']}: {r['count']} months")

print("\n" + "=" * 80)
