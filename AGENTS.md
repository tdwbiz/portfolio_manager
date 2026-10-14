# Portfolio Manager — Project Context

## Environment
- **Python:** `C:\AI-DevTeam\ai-env\Scripts\python.exe`
- **Virtual env:** `C:\AI-DevTeam\ai-env\` — **this is the ONLY virtual environment. DO NOT create or use any other venv.**
- **Django:** 5.1.15
- **Production DB:** MSSQL (configured in `settings.py`)
- **Test DB:** SQLite in-memory (`settings_test.py`)
- **Terminal shell:** PowerShell (`pwsh`) — do NOT use bash

> **Note:** The `venv/` folder in this project has been removed. Always use `ai-env` for everything.

### RBC Account Configuration (env-driven)
- Real RBC account numbers are **not** stored in the repo. They come from `.env`:
  - `RBC_ACCOUNTS` — comma list of `id:label` pairs, e.g. `90000001:ACCOUNT_A,90000002:ACCOUNT_B`
  - `RBC_DEFAULT_ACCOUNT` — default account ID for script `--account` flags
- Exposed in `settings.py` as `settings.RBC_ACCOUNTS` (dict `{id: label}`) and `settings.RBC_DEFAULT_ACCOUNT`. See `.env.example`.
- Used by: the CSV parser account whitelist (`imports/parsers.py`), `merge_duplicate_accounts`, `scripts/generate_accounting_ledger.py`, and the `--account` defaults in `scripts/ledger_high_change.py`, `pdf_internal_reconciliation.py`, `reconcile_holdings.py`, `reconcile_tx.py`, `verify_pdf_integrity.py`.
- **If `RBC_ACCOUNTS` is empty, RBC CSV rows are rejected as unknown accounts** — set it in `.env` before importing.
- Tests use fake accounts `90000001`-`90000008` (labels `ACCOUNT_A`...`ACCOUNT_H`) via `settings_test.py` — no `.env` needed.

## Key Commands
```powershell
# Run tests
C:\AI-DevTeam\ai-env\Scripts\python.exe manage.py test core --settings=portfolio_manager.settings_test

# Run pytest
C:\AI-DevTeam\ai-env\Scripts\python.exe -m pytest tests/ -v

# Run server
C:\AI-DevTeam\ai-env\Scripts\python.exe manage.py runserver --settings=portfolio_manager.settings

