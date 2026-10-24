# Feature Spec: tx_hash Deduplication & Standalone Import Fix

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature_code_review_fixes

---

## Context

Phase A added a `tx_hash` field and `compute_tx_hash()` function to `core/models.py` for canonical transaction deduplication. However, `imports/auto_import.py` ignores this mechanism and performs manual field-by-field comparison instead. This is less robust — if any field varies slightly between two exports of the same trade, duplicates slip through.

Additionally, `auto_import.py` contains a hardcoded SQLite fallback in its standalone mode (`if not settings.configured`), which means running it as a script (`python auto_import.py`) uses an in-memory database instead of the real MSSQL backend.

**Related issues:** S1 (duplicate detection), S2 (hardcoded SQLite).

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. **Use `tx_hash` for duplicate detection in `auto_import.py`**

- Import `compute_tx_hash` from `core.models`
- For each trade, compute `tx_hash` using `compute_tx_hash()` with the same fields the model expects
- Check for duplicates using `Transaction.objects.filter(tx_hash=tx_hash).first()` instead of the current manual field comparison
- Log skipped transactions with their tx_hash for audit trail
- Keep the existing `FileImportLog.content_hash` check (file-level dedup) — this is a separate layer

**Before (current):**
```python
existing = Transaction.objects.get(
    statement__source=broker,
    date=trade['date'],
    symbol=trade['symbol'],
    type=trade['type'],
    quantity=trade['quantity'],
    price=trade['price'],
    account__account_id=account_id if account_id else '',
)
```

**After:**
```python
from core.models import compute_tx_hash
tx_hash = compute_tx_hash(
    broker=broker,
    account_id=account_id or '',
    date=trade['date'],
    symbol=trade['symbol'],
    tx_type=trade['type'],
    quantity=trade['quantity'],
    price=trade['price'],
    amount=trade['amount'],
    currency=trade.get('currency', 'USD'),
)
existing = Transaction.objects.filter(tx_hash=tx_hash).first()
if existing:
    logger.debug(f"Duplicate tx_hash={tx_hash[:16]}... skipped")
    skipped_count += 1
    continue
```

### 2. **Remove hardcoded SQLite fallback in standalone mode**

- Remove the `if not settings.configured: settings.configure(...)` block entirely
- The file should ONLY work when Django is already configured (i.e., via `python manage.py auto_import`)
- Add a clear error message if Django is not configured:
```python
if not settings.configured:
    raise RuntimeError(
        "Django settings not configured. Run via: python manage.py auto_import <file> <broker>"
    )
```
- Add a management command `auto_import` in `imports/management/commands/auto_import.py` that wraps the existing `auto_import()` function with proper argument parsing

### 3. **Create management command `auto_import`**

- Location: `imports/management/commands/auto_import.py`
- Arguments:
  - `file_path` (positional): Path to the CSV file
  - `broker` (positional): 'IB' or 'RBC'
  - `--dry-run`: Flag to only parse and report
- Usage: `python manage.py auto_import path/to/file.csv IB --dry-run`

---

## Constraints

- Do NOT run migrations — that is a manual step
- Do NOT push to git — list it under Manual Steps Required
- Follow PEP8, add type hints to all new functions
- Preserve existing `FileImportLog.content_hash` file-level dedup logic
- The `compute_tx_hash()` function in `core/models.py` must NOT be modified — it is the canonical implementation
- All existing tests must continue to pass

---

## Files to Modify

1. `imports/auto_import.py` — Remove SQLite fallback, use `tx_hash` for dedup
2. `imports/management/commands/auto_import.py` — NEW FILE, management command wrapper

---

## Tests

- Unit test for `tx_hash` dedup: Create two trades with identical fields, verify only one is imported
- Unit test for management command: Test `python manage.py auto_import` with a sample CSV
- Unit test for standalone mode error: Verify `RuntimeError` is raised when Django is not configured
- Fixture: Use existing sample CSV files in the workspace for testing

---

## Manual Steps Required

- [ ] `python manage.py makemigrations` (if any model changes)
- [ ] `python manage.py migrate --settings=...`
- [ ] Review and push feature branch: `git push origin feature_rbc_pdf_fixes`
- [ ] Run full test suite: `python run_tests.py`

---

## Human Approval Required

Human approval required before any git push or DB changes.
