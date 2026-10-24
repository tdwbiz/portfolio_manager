# Accounting Ledger System Design

> **PARKED** (ledger/accounting layer — see docs/architecture.md §11)

## Current State Analysis

### What We Have:
- **PositionHistory**: Tracks holdings snapshots from CSV imports (351 records)
  - Fields: account, symbol, quantity, cost_basis_per_unit, total_cost_basis, market_price, market_value, currency, date_from, date_to
  - Issue: Only captures point-in-time snapshots, not continuous ledger

- **Transaction**: CSV activity imports (2,872 records)
  - Fields: account, date, symbol, type, quantity, price, amount, amount_cad, currency
  - Issue: No link to position cost basis changes

- **RbcMonthly/RbcMonthlyHolding**: PDF statement data
  - Provides market values and holdings snapshots
  - Issue: Unreliable transaction extraction, but good for balance verification

- **CoreMonthlyStatement**: Aggregated monthly data
  - Fields: beginning/ending market values, total buys/sells/dividends, net_flow
  - Issue: Doesn't track individual position changes

### What's Missing:
1. **Cash Ledger Accounts** - No tracking of cash balances per currency
2. **Position Ledger** - No continuous tracking of quantity/cost basis changes
3. **Double-Entry Bookkeeping** - No debit/credit tracking
4. **Monthly Closing Procedures** - No formal period-end processes
5. **Reconciliation Engine** - No systematic comparison of CSV vs PDF vs computed values

## Refined Design: CSV-Driven Accounting System

### Core Principles:

1. **CSV Activity is Source of Truth**: Ledger built from `core_transaction` (CSV imports)
2. **Holdings CSV & RBC PDF are Verification**: Used to verify ledger correctness
3. **Manual Batch Processing**: Import CSV → write ledger → verify → close month
4. **Audit Trail**: Every ledger entry traces to source CSV via `Transaction`
5. **Month-End Lock**: Once closed, no changes allowed

### Corporate Actions Handling:

**Stock Splits**: Add `SPLIT` transaction type
- CSV shows: `SPLIT` with ratio (e.g., 2:1)
- Effect: Quantity × ratio, Price ÷ ratio, Cost basis per share ÷ ratio
- Total cost basis unchanged

**Mergers/Acquisitions**: Use existing `SOLD` + `BOUGHT`
- Old symbol: `SOLD` at merger price
- New symbol: `BOUGHT` at merger price
- Cost basis carries over

**Spin-offs**: Use `DIST` with new symbol
- Cost basis allocated based on FMV ratio

### Data Models

#### 1. LedgerAccount
```python
class LedgerAccount(models.[PERSON_NAME]"""Represents a single ledger account (cash or position).

Created automatically when first transaction affects that account/symbol/currency.
"""
    ACCOUNT_TYPES = [
        ('CASH', 'Cash'),
        ('POSITION', 'Stock Position'),
    ]
    
    account = models.ForeignKey(Account, ...)  # Parent brokerage account
    account_type = models.CharField(max_length=10, choices=ACCOUNT_TYPES)
    currency = models.CharField(max_length=3)
    symbol = models.CharField(max_length=50, blank=True)  # Only for POSITION type
    
    # Current balance (updated as entries are added)
    quantity = models.DecimalField(...)  # Shares (POSITION) or 0 (CASH)
    cost_basis_total = models.DecimalField(...)  # Total cost basis
    cost_basis_per_unit = models.DecimalField(...)  # Average cost per share
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = [('account', 'account_type', 'currency', 'symbol')]
```

#### 2. LedgerEntry
```python
class LedgerEntry(models.[PERSON_NAME]"""Individual ledger transaction (double-entry).

Every Transaction creates 1-2 LedgerEntry records:
- BUY: CREDIT cash + DEBIT position
- SELL: DEBIT cash + CREDIT position
- DIV/DIST/INT: DEBIT cash only
- DEP/CONTRIB: DEBIT cash only
- WDRAW/TFR OUT: CREDIT cash only
- DIV REIN: DEBIT position + CREDIT cash (dividend used)
- SPLIT: DEBIT position (quantity up, cost basis per share down)
"""
    ENTRY_TYPES = [
        ('DEBIT', 'Debit'),
        ('CREDIT', 'Credit'),
    ]
    
    ledger_account = models.ForeignKey(LedgerAccount, ...)
    entry_date = models.DateField()
    entry_type = models.CharField(max_length=6, choices=ENTRY_TYPES)
    
    # For CASH accounts
    cash_amount = models.DecimalField(...)  # Positive for DEBIT, negative for CREDIT
    
    # For POSITION accounts
    quantity_change = models.DecimalField(...)  # Shares bought/sold
    cost_basis_change = models.DecimalField(...)  # Cost basis change
    
    # Audit trail: link back to source Transaction (which links to CSV file)
    source_transaction = models.ForeignKey(Transaction, ...)
    description = models.CharField(max_length=255)
    
    # Period tracking
    statement_month = models.DateField()  # First day of month
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        indexes = [
            ('ledger_account', 'statement_month'),
            ('source_transaction',),
        ]
```