# Check migrations
C:\AI-DevTeam\ai-env\Scripts\python.exe manage.py makemigrations --check --settings=portfolio_manager.settings_test
C:\AI-DevTeam\ai-env\Scripts\python.exe manage.py migrate --settings=portfolio_manager.settings_test
```

## Import Commands
Full reference: `docs/IMPORT_COMMANDS.md`

| What you want | Command |
|---|---|
| **Import all PDFs** (annual + monthly) | `manage_inbox_import` |
| **Import Activity CSVs** from inbox | `import_csv` |
| **Import Holdings CSVs** into PositionHistory | `import_csv --holdings` |
| **Import specific CSV** (legacy) | `auto_import` |
| **Watch inbox continuously** | `watch_inbox` |

**DEPRECATED (do not use):** `manage_pdf_import`, `manage_annual_report_import` — use `manage_inbox_import` instead.

## Aider (LLM Coding Agent)
```powershell
aider --model openai/qwen35B-code --openai-api-base http://127.0.0.1:11437/v1
```
- Uses llama.cpp on port 11437 (NOT Ollama)
- Model name: `qwen35B-code`
- Alternative model: `qwen27B-code` on port 11438
  ```powershell
  aider --model openai/qwen27B-code --openai-api-base http://127.0.0.1:11438/v1
  ```
- In aider, end a command with `/exit`

## Architecture Notes
- **URLs:** `core.urls` included at root — routes are `/login/`, `/dashboard/`, etc. (NOT `/core/login/`)
- **yfinance 1.3.0 bug:** `fast_info.last_price` crashes with `AttributeError: 'PriceHistory' object has no attribute '_dividends'`. Use the `_get_current_price()` helper in `core/utils/prices.py` which falls back to `info[]` then `history()`.
- **Django ORM:** pyright doesn't understand Django's runtime metaclass magic. Use `typeCheckingMode: "basic"` in pyright config.
- **Known false positives:** `logging.handlers` import (string ref in LOGGING dict), `utils_fetch_last_prices` re-export — both have `noqa: F401`.

### Currency Handling (Phase E3-E + E4)
- **10 of 13 accounts are multi-currency** (CAD + USD). The old `Account.currency` field was misleading.
- **`AccountCurrency` model** — each (account, currency) pair is a sub-account with its own `starting_balance` and `starting_balance_date`.
- **`Account.currency` is deprecated** — after migration it reflects the dominant currency or 'MIX' for multi-currency (max_length=3 constraint).
- **`Account.starting_balance` is deprecated** — moved to `AccountCurrency.starting_balance`.
- **All transaction-aware operations** (monthly statements, reconciliation, risk) already used per-currency dimensions on `CoreMonthlyStatement`, `CoreAnnualReport`, `RbcMonthly`, and `Transaction`.
- **`AccountForm`** no longer includes `currency`, `starting_balance`, `starting_balance_date` — use `AccountCurrencyFormSet` instead.
- **Reverse statement generation** uses the formula: `beginning = ending - net_flow` where `net_flow = sum(amount_cad)`.
- **Auto-detection** for reverse generation tries RbcMonthly data first, then falls back to `AccountCurrency.starting_balance`.
- **Template references** to `account.currency` should now use `account.currencies.all` to show currency badges per sub-account.
- **CSV sign convention:** Buys/WDRAW/TAX are **negative** `amount_cad` (money out). Sells/DEP/DIV/INT are **positive** `amount_cad` (money in). Commission is **already included** in `amount_cad` — never add separately.
- **Forward formula:** `ending = beginning + net_flow` (net_flow = sum of all amount_cad).
- **Reverse formula:** `beginning = ending - net_flow`.
- **Display fields** (`total_buys`, `total_commission`) stored as absolute magnitudes (positive numbers).
- **`distinct("currency")` is PostgreSQL-only** — use per-currency subqueries for SQLite/SQL Server compatibility.

### Statement Generation Formula (CSV Sign Convention)
- All amounts come from CSV import with signed values
- `BUY` = negative (money out to buy shares)
- `SELL` = positive (money in from selling)
- `WDRAW` = negative (withdrawal)
- `DEP` = positive (deposit)
- `DIV`/`DIST`/`INT` = positive (income)
- `TAX` = negative (tax withheld)
- `net_flow = sum(tx.amount_cad for tx in month_txs)`
- `ending = beginning + net_flow`
- Commission is already included in `amount_cad` — do NOT add it separately

## Phase F (IN PROGRESS — CSV Master Import)
**Branch:** `feature_csv_master_import`
**Test count:** 220 total (1 pre-existing failure in `test_chart_data_empty` — unrelated mock issue)

### Ledger System (NEW)
- **`LedgerAccount` model** — per-account ledger accounts: CASH, POSITION (per symbol), DIVIDEND, INTEREST, TAX, RETURN_OF_CAPITAL
- **`LedgerEntry` model** — double-entry bookkeeping records from Transactions
- **`LedgerMonthClosure` model** — marks months as reviewed/closed
- **`MonthlyLedgerBalance` model** — monthly snapshot of ledger balances (quantity, cost_basis_total, cost_basis_per_unit)
- **`write_ledger` management command** — creates ledger entries from Transaction records
  - `python manage.py write_ledger --account <ID> --month YYYY-MM` — single account/month
  - `python manage.py write_ledger --all-open` — all open (unclosed) months for a single account
  - `python manage.py write_ledger --all-open --account <ID>` — all open for specific account
  - **Idempotent**: skips transactions that already have ledger entries (`LedgerEntry.objects.filter(source_transaction=tx).exists()`) — safe to re-run
  - **Skips closed months**: checks `LedgerMonthClosure.is_closed` — closed months are not overwritten
  - **No manual close yet** — `LedgerMonthClosure` table may be empty initially; all months treated as open
  - **REORGANIZATION handling**: notes-based pattern matching — "SPLIT" = stock split, "DIV ON" = stock dividend, "CIL" = cash in lieu, "EXP" = option expiration, "DIV REIN" = dividend reinvestment, "TFR" = transfer
  - **TAX transactions**: create separate TAX ledger account (DEBIT tax expense + CREDIT cash)
  - **OTHER/UNKNOWN types**: logged as warnings, not errors — safe to skip
  - **REORGANIZATION with negative shares**: treated as stock dividend (e.g., 9PYDBZ1: -1 → 0 shares)
- **Ledger view**: `core/views_ledger.py` — `/ledger/<account_id>/<year>/<month>/`
- **T-account format**: For a given account/month — opening balance, all debits/credits, closing balance per position (shares) and cash
- **Holdings ledger**: Each stock gets its own position ledger account showing quantity changes (opens, closes, dividends in kind, splits)

### Import Commands
```powershell
# Import all Activity CSVs from inbox/
python manage.py import_csv

