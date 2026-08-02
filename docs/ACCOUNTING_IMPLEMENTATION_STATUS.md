# Accounting Ledger System - Implementation Status

## Phase 1: Models Created ✓

### New Models Added to `core/models.py`:

1. **LedgerAccount** (Table: `core_ledger_account`)
   - Represents cash or position accounts
   - Fields: account, account_type (CASH/POSITION), currency, symbol, quantity, cost_basis_total, cost_basis_per_unit
   - Unique constraint: (account, account_type, currency, symbol)
   - Tracks current balances

2. **LedgerEntry** (Table: `core_ledger_entry`)
   - Double-entry transaction records
   - Fields: ledger_account, entry_date, entry_type (DEBIT/CREDIT), cash_amount, quantity_change, cost_basis_change, source_transaction, description, statement_month
   - Links to source Transaction for audit trail
   - Ordered by entry_date descending

3. **MonthlyLedgerBalance** (Table: `core_monthly_ledger_balance`)
   - Month-end snapshots
   - Fields: ledger_account, statement_month, quantity, cost_basis_total, cost_basis_per_unit, market_price_csv, market_value_csv, market_price_rbc, market_value_rbc, quantity_match_csv, quantity_match_rbc, reconciliation_notes, is_closed, closed_at, closed_by
   - Unique constraint: (ledger_account, statement_month)
   - Includes verification fields for Holdings CSV and RBC PDF

4. **LedgerMonthClosure** (Table: `core_ledger_month_closure`)
   - Tracks closure status
   - Fields: account, statement_month, currency, is_closed, closed_at, closed_by, closure_notes, csv_holdings_verified, rbc_pdf_verified, verification_report_path
   - Unique constraint: (account, statement_month, currency)
   - Once closed, no changes allowed

### Migration Created:
- `core/migrations/0034_ledger_models.py`
- Creates all 4 tables with proper indexes and constraints

## Phase 2: Management Command Created ✓

### `write_ledger` Command:
- Location: `core/management/commands/write_ledger.py`
- Usage:
  ```powershell
  # Write ledger for specific month
  python manage.py write_ledger --account 90000001 --month 2024-01
  
  # Write all open months
  python manage.py write_ledger --account 90000001 --all-open
  
  # Dry run (show what would be done)
  python manage.py write_ledger --account 90000001 --month 2024-01 --dry-run
  ```

### Transaction Mapping Logic:
| Transaction Type | Cash Ledger | Position Ledger |
|-----------------|-------------|-----------------|
| BUY/BOUGHT | CREDIT (cash out) | DEBIT (shares in) |
| SELL/SOLD | DEBIT (cash in) | CREDIT (shares out) |
| DIV/DIST/INT | DEBIT (cash in) | — |
| DEP/DEPOSIT/CONTRIB | DEBIT (cash in) | — |
| WDRAW/WITHDRAWAL/TFR OUT | CREDIT (cash out) | — |
| DIV REIN | — | DEBIT (shares in) |
| SPLIT | — | DEBIT (qty up, cost/share down) |

### Features:
- Creates LedgerAccount records automatically if they don't exist
- Creates LedgerEntry records for each Transaction
- Updates LedgerAccount balances (quantity, cost_basis)
- Creates MonthlyLedgerBalance snapshots
- Checks for closed months and skips them
- Supports dry-run mode

## Phase 3: Next Steps (Not Yet Implemented)

### 1. Run Migration
```powershell
python manage.py migrate --settings=portfolio_manager.settings
```

### 2. Test with Sample Data
```powershell
# Verify models are working
python scripts/verify_ledger_models.py

# Write ledger for one account
python manage.py write_ledger --account 90000001 --month 2024-01
```

### 3. Create Verification Command (Not Yet Built)
```powershell
python manage.py verify_ledger --account 90000001 --month 2024-01
```
- Compare ledger vs Holdings CSV
- Compare ledger vs RBC PDF
- Generate markdown reports

### 4. Create Closure Commands (Not Yet Built)
```powershell
python manage.py close_month --account 90000001 --month 2024-01 --currency CAD
python manage.py reopen_month --account 90000001 --month 2024-01 --currency CAD --reason "..."
python manage.py close_year --account 90000001 --year 2024
```

### 5. Create Management Pages (Future)
- Admin interface for viewing ledger accounts
- Month-end closure workflow
- Verification reports dashboard
- Reconciliation status tracking

## Testing Checklist

- [ ] Run migration successfully
- [ ] Verify models can be imported
- [ ] Test write_ledger with dry-run
- [ ] Test write_ledger with actual data
- [ ] Verify LedgerEntry records created correctly
- [ ] Verify LedgerAccount balances updated
- [ ] Verify MonthlyLedgerBalance snapshots created
- [ ] Test closed month detection
- [ ] Test audit trail (LedgerEntry → Transaction → CSV file)

## Known Issues / TODOs

1. **Migration needs to be run** - Tables not yet created in database
2. **Cost basis calculation** - Currently uses simple average cost, may need refinement for SELL transactions
3. **Opening balances** - Need to handle initial balances for accounts with historical data
4. **Market values** - MonthlyLedgerBalance.market_price_csv and market_price_rbc not yet populated
5. **Reconciliation logic** - verify_ledger command not yet built
6. **Closure logic** - close_month command not yet built

## Files Created/Modified

### Modified:
- `core/models.py` - Added 4 new models (LedgerAccount, LedgerEntry, MonthlyLedgerBalance, LedgerMonthClosure)

### Created:
- `core/migrations/0034_ledger_models.py` - Migration for new models
- `core/management/commands/write_ledger.py` - Management command to write ledger
- `scripts/verify_ledger_models.py` - Verification script
- `scripts/test_models.py` - Simple model test
- `docs/ACCOUNTING_SYSTEM_DESIGN.md` - Comprehensive design document
- `docs/ACCOUNTING_SYSTEM_SUMMARY.md` - Executive summary
- `docs/ACCOUNTING_IMPLEMENTATION_STATUS.md` - This file

## Next Actions

1. **Run the migration** to create tables in database
2. **Test write_ledger** with a sample account
3. **Verify data integrity** - check that ledger entries match transactions
4. **Build verify_ledger** command for reconciliation
5. **Build close_month** command for month-end closure
6. **Test end-to-end workflow** - import CSV → write ledger → verify → close

## Questions for User

1. Should we run the migration now to create the tables?
2. Which account should we test with first? (Recommend: 90000001)
3. Do you want to see the write_ledger command in action before we build verify_ledger?
4. Any changes needed to the transaction mapping logic?
