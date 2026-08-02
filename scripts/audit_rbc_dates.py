"""Audit RBC statement dates to understand date matching pattern."""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
sys.path.insert(0, r'C:\AI-DevTeam\projects\portfolio_manager')
django.setup()

from core.models import RbcMonthly, RbcMonthlyHolding

print("=== RbcMonthly statement_date samples ===")
samples = list(RbcMonthly.objects.filter(
    account__account_id='90000001'
).order_by('statement_date')[:20])

for r in samples:
    print(f"  {r.statement_date} {r.currency} end={r.end_total_market_value}")

print(f"\nTotal RbcMonthly: {RbcMonthly.objects.count()}")
print(f"Total RbcMonthlyHolding: {RbcMonthlyHolding.objects.count()}")

# Check unique statement dates
dates = list(RbcMonthly.objects.values_list('statement_date').distinct().order_by('statement_date'))
print(f"\nUnique statement dates: {len(dates)}")

# Sample holdings dates
holdings = list(RbcMonthlyHolding.objects.filter(
    rbc_monthly__account__account_id='90000001'
).values_list('rbc_monthly__statement_date', 'symbol', 'quantity', 'market_value').order_by(
    'rbc_monthly__statement_date'
)[:10])

print("\n=== RbcMonthlyHolding samples (90000001) ===")
for h in holdings:
    print(f"  {h[0]} {h[1]} qty={h[2]} mv={h[3]}")