# Import Holdings CSVs into PositionHistory
python manage.py import_csv --holdings

# Dry run (parse without writing)
python manage.py import_csv --dry-run

# Write ledger entries for all open months
python manage.py write_ledger --all-open

# Write ledger for specific account and month
python manage.py write_ledger --account 90000003 --month 2025-02
```

### Strategy: CSV as Master for Activity
- PDF VL extraction is unreliable (hallucinates transactions, misses real ones)
- CSV is the authoritative source for activity data
- RBC PDF provides scaffolding: `begin_total_market_value`, `end_total_market_value`, `RbcMonthlyBalance.closing_balance`
- Holdings CSV provides position snapshots for `PositionHistory`
- Given CSV activity + beginning balances + month-end market values → can build `CoreMonthlyStatement`

### Changes Implemented
- **Fixed `compute_tx_hash`** — removed `amount` so overlapping CSV exports with slightly different amounts produce the same hash
- **New `PositionHistory.market_price` / `market_value`** — from Holdings CSV snapshots
- **New `Transaction.settlement_date`** — parsed from CSV column 6
- **New `BrokerStatement.period_start` / `period_end`** — track coverage range
- **Shrunk `Transaction.symbol`** to max_length=50, `commission` to (10,2)
- **New `imports/parsers_holdings.py`** — parses Holdings CSV → `PositionHistory`
- **New `import_csv` management command** — import Activity and Holdings CSVs from inbox

### Import Commands
```powershell
# Import all Activity CSVs from inbox/
python manage.py import_csv

# Import Holdings CSVs into PositionHistory
python manage.py import_csv --holdings

