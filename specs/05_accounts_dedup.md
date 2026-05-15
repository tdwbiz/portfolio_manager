# Feature Spec: Account Management & Deduplication

Project name: portfolio_manager
Customer: tdwdev
**Database:** Microsoft SQL Server (MSSQL) — existing DB `PortfolioDEV`, connection via `.env`
**Django version:** 5.1 (existing project — do NOT recreate, update in place)
**Feature branch:** `feature/accounts-dedup`

---

## Context

Specs 01–04 built the import pipeline and the dashboard. Two problems are now visible with real data:

1. **Duplicate transactions** — CSVs are manually generated exports with user-chosen date ranges.
   Multiple files can overlap (e.g. a file covering Aug 2024–Apr 2026 and another covering Jan 2026–Apr 2026).
   The same trade will appear in both. The existing row-level dedup check (`date + symbol + type + qty + price`)
   is the **primary protection**, but it has two weaknesses:
   - It does not include `account_id`, so `TOM IB` buying 100 AAPL and `SUSAN IB` buying 100 AAPL on the
     same day at the same price are wrongly treated as duplicates of each other.
   - File-level detection (same filename or hash) is **not reliable** since files are re-exported with
     different names or covering overlapping ranges.

2. **Account blindness** — The IB Transaction History CSV has an `Account` column (column index 3)
   with values like `TOM IB`, `SUSAN IB`, `TS JOINT`, `TOM ADVISOR`. The RBC Activity Export CSV
   also has an `Account` column. Currently these are ignored — all transactions are flattened
   into one pool, so the same symbol held in two sub-accounts looks like a duplicate on the dashboard.

This spec fixes both problems and adds an Accounts page.

---

## Requirements

### 1. Account Model

File: `core/models.py`

Add a new model:

```python
class Account(models.Model):
    BROKER_CHOICES = [('IB', 'Interactive Brokers'), ('RBC', 'RBC Brokerage')]
    account_id    = models.CharField(max_length=50, unique=True)   # e.g. "TOM IB", "TS JOINT", "RBC-123456"
    name          = models.CharField(max_length=100)               # human display name, editable
    broker        = models.CharField(max_length=3, choices=BROKER_CHOICES)
    currency      = models.CharField(max_length=3, default='USD')
    starting_balance     = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    starting_balance_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({self.broker})"
```

`account_id` is the raw string from the CSV (normalised: strip and upper, e.g. `"TOM IB"`).
`name` defaults to `account_id` on creation and can be updated by admin.
End balance is **calculated** (not stored): `starting_balance + sum of net_amount for all linked transactions`.

### 2. Add `account` FK to Transaction

File: `core/models.py`

Add to `Transaction`:
```python
account = models.ForeignKey(
    'Account',
    on_delete=models.SET_NULL,
    null=True, blank=True,
    related_name='transactions'
)
```

This is nullable so existing rows are not broken. New imports will always populate it.

### 3. Migration

Generate and apply one migration covering both changes:
```
python manage.py makemigrations core --name account_and_transaction_account_fk
python manage.py migrate
```

### 4. Update IB Parser — Capture Account

File: `imports/parsers.py`, function `_parse_ib_transaction_history`

`row[3]` is the Account column. Add `'account_id': row[3].strip().upper()` to every trade dict.

Do the same for `_parse_ib_activity_statement` if the Account column is present there too
(check the header row — if `Account` is a column, capture it; otherwise default to `''`).

### 5. Update RBC Parser — Capture Account

File: `imports/parsers.py`, function `parse_rbc_csv`

The RBC Activity Export has an `Account` column (column index 7, 0-based, in the data rows after
the header `"Date","Activity","Symbol","Symbol Description","Quantity","Price","Settlement Date","Account","Value","Currency","Description"`).

Add `'account_id': cleaned[7].strip().upper()` to every trade dict.
If the column is missing or blank, default to `''`.

### 6. Update Import Commands — Create/Link Account on Import

Files: `imports/management/commands/import_statements.py` and `auto_import.py`

When saving a `Transaction`, look up or create the `Account` record:

```python
account_id = trade.get('account_id', '').strip().upper()
if account_id:
    account, _ = Account.objects.get_or_create(
        account_id=account_id,
        defaults={'name': account_id, 'broker': broker_source}
    )
else:
    account = None
transaction.account = account
```

Import `Account` from `core.models`.

### 7. Transaction-Level Deduplication (Primary Fix)

**Problem:** CSVs are manually exported with overlapping date ranges. The same trade appears in
multiple files. File names and content hashes are NOT reliable dedup keys.

**Solution:** Fix the row-level duplicate check to include `account_id`.

The existing check in `import_statements.py` / `auto_import.py` queries:
```python
Transaction.objects.filter(
    statement__source=broker,
    date=trade['date'],
    symbol=trade['symbol'],
    type=trade['type'],
    quantity=trade['quantity'],
    price=trade['price'],
)
```

Update it to also filter by `account__account_id`:
```python
Transaction.objects.filter(
    statement__source=broker,
    date=trade['date'],
    symbol=trade['symbol'],
    type=trade['type'],
    quantity=trade['quantity'],
    price=trade['price'],
    account__account_id=account_id,  # add this
)
```

This means:
- Same trade in the same account on the same day → correctly detected as duplicate, skipped
- Same trade values but different accounts (e.g. TOM IB vs SUSAN IB) → correctly treated as two distinct transactions

