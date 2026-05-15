# Feature Spec: RBC Import, CAD Conversion & Auto-Process Inbox

Project name: portfolio_manager
Customer: tdwdev
**Database:** Microsoft SQL Server (MSSQL) — existing DB `PortfolioDEV`, connection via `.env`
**Django version:** 5.1 (existing project — do NOT recreate, update in place)
**Feature branch:** `feature/rbc-cad-autoimport`

---

## Context

Spec 01 bootstrapped the Django project at `C:\AI-DevTeam\projects\portfolio_manager`.
Spec 02 added IB Transaction History CSV import, a Django management command, duplicate detection,
and tests. The `core_brokerstatement` and `core_transaction` tables exist in `PortfolioDEV`.

This spec adds:
- Real RBC Activity Export CSV parsing
- Smarter IB format detection (Activity Statement vs Transaction History — detection logic already
  exists in `imports/parsers.py`, but it must be verified and tested against real format samples)
- Exchange rate and CAD amount columns on the `Transaction` model
- An auto-import management command that scans an inbox folder, imports all CSVs, and moves
  processed files to a done folder
- A `FileImportLog` model to record every file that was processed
- All tests must use SQLite in-memory (via `portfolio_manager/settings_test.py`) — do NOT attempt
  to create or drop a SQL Server database during testing

---

## File Layout for Broker CSVs

Place incoming files in:
```
C:\AI-DevTeam\projects\portfolio_manager\inbox\
```

After successful import, move them to:
```
C:\AI-DevTeam\projects\portfolio_manager\processed\
```

Both folders must be created if they do not exist.
Both folders are already in `.gitignore` — do NOT add CSV files to git.
Add `inbox/` and `processed/` to `.gitignore` if not already present.

---

## Requirements

### 1. RBC Activity Export CSV Parser

The real RBC export format has the following structure:

**Header block (skip everything before the data header row):**
```
"Activity Export as of Apr 24, 2026 at 6:51:18 pm ET"
(blank lines and metadata rows...)
"Exchange Rate: 1 USD = 1.3697 CAD"
(account summary table...)
"208 Activities"
"Date","Activity","Symbol","Symbol Description","Quantity","Price","Settlement Date","Account","Value","Currency","Description"
```

**Data rows (one per activity):**
```
"April 27, 2026","Buy","SOXX","ISHARES TR ...","-1","460.91","April 27, 2026","90000006","450.96","USD","..."
```

**Column mapping (0-based):**

| Column | Name | Maps to |
|--------|------|---------|
| 0 | Date | `transaction.date` (parse "Month DD, YYYY") |
| 1 | Activity | `transaction.type` — map: Buy→BUY, Sell→SELL, Dividends→DIV, Others→skip |
| 2 | Symbol | `transaction.symbol` |
| 3 | Symbol Description | (ignore) |
| 4 | Quantity | `transaction.quantity` — take absolute value |
| 5 | Price | `transaction.price` |
| 6 | Settlement Date | (ignore) |
| 7 | Account | (ignore for now) |
| 8 | Value | `transaction.amount` — take absolute value |
| 9 | Currency | `transaction.currency` |
| 10 | Description | (ignore) |

- Commission is not stated in RBC Activity Export — calculate it as:
  `commission = abs(abs(quantity) * price - abs(value))` (the difference between gross and net)
  Round to 2 decimal places. If the result is negative or > $50, store `Decimal('0.00')` (data anomaly).
- Skip rows where Activity is not Buy, Sell, or Dividends
- Skip the header row (detect by checking if first column equals "Date")
- The exchange rate line (`"Exchange Rate: 1 USD = X.XXXX CAD"`) must be extracted from
  the header block and stored on each imported transaction as `exchange_rate_cad`

**Function signature (update existing `parse_rbc_csv` in `imports/parsers.py`):**
```python
def parse_rbc_csv(file_path: str) -> Tuple[List[Dict[str, Any]], Decimal]:
    """Returns (trades_list, usd_to_cad_rate). Rate is Decimal('1.0') if not found."""
```

---

### 2. IB Format Auto-Detection (verify and test existing logic)

The existing `parse_ib_csv` in `imports/parsers.py` already auto-detects:
- **Transaction History format**: first column = "Transaction History", second = "Header"/"Data"
- **Activity Statement format**: multi-section CSV with "Trades" section header

Add explicit tests for both formats. The detection must be robust — do not rely on filename.

---

### 3. Model Changes — `core/models.py`

Add to the **`Transaction`** model:

```python
exchange_rate_cad = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal('1.000000'))
amount_cad = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
```

