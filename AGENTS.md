# Portfolio Manager — Project Context

## Environment
- **Python:** `C:\AI-DevTeam\ai-env\Scripts\python.exe`
- **Virtual env:** `C:\AI-DevTeam\ai-env\` — **this is the ONLY virtual environment. DO NOT create or use any other venv.**
- **Django:** 5.1.15
- **Production DB:** MSSQL (configured in `settings.py`)
- **Test DB:** SQLite in-memory (`settings_test.py`)
- **Terminal shell:** PowerShell (`pwsh`) — do NOT use bash

> **Note:** The `venv/` folder in this project has been removed. Always use `ai-env` for everything.

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

## Reconciliation Work (feature_rbc_statements_reconciliation)
- Active branch for verifying PDF data integrity and planning CSV reconciliation
- `scripts/verify_pdf_integrity.py` — verifies RBC PDF internal consistency
  - Usage: `python scripts/verify_pdf_integrity.py --account 90000001 --currency CAD --start-date 2019-01-01 --start-balance 393437.82`
  - Reports saved to `docs/reconciliation/pdf_integrity_<account>_<currency>.md`
  - Each account gets its own start date decided during inspection
- TS_HUISGELD (90000001) CAD: Start date 2019-01-01, Start balance $393,437.82. 90 PDF statements (2017-11 to 2026-04), 88 in analysis. Balance chain intact except first statement ($12.28 offset from start balance). 57 periods show small DRIP reconciliation gaps (expected).
- Monthly vs Annual cross-check shows consistent $1,500-$2,200 mismatch across all years — systematic offset from different valuation dates

## PDF Import & Holdings Architecture
- **PDF monthly statements are master** (state + activity). CSV is activity layer only.
- **4 PDF sections parsed:** Account Info (`RbcMonthly`), Account Activity (`RbcMonthlyTransaction`), Balances summary (`RbcMonthlyBalance`), Holdings/Asset Review (`RbcMonthlyHolding`).
- **VL model config:** `settings.py` lines 238-247: `VL_MODEL_BASE_URL = http://127.0.0.1:11440/v1`, `VL_MODEL_NAME = qwen2.5-vl`. Model `qwen2.5-vl-7b-instruct-q8_0.gguf` on port 11440 via llama.cpp with `--mmproj`.
- **`vl_model` skill at `~/.agents/skills/vl_model/`** — not registered in available skills list, can be edited directly.
- **Key bug:** `vl_model` error `500 - image input is not supported - hint: if this is unexpected, you may need to provide the mmproj` — this was from old qwen2.5-vl config, not Gemma 4. Gemma 4 has built-in vision encoder (~550M params), no separate `mmproj` needed.
- **JSON logs:** `processed/json_logs/` contains JSON only for **failed** imports. Successful imports log to `FileImportLog` model.
- **PDF transaction sign convention (from reconcile_tx analysis):**
  - PDF `RbcMonthlyTransaction` uses `debit` (outflow) and `credit` (inflow). Net = `credit - debit`.
  - PDF SELL/INTEREST(Tax) show as `debit` (negative net) — opposite of CSV where SELL is positive.
  - PDF `BUY` with `credit` is actually a dividend reinvestment — CSV labels it `DIST`.
  - PDF `BOUGHT` with small `debit` ($1-$5) is likely a fee/commission.
  - PDF `TRANSFER` with `credit` is an inflow (transfer-in), but CSV may record it as negative TFO (transfer-out from source account).
  - CSV `INT` with `amount_cad = 0.00` are phantom entries — no actual money moved.
- **PDF DRIP quantity convention (from verify_pdf_integrity analysis):**
  - DRIP `BUY` entries with description "DIST ON X SHS" have two patterns:
    - **"Fake quantity"**: `quantity` field = existing holdings count (e.g., `BUY VDY 2891 debit=366.13`). The actual new shares are fractional and NOT stored in `quantity`. Detect by parsing "DIST ON X SHS" — if `quantity == X`, the quantity field is unreliable. Skip for share-count reconciliation.
    - **"Real quantity"**: `quantity` is a small fractional number (e.g., `BUY VDY 0.1670 debit=1033.79`). This IS the actual new shares. Detect when `quantity < 10` and `quantity != parsed_X`.
  - `BOUGHT` with `debit > 0` and no "DIST" in description = real cash purchase (quantity is accurate)
  - `SOLD` with `debit > 0` = sell (quantity is accurate, `debit` = proceeds)
  - `INTEREST` with `debit > 0` for RBF2010 = interest reinvested as shares at $10/share. Shares = `debit / 10`
  - Many older transactions have empty `symbol` — symbol must be extracted from `description`

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