**Secondary: Content Hash (optional optimisation)**

Add `content_hash = models.CharField(max_length=64, blank=True, default='')` to `FileImportLog`.
In `auto_import.py`, compute `hashlib.sha256(file_bytes).hexdigest()` before parsing.
If a prior `FileImportLog` with the same hash exists, skip the file entirely and log a warning
"Skipping {filename}: identical content already imported as {prior_filename}".
This is an optimisation only — it saves re-processing exact duplicate files but does NOT replace
row-level dedup. Do NOT apply in `--dry-run` mode.
Generate migration for the new field.

### 8. Update Dashboard — Account-Aware Positions

File: `core/utils.py`, `core/views.py`, `core/templates/core/dashboard.html`

- `compute_positions()` must group by `(symbol, broker, account)` — i.e. `AAPL` in `TOM IB`
  and `AAPL` in `SUSAN IB` are two separate rows
- Add `account_name` to each position dict (from `account.name` if linked, else blank)
- Dashboard positions table: add an **Account** column between Broker and Symbol
- **Net Worth section** at the top of the dashboard (above positions table):
  - For each `Account` that has a `starting_balance` set, show:
    `current_balance = starting_balance + sum(transaction.amount_cad × sign)` where sign is
    +1 for BUY dividends/deposits and -1 for SELL/fees — but for simplicity in this spec,
    net worth is just **sum of `total_cost_cad` across all open positions** labelled
    "Total Invested (CAD)". A proper cash-balance net worth can be spec 06.

### 9. Update Transaction List — Add Account Filter

File: `core/templates/core/transaction_list.html`, `core/views.py`

- Add `?account=TOM+IB` filter to `transaction_list` view (filter by `account__account_id`)
- Add an Account dropdown to the filter form, populated from `Account.objects.all().order_by('account_id')`
- Pass `accounts` queryset to template context
- Add **Account** column to the transaction table

### 10. Accounts Page

URL: `/accounts/`
View: `core/views.py` → `accounts_list`
Template: `core/templates/core/accounts.html`

Table columns:
| Account ID | Name | Broker | Currency | Starting Balance | Starting Date | Transaction Count | Total Invested (CAD) |

- `transaction_count` = `account.transactions.count()`
- `total_invested_cad` = `sum(t.amount_cad for t in BUY transactions)`
- Add link to nav in `base.html`: Dashboard | Transactions | Accounts

URL: `path('accounts/', views.accounts_list, name='accounts_list')`

### 11. Admin Registration

File: `core/admin.py`

Register `Account` with `list_display = ['account_id', 'name', 'broker', 'currency', 'starting_balance']`
so accounts can be edited (name, starting_balance, starting_balance_date) via `/admin/`.

### 12. Tests

Files: `core/tests/test_views.py` (add tests), `imports/tests/test_import_statements.py` (update),
`imports/tests/test_auto_import.py` (add dedup test)

New tests:
- `test_account_created_on_ib_import` — import IB fixture with account_id in trade dict, assert Account row created
- `test_account_created_on_rbc_import` — same for RBC
- `test_transaction_linked_to_account` — assert `transaction.account` is not None after import
- `test_accounts_page_loads` — GET `/accounts/` returns 200
- `test_accounts_page_shows_accounts` — seed Account, assert appears in response
- `test_duplicate_tx_same_account_skipped` — import same trade twice for same account, assert second import is skipped (row-level dedup)
- `test_same_trade_different_accounts_both_imported` — import identical trade values for TOM IB and SUSAN IB, assert BOTH are saved (not treated as duplicates)
- `test_duplicate_file_skipped_by_hash` — import same file content twice, assert second import is skipped and only 1 FileImportLog with status='success'
- `test_transaction_list_filter_by_account` — filter `?account=TOM+IB`, assert only that account's transactions shown

All existing tests must still pass. Run all with:
```
C:\AI-DevTeam\ai-env\Scripts\python.exe manage.py test imports core --settings=portfolio_manager.settings_test
```

---

## Data Notes for Reference

IB Transaction History CSV account values seen in real data:
- `TOM IB` — Tom's main IB account
- `SUSAN IB` — Susan's IB account
- `TS JOINT` — joint account
- `TOM ADVISOR` — advisor account (fees, subscriptions)

RBC Activity Export account column is at index 7 in data rows.

---

## Out of Scope for This Spec

- Cash balance tracking (deposits/withdrawals/interest)
- Real-time market prices
- Editing accounts via a web form (use Django admin)
- Historical balance charts

---

## Acceptance Criteria

1. All new and existing tests pass on SQLite in-memory
2. `/accounts/` page loads and shows accounts grouped by broker
3. Dashboard positions table has an Account column — `AAPL` in `TOM IB` and `AAPL` in `SUSAN IB` appear as separate rows
4. Re-importing a file with overlapping date range skips already-imported transactions (row-level dedup by account+date+symbol+type+qty+price)
5. Two accounts holding the same symbol at the same price on the same date are NOT incorrectly deduped against each other
6. Importing the same exact file content twice skips the second file entirely (hash check)
7. Transaction list has an Account dropdown filter that works
8. `Account` records are editable via Django admin
9. No data loss — existing transactions retain their data; `account` FK is nullable for old rows
