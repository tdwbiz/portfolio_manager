# Accounting Ledger System - Quick Start Guide

> **PARKED** (ledger/accounting layer — see docs/architecture.md §11)

## What's Been Built

✅ **4 New Database Models** - Ready to create tables  
✅ **Migration File** - `0034_ledger_models.py` created  
✅ **write_ledger Command** - Converts transactions to ledger entries  
✅ **Verification Scripts** - Check models are working  

## Quick Start (3 Steps)

### Step 1: Run Migration
```powershell
C:\AI-DevTeam\ai-env\Scripts\python.exe manage.py migrate --settings=portfolio_manager.settings
```

This creates 4 new tables:
- `core_ledger_account` - Cash and position accounts
- `core_ledger_entry` - Double-entry transactions
- `core_monthly_ledger_balance` - Month-end snapshots
- `core_ledger_month_closure` - Closure tracking

### Step 2: Verify Models Work
```powershell
C:\AI-DevTeam\ai-env\Scripts\python.exe scripts/verify_ledger_models.py
```

Expected output:
```
✓ Models imported successfully
  LedgerAccount table: core_ledger_account
  LedgerEntry table: core_ledger_entry
  ...
```

### Step 3: Test write_ledger
```powershell
# Dry run first (shows what would be done)
C:\AI-DevTeam\ai-env\Scripts\python.exe manage.py write_ledger --account 90000001 --month 2024-01 --dry-run

# Then actual run
C:\AI-DevTeam\ai-env\Scripts\python.exe manage.py write_ledger --account 90000001 --month 2024-01
```

## What Happens When You Run write_ledger

1. **Checks if month is closed** - Skips if already closed
2. **Gets all transactions** for that month from `core_transaction`
3. **Creates LedgerAccount** records if they don't exist (one per cash/position)
4. **Creates LedgerEntry** records (double-entry bookkeeping):
   - BUY → CREDIT cash + DEBIT position
   - SELL → DEBIT cash + CREDIT position
   - DIV → DEBIT cash
   - etc.
5. **Updates LedgerAccount** balances (quantity, cost_basis)
6. **Creates MonthlyLedgerBalance** snapshot

## Example: What Gets Created

For a BUY transaction:
```
Transaction: BUY 100 AAPL @ $150 = $15,000

Creates 2 LedgerEntry records:
1. CREDIT Cash Account: -$15,000 (cash out)
2. DEBIT Position Account: +100 shares, +$15,000 cost basis

Updates LedgerAccount balances:
- Cash: quantity unchanged, balance -$15,000
- AAPL Position: quantity +100, cost_basis +$15,000
```

## Next Steps (After Testing)

### Build verify_ledger Command
```powershell
python manage.py verify_ledger --account 90000001 --month 2024-01
```
- Compare ledger vs Holdings CSV
- Compare ledger vs RBC PDF
- Generate markdown reports

### Build close_month Command
```powershell
python manage.py close_month --account 90000001 --month 2024-01 --currency CAD
```
- Locks month (no further changes)
- Records closure timestamp

### Build reopen_month Command
```powershell
python manage.py reopen_month --account 90000001 --month 2024-01 --currency CAD --reason "Found missing transaction"
```
- Unlocks month for corrections

## Troubleshooting

### Migration Fails
```powershell
# Check for syntax errors
C:\AI-DevTeam\ai-env\Scripts\python.exe manage.py makemigrations --check --settings=portfolio_manager.settings

# If migration file has issues, delete and recreate
del core\migrations\0034_ledger_models.py
C:\AI-DevTeam\ai-env\Scripts\python.exe manage.py makemigrations core --settings=portfolio_manager.settings
```

### write_ledger Fails
```powershell
# Check if account exists
C:\AI-DevTeam\ai-env\Scripts\python.exe -c "import os, sys, django; sys.path.insert(0, '.'); os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings'); [PERSON_NAME](); from core.models import Account; print(Account.objects.filter(account_id='90000001').exists())"

# Check if transactions exist
C:\AI-DevTeam\ai-env\Scripts\python.exe -c "import os, sys, django; sys.path.insert(0, '.'); os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings'); [PERSON_NAME](); from core.models import Transaction, Account; acct = Account.objects.get(account_id='90000001'); print(Transaction.objects.filter(account=acct).count())"
```

## Files Reference

### Design Documents
- `docs/ACCOUNTING_SYSTEM_DESIGN.md` - Full design (detailed)
- `docs/ACCOUNTING_SYSTEM_SUMMARY.md` - Executive summary
- `docs/ACCOUNTING_IMPLEMENTATION_STATUS.md` - Implementation status

### Code Files
- `core/models.py` - 4 new models (lines 1087-1300+)
- `core/migrations/0034_ledger_models.py` - Migration
- `core/management/commands/write_ledger.py` - Write ledger command

### Scripts
- `scripts/verify_ledger_models.py` - Verify models work
- `scripts/test_models.py` - Simple model test
- `scripts/check_db_state.py` - Check database state

## Questions?

1. **Ready to run migration?** - Say "yes" and I'll run it
2. **Want to test first?** - We can verify models work before migration
3. **Need changes?** - Let me know what to adjust
4. **Move to next phase?** - Build verify_ledger command

## Current Status

**Phase 1: Models** ✅ COMPLETE  
**Phase 2: write_ledger** ✅ COMPLETE  
**Phase 3: Migration** ⏳ READY TO RUN  
**Phase 4: verify_ledger** ⏳ NEXT  
**Phase 5: close_month** ⏳ AFTER VERIFY  
**Phase 6: Management Pages** ⏳ FUTURE  