# Dry run (parse without writing)
python manage.py import_csv --dry-run
```

## Branch Strategy
- `dev` — active development branch
- `main` — production
- Feature branches: `feature/phase*-...`, `feature_misc_outstanding_items`

## Phase D (COMPLETE)
**All 7 items implemented and tested (122/122 tests pass, 0 regressions).**
- D1: Portfolio Summary — account/category subtotals, allocation %, currency breakdown (`portfolio_summary.html`, `group_positions_by_account`, `group_positions_by_category`)
- D2: Performance Chart — cost basis, benchmark, income overlay, date range, chart type selector (`portfolio_chart.html`, `compute_cost_basis_series`, `compute_income_series`, `compute_series_by_account`)
- D3: Dashboard Dollar Summary — market value, P&L, YTD income cards (`dashboard.html`, dashboard view)
- D4: Total Return — realised + income + return cards (`portfolio_summary.html`, row 2)
- D5: Brand & UI — `tsmoney.css`, favicon, site banner, footer, `.page-header` on all templates, `:root` CSS variables
- D6: Top Movers — gainers/losers widget on dashboard (`get_top_movers` in `core/utils/portfolio.py`)
- D7: Dividends Tracker — `DividendSchedule` model, `get_upcoming_dividends`, upcoming dividends widget on dashboard

**Branch:** `feature_misc_outstanding_items` (merged to `dev`)

## Phase D History
Phase D was implemented in a previous session. The code, views, templates, models, and tests were already present when we started `feature_phaseD_coding`. We verified all 122 tests pass with 0 regressions. No new code was written — only infrastructure (AGENTS.md, global_agents.md, zed settings, ai-env config, migration 0016).

## Phase E (COMPLETE — E1, E3-A, E3-B, E3-C, E3-D, E3-E, E4 ALL IMPLEMENTED)
**Current test count: 183 tests (148 existing + 35 new), 0 regressions.**

### E1: Symbol Resolution + Fixed Prices ✅
- `resolve_yahoo_symbol(symbol, primary_currency)` — adds `.TO` for CAD symbols
- `FixedPriceConfig` model — admin-editable fixed prices for RBF2010 (CAD 10), RBF2014 (USD 10)
- `get_fixed_price(symbol)` — skips yfinance for fixed-price securities
- Updated `fetch_last_prices()` and `load_prices_from_yfinance()` to use resolution

### E3-A: Gap Analysis Report ✅
- `core/utils/gap_analysis.py` — finds longest continuous run, counts gaps
- View at `/accounts/gap-analysis/` — shows per-account data completeness

### E3-B: Monthly Statement Generation ✅
- `CoreMonthlyStatement` model — one row per (account, month, currency)
- `generate_monthly_statements(account)` — aggregates transactions by month
- Management command: `generate_monthly_statements`
- View at `/accounts/generate-monthly-statements/`

### E3-C: Annual Report Generation ✅
- `CoreAnnualReport` model — one row per (account, year, currency)
- `generate_annual_reports(account)` — aggregates from monthly statements
- Management command: `generate_annual_reports`
- View at `/accounts/generate-annual-reports/`

### E3-D: Reconciliation Engine ✅
- `CoreReconciliation` model — CSV vs RBC comparison results
- `reconcile_monthly()`, `reconcile_annual()` — compare CSV vs RBC with $1 threshold
- Management command: `reconcile`
- View at `/accounts/reconcile/`

### E3-E: Account Currency Refactor (Sub-Account Model) ✅
- `AccountCurrency` model — sub-accounts per (account, currency) with own starting balance
- `Account.currency` deprecated — set to dominant currency or 'MIX' after migration (max_length=3 constraint)
- `Account.starting_balance` / `starting_balance_date` deprecated — moved to sub-account
- Migration `0024_alter_*` + `0025_populate_account_currencies` — detects currencies from Transaction/RbcMonthly, creates sub-accounts, computes starting balances
- `AccountForm` no longer includes deprecated fields; `AccountCurrencyFormSet` handles currency inlines
- `AccountAdmin` has `AccountCurrencyInline` (TabularInline)
- Templates show currency badges from sub-accounts, not single `account.currency`
- Account edit view handles both `AccountForm` and `AccountCurrencyFormSet`
- Account create redirects to edit page (to set up currencies)
- View at `/accounts/<id>/edit/` includes currency sub-account editor

### E4: Reverse Statement Generation ✅
- `reverse_generate_monthly_statements(account, anchor_month, ending_balances)` in `core/utils/statements.py`
- Walks backwards from a known ending balance, computing beginning values
- Auto-detects ending balances from RbcMonthly data or AccountCurrency starting_balance
- View at `/accounts/reverse-statements/` — form with account, anchor month, ending balances per currency
- Template at `core/templates/core/reverse_statements.html`
- Link added to accounts page quick tools

### Batch Statement Generation (NEW)
- `batch_generate_all_statements(account)` in `core/utils/statements.py`
- Anchors from RBC `end_total_market_value` per currency, reverse-generates monthly, then forward-generates annual
- Creates placeholder (zero-activity) statements when no transactions exist but RBC data is present
- Management command: `python manage.py batch_generate_statements [--account ID] [--dry-run]`
- Views at `/accounts/generate-monthly-statements/` and `/accounts/generate-annual-reports/`
- **Default behavior:** shows existing statements (no regeneration)
- **Regenerate:** add `?regenerate=1` to force recalculation from transactions
- **Batch from RBC:** add `?batch=1` to anchor from RBC data and fill gaps

**Branch:** `feature_phaseE_changes_and_fixes`
**Migrations:** `0023_coreannualreport_coremonthlystatement_and_more.py`, `0024_alter_*`, `0025_populate_account_currencies.py`

## Bug Fixes & Gotchas
- **Migration numbering:** Data migration must be `0025_` not `0024_` to avoid conflicts with schema migration `0024_alter_*`.
- **Production DB max_length=3:** `Account.currency` column has `max_length=3` — use `'MIX'` (3 chars), NOT `'MIXED'` (5 chars). Use `Account.objects.filter(pk=pk).update(currency=...)` instead of `account.save()` to avoid SQL Server truncation errors when column was previously wider.
- **Naive datetime warning:** Suppressed in `settings_test.py` — Django `auto_now_add=True` fields can trigger warnings during tests when TZ support is active.
- **Template bug:** Fixed malformed HTML in `accounts.html` — currency badge span was empty with stray `</span>`.
- **Transaction requires statement FK:** Always create `BrokerStatement` in tests before creating `Transaction`.
- **CSV sign convention changed:** Tests that create transactions with `type="BUY"` must use **negative** `amount_cad` (e.g., `-1950.00`), not positive. The old convention of `+1950` for buys and separate commission subtraction is obsolete. Commission is already in `amount_cad`.
- **Test DB uses SQLite in-memory** — always run with `--settings=portfolio_manager.settings_test`. Production uses MSSQL.
- **Batch generation** (`batch_generate_statements`) processes **all accounts** by default. Use `--account ID` for a single account or `--dry-run` to preview.
- **`write_ledger --all-open` is idempotent** — does NOT wipe existing ledger entries. Skips transactions that already have `LedgerEntry` records. Safe to re-run.
- **`write_ledger` skips closed months** — checks `LedgerMonthClosure.is_closed`. No manual close UI yet; table starts empty so all months are open.
- **REORGANIZATION notes-based matching** — uses `tx.notes` text: "SPLIT" = stock split, "DIV ON" = stock dividend, "CIL" = cash in lieu, "EXP" = option expiration, "DIV REIN" = dividend reinvestment, "TFR" = transfer
- **TAX transactions** create a separate TAX ledger account (DEBIT tax expense + CREDIT cash), NOT treated as other
- **OTHER ASSIGNMENT** (e.g., option assignment) — ignore in write_ledger; the SELL transaction already handles the quantity/price
- **Import CSV idempotent** — deduplicates by `tx_hash`. Running again won't create duplicates unless hash changed.
- **Import CSV does NOT clear existing transactions** — only new/changed files are processed. Previously imported files moved to `processed/` are skipped.
- **`transaction.atomic()` context manager** — pyright false positive: `Object of type (...) -> object cannot be used with "with"`. Works fine at runtime.
- **`Style.WARNING`/`Style.SUCCESS`** — pyright false positive. These work at runtime via Django's command output API.

## Reconciliation Work (feature_rbc_statements_reconciliation)
- Active branch for verifying PDF data integrity and planning CSV reconciliation
- **CSV is the authoritative source for activity data**. PDF VL extraction is unreliable (hallucinates transactions).
- **RBC PDF provides scaffolding**: balance data (`RbcMonthly.end_total_market_value`, `RbcMonthlyBalance.closing_balance`), holdings snapshots (`RbcMonthlyHolding`)

### Scripts
- `scripts/verify_pdf_integrity.py` — verifies RBC PDF internal consistency
  - Usage: `python scripts/verify_pdf_integrity.py --account 90000001 --currency CAD --start-date 2019-01-01 --start-balance 393437.82`
  - Reports saved to `docs/reconciliation/pdf_integrity_<account>_<currency>.md`
  - Each account gets its own start date decided during inspection
- `scripts/ledger_high_change.py` — detailed ledger for months with >5% price change
  - Usage: `python scripts/ledger_high_change.py --account 90000001 --currency CAD --start-date 2019-01-01`
  - Reports saved to `docs/reconciliation/ledger_high_change_<account>_<currency>.md`

### ACCOUNT_A (90000001) CAD
- Start date 2019-01-01, Start balance $393,437.82
- 90 PDF statements total (2017-11 to 2026-04), 88 in analysis (excludes 2 pre-2019)
- Balance chain intact across all 88 statements (only $12.28 offset on first statement from start balance)
- Missing statements (2019-12, 2020-12) were re-imported from inbox
- No USD sub-account

### RBC PDF Transaction Convention (Verified from actual PDFs)

**`activity_type` is a fixed-choice CharField** — `RbcMonthlyTransaction.activity_type` has choices defined in `core/models.py` and enforced by `normalize_activity_type()` in `imports/parsers_monthly_vl.py`. The VL parser prompt now uses these exact values.

**Valid activity types:** BOUGHT, SOLD, DIST, DIVIDEND, DIV REIN, INTEREST_RECEIVED, INTEREST_PAID, TRANSFER, TFR OUT, WIRE TFR, CONTRIB, DEPOSIT, WITHDRAWAL, ADJUST, RTC, FEE, OTHER

**PDF columns:** `DATE | ACTIVITY | DESCRIPTION | QUANTITY | RATE | DEBIT | CREDIT`

- **BOUGHT**: Real cash purchase. `debit` = cost (money out). `quantity` = shares purchased. Description does NOT contain "DIST".
- **SOLD**: Sale of shares. `debit` or `credit` = proceeds (money in). `quantity` = shares sold.
- **DIST**: Cash distribution/dividend. Identified by "DIST ON X SHS" in description. `credit` = total cash received (money in). `price` = per-share distribution rate (e.g., 0.169). `quantity` = 0 (blank in PDF). **This is CASH INCOME — NOT a purchase.**
- **DIVIDEND**: Cash dividend. `credit` = cash received (money in).
- **DIV REIN**: Dividend reinvestment — dividend immediately used to buy shares. Description contains "REINV@C$XX.XX". `quantity` = new shares, `debit` = cost. **Net cash = 0**.
- **INTEREST_RECEIVED**: RBF2010/RBF2014 or IB interest earned. `quantity` = new shares earned (e.g., 2.067). `price` = 0, `debit` = 0, `credit` = 0. **Net cash = 0** (reinvested as shares).
- **INTEREST_PAID**: Interest paid (e.g., margin debt). `debit` = amount. (Used by IB accounts.)
- **TRANSFER/TFR OUT/WIRE TFR/CONTRIB**: Cash transfers. `credit` = inflow, `debit` = outflow.
- **DEPOSIT**: Cash deposited. `credit` = amount.
- **WITHDRAWAL**: Cash withdrawn. `debit` = amount.
- **ADJUST/RTC**: Book cost adjustments. No cash effect.
- **FEE**: Charges/fees. `debit` = amount.
- **OPENING BALANCE / CLOSING BALANCE**: Balance summary lines — NOT transactions. The parser skips these.

**Cash flow categories:**
- Cash IN: SOLD, DIVIDEND, DIST, TRANSFER (credit), CONTRIB, DEPOSIT
- Cash OUT: BOUGHT, WITHDRAWAL, TFR OUT (debit), FEE, INTEREST_PAID
- Net Zero: DIV REIN, INTEREST_RECEIVED, ADJUST, RTC

**VL Parser post-processing** (`normalize_activity_type`): Uses description to fix known LLM errors:
- "DIST ON X SHS" in description → force `DIST` (even if LLM says `BUY`/`BOUGHT`)
- "REINV@" in description → force `DIV REIN`
- "RBC INVESTMENT SAVINGS" → force `INTEREST`
- `BUY` → `BOUGHT`, `SELL` → `SOLD`, `DIV` → `DIVIDEND`

**DIST quantity pattern:** `quantity` = per-share distribution rate (e.g., 0.169). The actual cash received is in the `debit` column. Do NOT multiply quantity × existing holdings — the `debit` IS the total cash.

**INTEREST quantity pattern:** `quantity` = new RBF shares earned. Cash value = `quantity × 10`.

### Monthly vs Annual Cross-check
- Consistent $1,500-$2,200 mismatch across all years — systematic offset from different valuation dates

## PDF Import & Holdings Architecture
- **CSV Activity is master for transactions**. PDF provides balance scaffolding only.
- **Holdings CSV** provides position snapshots for `PositionHistory` (quantity, market_price, market_value, average_cost)
- **4 PDF sections parsed:** Account Info (`RbcMonthly`), Balances summary (`RbcMonthlyBalance`), Holdings/Asset Review (`RbcMonthlyHolding`), Account Activity (`RbcMonthlyTransaction` — unreliable, do not use for transactions)
- **VL model config:** `settings.py` lines 238-247: `VL_MODEL_BASE_URL = http://127.0.0.1:11440/v1`, `VL_MODEL_NAME = qwen2.5-vl`. Model `qwen2.5-vl-7b-instruct-q8_0.gguf` on port 11440 via llama.cpp with `--mmproj`.
- **`vl_model` skill at `~/.agents/skills/vl_model/`** — not registered in available skills list, can be edited directly.
- **Key bug:** `vl_model` error `500 - image input is not supported - hint: if this is unexpected, you may need to provide the mmproj` — this was from old qwen2.5-vl config, not Gemma 4. Gemma 4 has built-in vision encoder (~550M params), no separate `mmproj` needed.
- **JSON logs:** `processed/json_logs/` contains JSON only for **failed** imports. Successful imports log to `FileImportLog` model. Both monthly and annual imports follow this convention.
- **Watchdog:** The inbox watcher runs as a daemon thread when `runserver` starts. It polls `inbox/` every `IMPORT_INBOX_POLL_INTERVAL` seconds (default: 300 = 5 minutes). When PDFs are found, it classifies them and routes to either `manage_annual_report_import` or `manage_pdf_import`. You can also run `python manage.py import_inbox` for a one-shot import that handles both types.
- **PDF transaction sign convention:**
  - PDF `RbcMonthlyTransaction` uses `debit` (outflow) and `credit` (inflow). Net = `credit - debit`.
  - CSV `INT` with `amount_cad = 0.00` are phantom entries — no actual money moved.
  - Many older transactions have empty `symbol` — symbol must be extracted from `description`
