# Accounting System Update - Option C Implementation

## Changes Made

### 1. Added INCOME Account Type
- **LedgerAccount** now supports 3 types: CASH, POSITION, INCOME
- Income accounts track dividends, interest, and other revenue
- Each income account is per (account, income_type, currency)
- Example: "90000001 DIVIDEND CAD", "90000001 INTEREST USD"

### 2. Double-Entry for Income Transactions
**Before (Option A - Simple):**
```
DIV $1,000 from VDY:
  DEBIT: Cash +$1,000
```

**After (Option C - Proper Accounting):**
```
DIV $1,000 from VDY:
  DEBIT:  Cash +$1,000
  CREDIT: Income (DIVIDEND) +$1,000
```

### 3. Related Symbol Field
- Added `related_symbol` to LedgerEntry
- For cash entries, shows which security generated the transaction
- Example: Cash DEBIT for "DIV VDY" has related_symbol="VDY"

## Transaction Mapping (Updated)

| Transaction Type | Cash Entry | Position Entry | Income Entry |
|-----------------|------------|----------------|--------------|
| BUY/BOUGHT | CREDIT (cash out) | DEBIT (shares in) | — |
| SELL/SOLD | DEBIT (cash in) | CREDIT (shares out) | — |
| DIV/DIST/INT | DEBIT (cash in) | — | CREDIT (income) |
| DEP/DEPOSIT/CONTRIB | DEBIT (cash in) | — | — |
| WDRAW/WITHDRAWAL/TFR OUT | CREDIT (cash out) | — | — |
| DIV REIN | — | DEBIT (shares in) | CREDIT (income used) |
| SPLIT | — | DEBIT (qty up) | — |

## Benefits of Option C

1. **Complete Audit Trail**: Every transaction has balanced entries
2. **Income Tracking**: Separate accounts for dividends, interest, etc.
3. **P&L Reporting**: Easy to calculate total income by type
4. **Tax Reporting**: Income accounts map directly to tax categories
5. **Market Value Tracking**: MonthlyLedgerBalance tracks actual market values separately from cost basis

## How It Works

### Example: VDY Dividend of $1,000

**Ledger Entries Created:**
```
1. DEBIT  Cash (CAD)           +$1,000  related_symbol=VDY
2. CREDIT Income-DIVIDEND (CAD) +$1,000  related_symbol=VDY
```

**Account Balances After:**
```
Cash (CAD):              +$1,000
Income-DIVIDEND (CAD):   +$1,000 (cumulative total)
VDY Position:            unchanged (shares and cost basis)
```

**Monthly Market Value:**
- MonthlyLedgerBalance tracks actual market value from Holdings CSV
- Shows VDY position at market price, not cost basis
- Difference between market value and cost basis = unrealized gain/loss

## Commands to Run

### Step 1: Create Migration
```powershell
C:\AI-DevTeam\ai-env\Scripts\python.exe manage.py makemigrations core --settings=portfolio_manager.settings
```

### Step 2: Apply Migration
```powershell
C:\AI-DevTeam\ai-env\Scripts\python.exe manage.py migrate --settings=portfolio_manager.settings
```

### Step 3: Clear Old Ledger Data (Optional)
If you want to start fresh with the new double-entry logic:
```sql
-- In SQL Server Management Studio
DELETE FROM core_ledger_entry;
DELETE FROM core_monthly_ledger_balance;
DELETE FROM core_ledger_account;
```

### Step 4: Re-run write_ledger
```powershell
# For a specific month
C:\AI-DevTeam\ai-env\Scripts\python.exe manage.py write_ledger --account 90000001 --month 2024-01 --settings=portfolio_manager.settings

# For all open months
C:\AI-DevTeam\ai-env\Scripts\python.exe manage.py write_ledger --account 90000001 --all-open --settings=portfolio_manager.settings
```

### Step 5: Verify Results
```sql
-- Check ledger accounts by type
SELECT account_type, COUNT(*) as count
FROM core_ledger_account
GROUP BY account_type
ORDER BY account_type;

-- Check income entries
SELECT TOP 20
    le.entry_date,
    le.entry_type,
    la.account_type,
    la.symbol as income_type,
    le.related_symbol,
    le.cash_amount,
    le.description
FROM core_ledger_entry le
JOIN core_ledger_account la ON le.ledger_account_id = la.id
WHERE la.account_type = 'INCOME'
ORDER BY le.entry_date DESC;

-- Check double-entry balance (should be ~0 for each month)
SELECT 
    statement_month,
    SUM(CASE WHEN entry_type = 'DEBIT' THEN cash_amount ELSE 0 END) as total_debits,
    SUM(CASE WHEN entry_type = 'CREDIT' THEN cash_amount ELSE 0 END) as total_credits,
    SUM(CASE WHEN entry_type = 'DEBIT' THEN cash_amount ELSE 0 END) + 
    SUM(CASE WHEN entry_type = 'CREDIT' THEN cash_amount ELSE 0 END) as net
FROM core_ledger_entry
WHERE ledger_account_id IN (
    SELECT id FROM core_ledger_account WHERE account_type IN ('CASH', 'INCOME')
)
GROUP BY statement_month
ORDER BY statement_month;
```

## What You'll See

### Ledger Accounts
```
CASH      CAD              qty=0        cost=0
CASH      USD              qty=0        cost=0
POSITION  CAD  RBF2010     qty=319      cost=3190
POSITION  CAD  VDY         qty=1000     cost=25000
INCOME    CAD  DIVIDEND    qty=0        cost=2138.72  (cumulative dividends)
INCOME    CAD  INTEREST    qty=0        cost=150.00   (cumulative interest)
```

### Ledger Entries for Dividend
```
2024-01-08  DEBIT   CASH      CAD      +$1,033.79  related=VDY  "DIST VDY"
2024-01-08  CREDIT  INCOME    CAD      +$1,033.79  related=VDY  "DIST VDY"
```

### Monthly Balance Snapshot
```
Month: 2024-01
VDY Position:
  - quantity: 1000 shares
  - cost_basis: $25,000
  - market_value: $28,000 (from Holdings CSV)
  - unrealized_gain: $3,000
```

## Next Steps

After verifying this works:
1. Build `verify_ledger` command to compare with Holdings CSV and RBC PDF
2. Build `close_month` command to lock months
3. Build management pages to view ledger status
4. Add reconciliation reports

## Questions?

Let me know if the double-entry logic makes sense or if you need adjustments!
