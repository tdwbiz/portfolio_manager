import os
import sys
import django

sys.path.insert(0, r'C:\AI-DevTeam\projects\portfolio_manager')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
django.setup()

try:
    from core.models import LedgerAccount, LedgerEntry, MonthlyLedgerBalance, LedgerMonthClosure
    print("✓ Models imported successfully")
    print(f"  LedgerAccount: {LedgerAccount._meta.db_table}")
    print(f"  LedgerEntry: {LedgerEntry._meta.db_table}")
    print(f"  MonthlyLedgerBalance: {MonthlyLedgerBalance._meta.db_table}")
    print(f"  LedgerMonthClosure: {LedgerMonthClosure._meta.db_table}")
except Exception as e:
    print(f"✗ Error importing models: {e}")
    import traceback
    traceback.print_exc()