- **DIST quantity pattern:** `quantity` = per-share distribution rate (e.g., 0.169). The actual cash received is in the `debit` column. Do NOT multiply quantity × existing holdings.
- **INTEREST_RECEIVED quantity pattern:** `quantity` = new RBF shares earned. Cash value = `quantity × 10`.

## Bug Fixes & Gotchas
- **Migration numbering:** Data migration must be `0025_` not `00024_` to avoid conflicts with schema migration `00024_alter_*`.
- **Production DB max_length=3:** `Account.currency` column has `max_length=3` — use `'MIX'` (3 chars), NOT `'MIXED'` (5 chars). Use `Account.objects.filter(pk=pk).update(currency=...)` instead of `account.save()` to avoid SQL Server truncation errors when column was previously wider.
- **Naive datetime warning:** Suppressed in `settings_test.py` — Django `auto_now_add=True` fields can trigger warnings during tests when TZ support is active.
- **Template bug:** Fixed malformed HTML in `accounts.html` — currency badge span was empty with stray `</span>`.
- **Transaction requires statement FK:** Always create `BrokerStatement` in tests before creating `Transaction`.
- **CSV sign convention changed:** Tests that create transactions with `type="BUY"` must use **negative** `amount_cad` (e.g., `-1950.00`), not positive. The old convention of `+1950` for buys and separate commission subtraction is obsolete. Commission is already in `amount_cad`.
- **Test DB uses SQLite in-memory** — always run with `--settings=portfolio_manager.settings_test`. Production uses MSSQL.
- **Batch generation** (`batch_generate_statements`) processes **all accounts** by default. Use `--account ID` for a single account or `--dry-run` to preview.
- **`write_ledger --all-open` is idempotent** — does NOT wipe existing ledger entries. Skips transactions that already have `LedgerEntry` records. Safe to re-run.
- **`write_ledger` skips closed months** — checks `LedgerMonthClosure.is_closed`. No manual close UI yet; table starts empty so all months are open.
- **REORGANIZATION notes-based matching** — uses `tx.notes` text: "SPLIT" = stock split, "DIV ON" = stock dividend, "CIL" = cash in lieu, "EXP" = option expiration, "DIV REIN" = dividend reinvestment, "TFR" = transfer
- **TAX transactions** create a separate TAX ledger account (DEBIT tax expense + CREDIT cash), NOT treated as other
- **OTHER ASSIGNMENT** (e.g., option assignment) — ignore in write_ledger; the SELL transaction already handles the quantity/price
- **Import CSV idempotent** — deduplicates by `tx_hash`. Running again won't create duplicates unless hash changed.
- **Import CSV does NOT clear existing transactions** — only new/changed files are processed. Previously imported files moved to `processed/` are skipped.
- **`transaction.atomic()` context manager** — pyright false positive: `Object of type (...) -> object cannot be used with "with"`. Works fine at runtime.
- **`Style.WARNING`/`Style.SUCCESS`** — pyright false positive. These work at runtime via Django's command output API.

