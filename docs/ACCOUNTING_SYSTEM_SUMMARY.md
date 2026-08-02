# Accounting System Design — Executive Summary

## Overview

Build a double-entry accounting system where:
- **CSV Activity files** are the source of truth (imported into `core_transaction`)
- **Holdings CSV** and **RBC PDF** are used for verification only
- **Manual batch processing** gives you full control over when ledger is written and months are closed
- **Audit trail** traces every ledger entry back to source CSV file

## Key Design Decisions (from your answers)

✅ **Keep `PositionHistory`** — used for verification, not primary data source  
✅ **Average cost basis** — no need for FIFO/LIFO lot tracking  
✅ **Batch processing** — write ledger after CSV import, not during  
✅ **Manual closure** — you decide when to close each month  
✅ **Month-end lock** — once closed, no changes allowed (unless manually reopened)

## Data Flow

```
1. CSV Activity Import
   └─> core_transaction (with tx_hash for deduplication)
   
2. Write Ledger (manual batch)
   └─> LedgerAccount (created if doesn't exist)
   └─> LedgerEntry (one per transaction, double-entry)
   └─> MonthlyLedgerBalance (month-end snapshot)
   
3. Verify Ledger (manual)
   └─> Compare vs Holdings CSV (quantities)
   └─> Compare vs RBC PDF (quantities + market values)
   └─> Generate markdown reports
   
4. Close Month (manual)
   └─> Lock month (no further changes)
   └─> Record closure timestamp
```

## New Models (4 total)

### 1. LedgerAccount
- Represents cash or position accounts
- One per (account, currency) for cash
- One per (account, symbol, currency) for positions
- Tracks current quantity and cost basis

### 2. LedgerEntry
- Double-entry transaction records
- Every `Transaction` creates 1-2 `LedgerEntry` records
- Links back to source `Transaction` (audit trail)
- Tracks statement_month for period reporting

### 3. MonthlyLedgerBalance
- Month-end snapshot of all ledger accounts
- Computed from `LedgerEntry` records
- Includes verification fields for Holdings CSV and RBC PDF
- Can be locked after month closure

### 4. LedgerMonthClosure
- Tracks closure status per (account, month, currency)
- Once closed, no changes allowed
- Records closure timestamp and reason

## Transaction Mapping

| Transaction Type | Cash Ledger | Position Ledger |
|-----------------|-------------|-----------------|
| BUY | CREDIT (cash out) | DEBIT (shares in) |
| SELL | DEBIT (cash in) | CREDIT (shares out) |
| DIV/DIST/INT | DEBIT (cash in) | — |
| DEP/CONTRIB | DEBIT (cash in) | — |
| WDRAW/TFR OUT | CREDIT (cash out) | — |
| DIV REIN | CREDIT (div used) | DEBIT (shares in) |
| SPLIT | — | DEBIT (qty up, cost/share down) |

## Management Commands (new)

```powershell
# Write ledger entries for a month
python manage.py write_ledger --account 90000001 --month 2024-01

# Verify ledger vs Holdings CSV and RBC PDF
python manage.py verify_ledger --account 90000001 --month 2024-01

# Close a month (lock it)
python manage.py close_month --account 90000001 --month 2024-01 --currency CAD

# Reopen a month (if corrections needed)
python manage.py reopen_month --account 90000001 --month 2024-01 --currency CAD --reason "..."

# Year-end closure
python manage.py close_year --account 90000001 --year 2024
```

## Verification Reports (markdown)

### Report 1: Cash & Amounts
```markdown
# Cash Ledger — Account 90000001 CAD — 2024-01

## Opening Balance: $50,000.00

## Transactions
| Date | Type | Description | Debit | Credit | Balance |
|------|------|-------------|-------|--------|---------|
| 2024-01-05 | DEP | Deposit | $5,000 | | $55,000 |
| 2024-01-10 | BUY | AAPL | | $10,000 | $45,000 |

## Closing Balance
- Computed: $45,200.00
- RBC PDF: $45,200.00 ✓
```

### Report 2: Stock Quantities
```markdown
# Position Ledger — Account 90000001 CAD — 2024-01

## Closing Positions
| Symbol | Computed | Holdings CSV | RBC PDF | Status |
|--------|----------|--------------|---------|--------|
| AAPL | 150 | 150 | 150 | ✓ MATCHED |
```

## Corporate Actions

**Stock Splits**: Add `SPLIT` transaction type
- Quantity × ratio, Price ÷ ratio, Cost basis per share ÷ ratio
- Total cost basis unchanged

**Mergers**: Use existing `SOLD` + `BOUGHT`
- Old symbol sold, new symbol bought
- Cost basis carries over

**Spin-offs**: Use `DIST` with new symbol
- Cost basis allocated based on FMV ratio

## Implementation Phases

### Phase 1: Create Models (1-2 days)
- Add 4 new models to `core/models.py`
- Add `SPLIT` to `TransactionType` choices
- Create migration

### Phase 2: Write Ledger Command (2-3 days)
- `manage.py write_ledger` command
- Transaction → LedgerEntry mapping logic
- Update LedgerAccount balances
- Create MonthlyLedgerBalance snapshots

### Phase 3: Verification Command (2-3 days)
- `manage.py verify_ledger` command
- Compare ledger vs Holdings CSV
- Compare ledger vs RBC PDF
- Generate markdown reports

### Phase 4: Closure Commands (1 day)
- `manage.py close_month`
- `manage.py close_year`
- `manage.py reopen_month`

### Phase 5: Testing & Backfill (2-3 days)
- Test with one account (90000001)
- Backfill historical data
- Verify against existing data

**Total: 8-12 days**

## Audit Trail

Every ledger entry traces back to source:
```
LedgerEntry 
  → source_transaction (Transaction)
    → tx_hash (unique from CSV)
    → statement (BrokerStatement)
      → raw_filename (CSV file path)
```

## Next Steps

1. **Review this design** — does it meet your requirements?
2. **Approve the model structure** — any changes needed?
3. **Start Phase 1** — create the models
4. **Test with one account** — verify the logic works
5. **Backfill historical data** — build ledger for all months

## Questions?

Let me know if you have any questions or if anything needs clarification before we start implementation.