#### 3. MonthlyLedgerBalance
```python
class MonthlyLedgerBalance(models.Model):
    """Month-end snapshot of all ledger accounts.
    
    Built from LedgerEntry records for the month.
    Verified against Holdings CSV and RBC PDF.
    """
    ledger_account = models.ForeignKey(LedgerAccount, ...)
    statement_month = models.DateField()
    
    # Balances at month end (computed from LedgerEntry)
    quantity = models.DecimalField(...)
    cost_basis_total = models.DecimalField(...)
    cost_basis_per_unit = models.DecimalField(...)
    
    # Market values (from Holdings CSV or RBC PDF for verification)
    market_price_csv = models.DecimalField(null=True, ...)  # From Holdings CSV
    market_value_csv = models.DecimalField(null=True, ...)
    market_price_rbc = models.DecimalField(null=True, ...)  # From RBC PDF
    market_value_rbc = models.DecimalField(null=True, ...)
    
    # Reconciliation status
    quantity_match_csv = models.BooleanField(null=True)  # Matches Holdings CSV?
    quantity_match_rbc = models.BooleanField(null=True)  # Matches RBC PDF?
    reconciliation_notes = models.TextField(blank=True)
    
    # Month closure
    is_closed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True)
    closed_by = models.CharField(max_length=100, blank=True)
    
    class Meta:
        unique_together = [('ledger_account', 'statement_month')]
```

#### 4. LedgerMonthClosure
```python
class LedgerMonthClosure(models.Model):
    """Tracks month-end closure status for each account.
    
    Once closed, no LedgerEntry can be added/modified for that month.
    """
    account = models.ForeignKey(Account, ...)
    statement_month = models.DateField()
    currency = models.CharField(max_length=3)
    
    is_closed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True)
    closed_by = models.CharField(max_length=100, blank=True)
    closure_notes = models.TextField(blank=True)
    
    # Verification results
    csv_holdings_verified = models.BooleanField(null=True)
    rbc_pdf_verified = models.BooleanField(null=True)
    verification_report_path = models.CharField(max_length=500, blank=True)
    
    class Meta:
        unique_together = [('account', 'statement_month', 'currency')]
```

### Workflow: Manual Batch Processing

#### Step 1: Import CSV Activity
```powershell
# Import Activity CSVs from inbox/
python manage.py import_csv

# Verify no duplicates
python scripts/check_csv_vs_db.py
```
- Creates `Transaction` records in `core_transaction`
- Each Transaction has `tx_hash` for deduplication
- Each Transaction links to source CSV via `FileImportLog`

#### Step 2: Write Ledger (Manual Batch Command)
```powershell
# Write ledger entries for a specific account and month
python manage.py write_ledger --account 90000001 --month 2024-01

# Or write all open months for an account
python manage.py write_ledger --account 90000001 --all-open
```
- Creates `LedgerAccount` records if they don't exist
- Creates `LedgerEntry` records for each `Transaction`
- Updates `LedgerAccount` balances (quantity, cost_basis)
- **Does NOT close the month** (allows verification first)

#### Step 3: Verify Ledger
```powershell
# Generate verification report comparing ledger vs Holdings CSV vs RBC PDF
python manage.py verify_ledger --account 90000001 --month 2024-01
```
- Compares `MonthlyLedgerBalance.quantity` vs Holdings CSV
- Compares `MonthlyLedgerBalance.quantity` vs RBC PDF
- Generates markdown report showing discrepancies
- User reviews report and decides if month is correct

#### Step 4: Close Month (Manual)
```powershell
# Close a month after verification
python manage.py close_month --account 90000001 --month 2024-01 --currency CAD
```
- Sets `LedgerMonthClosure.is_closed = True`
- Sets `MonthlyLedgerBalance.is_closed = True`
- **Locks the month**: No further `LedgerEntry` can be added/modified
- Records closure timestamp and user

#### Step 5: Year-End Closure
```powershell
# Year-end procedure
python manage.py close_year --account 90000001 --year 2024
```
- Verifies all 12 months are closed
- Generates annual summary report
- Computes realized gains/losses for tax reporting
- Locks entire year

### Transaction → LedgerEntry Mapping