`amount_cad` must be calculated at import time:
- If `currency == 'CAD'`: `amount_cad = amount`
- If `currency == 'USD'`: `amount_cad = amount * exchange_rate_cad`

Add a new model **`FileImportLog`** to `core/models.py`:

```python
class FileImportLog(models.Model):
    filename        = models.CharField(max_length=255)
    broker          = models.CharField(max_length=3)  # IB or RBC
    imported_at     = models.DateTimeField(auto_now_add=True)
    records_imported = models.IntegerField(default=0)
    records_skipped  = models.IntegerField(default=0)
    status          = models.CharField(max_length=20, default='success')  # success / partial / error
    error_message   = models.TextField(blank=True, default='')
```

---

### 4. Management Command: `auto_import`

**Location:** `imports/management/commands/auto_import.py`

**Usage:**
```
python manage.py auto_import
python manage.py auto_import --broker IB
python manage.py auto_import --broker RBC
python manage.py auto_import --dry-run
```

**Behaviour:**
- Scan the `inbox/` folder (relative to `BASE_DIR`) for `*.csv` files
- Detect broker from file content (do not rely on filename):
  - If first data line starts with `"Transaction History"` or `"Statement"` → IB
  - If file contains `"Activity Export"` or column header `"Activity"` → RBC
  - If `--broker` is specified, skip auto-detection and use that broker
- For each file:
  1. Parse the CSV using the appropriate parser
  2. Import transactions (same duplicate-detection logic as `import_statements`)
  3. Create a `FileImportLog` record
  4. Move the file from `inbox/` to `processed/` (append timestamp to filename to avoid collisions,
     e.g. `FILENAME_20260427_152300.csv`)
  5. Log to `logs/portfolio_manager.log` with filename, broker detected, count imported/skipped
- If `--dry-run`: parse and report, do not write to DB and do not move files
- If a file fails to parse: log the error, write a `FileImportLog` with `status='error'`,
  move the file to `processed/errors/`, continue with remaining files
- Print a summary at the end: total files processed, total transactions imported, total skipped

---

### 5. Update `import_statements` command

Update the existing `import_statements` command to also:
- Accept and store `exchange_rate_cad` (pass `Decimal('1.0')` for IB if not available)
- Populate `amount_cad` on every transaction at import time

---

### 6. Tests (`imports/tests/test_import_statements.py` and new `imports/tests/test_auto_import.py`)

**All tests must use:**
```python
--settings=portfolio_manager.settings_test
```
which uses SQLite in-memory. Do NOT attempt MSSQL connections in tests.

**Required tests:**

#### `test_import_statements.py` (update existing):
- Update IB test fixture to use Transaction History format (matches real files)
- Add test for IB Activity Statement format (multi-section with "Trades" header)
- Update RBC test fixture to match the real RBC Activity Export format (with header block,
  exchange rate line, quoted fields, "Month DD, YYYY" date format)
- Verify `amount_cad` is calculated correctly for USD and CAD transactions
- Verify `exchange_rate_cad` is stored correctly

#### `test_auto_import.py` (new):
- Test that `auto_import` scans `inbox/`, imports, and moves files to `processed/`
- Test broker auto-detection from file content
- Test `--dry-run` — no DB writes, no files moved
- Test error handling — bad CSV in inbox does not stop processing of other files
- Test `FileImportLog` records are created correctly
- Use `tempfile.mkdtemp()` for inbox/processed dirs in tests (do NOT use the real `inbox/` folder)

---

## Constraints

- Do NOT run `python manage.py migrate` — migrations are a manual step
- Do NOT push to git — output `manual_steps_required`
- Follow PEP8, add type hints to all functions
- Use Python's `csv` module — no pandas dependency
- Existing `import_statements` command must remain fully functional
- Do not break any existing passing tests
- All file moves must be atomic (copy then delete, not rename across drives)
- The `inbox/` and `processed/` directory paths must be configurable via Django settings
  (`IMPORT_INBOX_DIR` and `IMPORT_PROCESSED_DIR`), defaulting to `BASE_DIR / 'inbox'` and
  `BASE_DIR / 'processed'`

---

## Manual Steps Required (output at end of run)

1. **Database Migrations**: Run `python manage.py makemigrations core` then `python manage.py migrate`
2. **Create inbox folder**: `mkdir C:\AI-DevTeam\projects\portfolio_manager\inbox`
3. **Git Operations**: Commit and push to branch `feature/rbc-cad-autoimport` manually
4. **Testing**: Run `python manage.py test imports --settings=portfolio_manager.settings_test`
5. **Dependencies**: No new pip packages required