### RBC Monthly PDF Parser Bugs
- **`"Continued on Next Page"` was case-sensitive** — PDF text uses uppercase `"- CONTINUED ON NEXT PAGE -"` but parser checked lowercase. Fixed with `lines[i].lower()`.
- **`"Opening Balance"` / `"Closing Balance"` were case-sensitive** — same issue, fixed with `.lower()`.
- **DIST transactions have activity type AFTER quantity/rate** — unlike DIV REIN/DIVIDEND where activity is on line 2, DIST has: date → security name → quantity → rate → description → ACTIVITY TYPE → "ON X SHS" → "REC ... PAY ...". Parser scans forward to find the activity type and collects pre-activity lines as description/quantity/rate.
- **Transaction dates are strings like `"JAN. 08"`** — must be converted to proper `datetime.date` objects via `_parse_date_from_string()` before saving to `RbcMonthlyTransaction.date` (DateField).
- **`_parse_date_from_string()` must handle `"JAN. 08"` format** (with dot after month abbreviation) — not just `"Dec 08"`.

## Memory & Skill Management

### Global AGENTS.md (`C:\Users\tdwdev\.agents\AGENTS.md`)
I **cannot edit** this file directly (it's outside the project). The full content is mirrored in `global_agents.md` at the project root.

**Workflow:**
1. If I find something worth adding to the global AGENTS.md, I will edit `global_agents.md` at the project root and tell you to copy the updated content to `C:\Users\tdwdev\.agents\AGENTS.md`
2. You can also edit `global_agents.md` directly — I will read it to know what's in the global AGENTS.md
3. After you copy the update, let me know and I'll consider it live

### Global Skills (`~/.agents/skills/`)
I **can edit** skills directly in `C:\Users\tdwdev\.agents\skills\`. If I find something new or worthwhile to add to a skill, I will do it myself. Currently available global skills:
- `web-app-debug` — general web app debugging checklist (Django, Flask, FastAPI, Node.js)

### Project AGENTS.md (this file)
This is my primary reference for this project. I will update it when project-specific knowledge changes (new commands, new bugs, new specs, etc.).