| Transaction Type | Cash LedgerEntry | Position LedgerEntry |
|-----------------|------------------|----------------------|
| **BUY** | CREDIT (cash out) | DEBIT (shares in, cost basis up) |
| **SELL** | DEBIT (cash in) | CREDIT (shares out, cost basis down) |
| **DIV/DIST** | DEBIT (cash in) | None |
| **INT** | DEBIT (cash in) | None |
| **DEP/CONTRIB** | DEBIT (cash in) | None |
| **WDRAW/TFR OUT** | CREDIT (cash out) | None |
| **DIV REIN** | CREDIT (dividend used) | DEBIT (shares in, cost basis up) |
| **INTEREST_RECEIVED** | None | DEBIT (shares in, cost basis up) |
| **SPLIT** | None | DEBIT (quantity up, cost per share down) |

### Audit Trail

Every `LedgerEntry` has:
- `source_transaction` → links to `Transaction` record
- `Transaction.tx_hash` → unique identifier from CSV
- `Transaction.statement` → links to `BrokerStatement` (which has `raw_filename`)

So you can trace:
```
LedgerEntry → Transaction → BrokerStatement → raw CSV filename
```

### Month Closure Rules

1. **Before Closure**:
   - Can add/modify `LedgerEntry` records
   - Can re-run `write_ledger` to recalculate
   - Can verify against Holdings CSV and RBC PDF

2. **After Closure**:
   - Cannot add/modify `LedgerEntry` for that month
   - Cannot modify `MonthlyLedgerBalance`
   - Any corrections require reopening the month (admin override)

3. **Reopening a Month** (if needed):
   ```powershell
   python manage.py reopen_month --account 90000001 --month 2024-01 --currency CAD --reason "Found missing transaction"
   ```
   - Sets `is_closed = False`
   - Records reason and timestamp
   - Allows corrections

### Verification Reports

For each month, generate two markdown reports:

#### Report 1: Cash & Amounts
```markdown
# Cash Ledger — Account 90000001 CAD — 2024-01

## Opening Balance
- Cash: $50,000.00

## Transactions
| Date | Type | Description | Debit | Credit | Balance |
|------|------|-------------|-------|--------|---------|
| 2024-01-05 | DEP | Deposit | $5,000 | | $55,000 |
| 2024-01-10 | BUY | AAPL | | $10,000 | $45,000 |
| 2024-01-15 | DIV | AAPL dividend | $200 | | $45,200 |

## Closing Balance
- Computed: $45,200.00
- RBC PDF: $45,200.00 ✓
- Status: MATCHED
```

#### Report 2: Stock Quantities
```markdown
# Position Ledger — Account 90000001 CAD — 2024-01

## Opening Positions
| Symbol | Quantity | Cost Basis |
|--------|----------|------------|
| AAPL | 100 | $15,000.00 |

## Transactions
| Date | Type | Symbol | Qty Change | New Qty | Cost Basis |
|------|------|--------|------------|---------|------------|
| 2024-01-10 | BUY | AAPL | +50 | 150 | $25,000.00 |

## Closing Positions
| Symbol | Computed Qty | Holdings CSV Qty | RBC PDF Qty | Status |
|--------|--------------|------------------|-------------|--------|
| AAPL | 150 | 150 | 150 | ✓ MATCHED |

## Market Values
| Symbol | Computed MV | Holdings CSV MV | RBC PDF MV |
|--------|-------------|-----------------|------------|
| AAPL | $30,000.00 | $30,000.00 | $30,000.00 |
```

### Advantages of This Design

1. **CSV is Source of Truth**: Ledger built from verified CSV data
2. **Verification Built-In**: Holdings CSV and RBC PDF used to verify correctness
3. **Manual Control**: You decide when to write ledger and when to close month
4. **Audit Trail**: Every entry traces back to source CSV file
5. **Month-End Lock**: Prevents accidental changes after closure
6. **Flexible**: Can reopen months if corrections needed
7. **Reporting**: Clear markdown reports for verification

### Implementation Plan

#### Phase 1: Create Models (1-2 days)
- `LedgerAccount`
- `LedgerEntry`
- `MonthlyLedgerBalance`
- `LedgerMonthClosure`
- Add `SPLIT` to `TransactionType` choices

#### Phase 2: Write Ledger Command (2-3 days)
- `manage.py write_ledger` command
- Transaction → LedgerEntry mapping logic
- Update `LedgerAccount` balances
- Create `MonthlyLedgerBalance` snapshots

#### Phase 3: Verification Command (2-3 days)
- `manage.py verify_ledger` command
- Compare ledger vs Holdings CSV
- Compare ledger vs RBC PDF
- Generate markdown reports

#### Phase 4: Closure Commands (1 day)
- `manage.py close_month` command
- `manage.py close_year` command
- `manage.py reopen_month` command (for corrections)

#### Phase 5: Testing & Backfill (2-3 days)
- Test with one account (e.g., 90000001)
- Backfill historical data
- Verify against existing Holdings CSV and RBC PDF

Total estimated time: 8-12 days
