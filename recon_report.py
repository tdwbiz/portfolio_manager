"""Reconciliation report script for RBC accounts."""
import os
import sys
sys.path.append('.')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')

import django
django.setup()

from decimal import Decimal
from core.models import Account, RbcMonthly, CoreMonthlyStatement
from datetime import date

def get_month_key(stmt):
    """Get month key for a statement."""
    if hasattr(stmt, 'statement_date'):
        return stmt.statement_date.replace(day=1)
    return stmt.statement_month

def compare_accounts():
    # Get all RBC accounts
    rbc_accounts = Account.objects.filter(rbc_monthly_statements__isnull=False).distinct()
    
    print("=== RECONCILIATION REPORT: RBC ACCOUNTS (2020-Present) ===")
    print(f"Total RBC accounts: {rbc_accounts.count()}")
    print()
    
    for acct in rbc_accounts.order_by("account_id"):
        # Get RBC data for 2020+
        rbc_2020 = RbcMonthly.objects.filter(
            account=acct, statement_date__gte=date(2020, 1, 1)
        ).order_by("statement_date", "currency")
        
        # Get CSV data for 2020+
        csv_2020 = CoreMonthlyStatement.objects.filter(
            account=acct, statement_month__gte=date(2020, 1, 1)
        ).order_by("statement_month", "currency")
        
        print(f"\n{acct.account_id} ({acct.name})")
        print(f"  RBC statements (2020+): {rbc_2020.count()}")
        print(f"  CSV statements (2020+): {csv_2020.count()}")
        
        # Sample comparison for 2026
        rbc_2026 = rbc_2020.filter(statement_date__year=2026)
        csv_2026 = csv_2020.filter(statement_month__year=2026)
        
        if rbc_2026.exists() and csv_2026.exists():
            print("\n  2026 Comparison (sample):")
            print(f"  {'Month':<10} {'RBC Begin':>12} {'RBC End':>12} {'CSV Begin':>12} {'CSV End':>12} {'Diff End':>10}")
            print("  " + "-"*70)
            
            # Match by month
            rbc_by_month = {}
            for r in rbc_2026:
                rbc_by_month[r.statement_date.replace(day=1)] = r
            
            csv_by_month = {}
            for c in csv_2026:
                csv_by_month[c.statement_month] = c
            
            for month in sorted(set(rbc_by_month.keys()) | set(csv_by_month.keys()))[:3]:
                rbc = rbc_by_month.get(month)
                csv = csv_by_month.get(month)
                if rbc and csv:
                    diff = abs((rbc.end_total_market_value or Decimal("0")) - (csv.ending_market_value or Decimal("0")))
                    print(f"  {month.strftime('%Y-%m'):<10} "
                          f"{rbc.begin_total_market_value:>12.2f} {rbc.end_total_market_value:>12.2f} "
                          f"{csv.beginning_market_value:>12.2f} {csv.ending_market_value:>12.2f} "
                          f"{diff:>10.2f}")
        
        # Check for data gaps
        if rbc_2020.exists():
            rbc_years = set(r.statement_date.year for r in rbc_2020)
            csv_years = set(c.statement_month.year for c in csv_2020) if csv_2020.exists() else set()
            
            missing_years = rbc_years - csv_years
            if missing_years:
                print(f"  WARNING: Missing CSV data for years: {sorted(missing_years)}")
            extra_years = csv_years - rbc_years
            if extra_years:
                print(f"  NOTE: CSV has data for years not in RBC: {sorted(extra_years)}")

if __name__ == '__main__':
    compare_accounts()
