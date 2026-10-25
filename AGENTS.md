# Portfolio Manager — Agent Instructions

Django app for tdw's RBC investment accounts: CSV/PDF statement imports, holdings, ledger, monthly/annual statements, reconciliation, performance and dividends. History of earlier phases and reconciliation findings: `docs/project-history.md` (reference only).

## Sharing data with a cloud AI

   Target design (`docs/architecture.md` §5): the app stores no account numbers; accounts are identified by name, and the only digits shown are the last 4 on the accounts page. `.env` holds only `ACCOUNT_FINGERPRINT_KEY`; until phase 2 of the clean-up is done, the real numbers are still in the database on `mercury` (`core_account.account_id` and stored statement fields). Rules for sharing anything about this repo with a cloud AI:

- **Never paste into a cloud AI:** account numbers (not even last-4 digits), `.env` content, log lines, `docs/reconciliation/` content, statement files (CSV or PDF), or VL JSON output from `processed/json_logs/`.
- **Never paste full names, surnames, addresses or SINs** — these never leave the machine.
- Account names (`TOM_TFSA`, `SUSAN IB`, `TS_HUISGELD`) do contain real first names, but they are the identifiers the app itself uses, so they **may be shared** with a cloud AI when describing a problem.
- When describing a problem, quote the **file and line** (and the account **name**), not data rows.
- In examples, docs and prompts use the fake accounts `90000001`–`90000008` (`ACCOUNT_A`–`ACCOUNT_H`), or the real account names above.
- Local models (ports 11435 / 11438, no internet path) are exempt.

## Environment

- **Configuration comes from environment variables**: strictly avoid hardcoded strings for configuration (e.g. `VL_MODEL_*`).

### saturn Ubuntu (primary; where Pi works)
- **Python:** `/opt/pm-venv/bin/python` (and `/opt/pm-venv/bin/pytest`). Django 6.1.x. This is the **only** environment: do not create venvs, do not install packages. If a package is missing, stop and say which one.
- **Shell:** bash.
- Pi works in its own copy with SQLite test settings and a throwaway `.env`. **Never edit, print or commit `.env`.**

### Windows (saturn Windows, neptune) — fallback
- **Python:** `C:\AI-DevTeam\ai-env\Scripts\python.exe` (Django 5.1.x). Shell: PowerShell. Repo: `C:\AI-DevTeam\projects\portfolio_manager`.

### Databases
- **Production and development DBs:** SQL Server on `mercury`, connection from `.env`. Agents never connect to them.
- **Tests:** SQLite in-memory via `--settings=portfolio_manager.settings_test`. Always use it; never run tests against the production settings.
- `requirements.txt` has `django>=5.1` (unpinned): Linux runs 6.1, Windows 5.1.

### Account identification (fingerprints)
- Real RBC account numbers are **not** in the repo or `.env`. `Account` stores a keyed HMAC-SHA256 **fingerprint** (`core/utils/account_fingerprint.py`) plus the **last 4 digits**; the number itself is never stored, printed or logged.
- `ACCOUNT_FINGERPRINT_KEY` (in `.env`, ≥ 32 chars) keys the HMAC. With an empty/short key the fingerprint code fails loudly at the point of use (import commands error out; pages keep working).
- The one-off command `manage.py set_account_fingerprint` **refuses to create accounts**: it requires exactly one Account whose `name` matches the entered name exactly (otherwise it errors and stores nothing), asks for the number twice (hidden prompt) and compares the two entries after normalisation, stores only fingerprint + last 4, prints only the account name and last 4, needs a valid `ACCOUNT_FINGERPRINT_KEY`, and needs `--replace` to overwrite an existing fingerprint.
- Importers (`import_csv`, `import_csv --holdings`, `manage_inbox_import`) resolve accounts by fingerprint and reject unknown fingerprints; `manage_inbox_import` never auto-creates accounts. Scripts that take `--account` require it (no default).
- Tests use fake accounts `90000001`–`90000008` (labels `ACCOUNT_A`…`ACCOUNT_H`) as `Account` rows with `fingerprint=fingerprint_account_number('90000001')` etc., keyed by the fixed test key in `settings_test.py` — no `.env` needed.
- **`PRACEF…` (e.g. `PRACEF8C2DAE`) is a PRACTICE account / Goal container — NEVER import it.** Skip every row whose account id starts with `PRACEF`, in both Activity and Holdings CSV imports.

## Key commands (saturn Ubuntu)

```bash
# Main test suite (220 tests, all pass)
/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test

# pytest suite (normal run: plain tests only, no browser tests, works for Pi)
/opt/pm-venv/bin/python -m pytest tests/ imports/tests/

# browser (Playwright) tests — tdw only, not Pi (needs playwright + browsers in /opt/pm-venv)
RUN_E2E=1 /opt/pm-venv/bin/python -m pytest tests/ imports/tests/ -v

# Migrations check
/opt/pm-venv/bin/python manage.py makemigrations --check --settings=portfolio_manager.settings_test

# Run server (tdw only, not agents)
/opt/pm-venv/bin/python manage.py runserver --settings=portfolio_manager.settings
```

Windows: the same commands with `C:\AI-DevTeam\ai-env\Scripts\python.exe` in PowerShell.

## Ports

| Port | Service | Where | Notes |
|---|---|---|---|
| 11435 | Halogen, Qwen3.8 Flash-Next (vision on) | saturn Ubuntu | Pi planning model (`saturn-halogen`); candidate VL model |
| 11438 | llama.cpp, Qwen3.8 27B (`qwen27B-code`) | saturn Ubuntu | Pi coding model (`saturn-27b`) |
| 11440 | Gufo, Qwen3.8 Flash-Next | saturn Windows | chat; **no longer** the VL model (see VL below) |

Pi (user `piagent`) can reach only 11435 and 11438 — no internet, no other ports.

## Branches
- `dev` — the working line (same as GitHub). `main` — production.
- One branch per feature: `feature/<name>`, made from `dev`. Pi's test-task branches: `pi/<task>`.

## Feature workflow (plans)

- Features are planned in `docs/plans/<feature>.md` before any code is written (see `docs/plans/README.md`).
- When asked to plan: read the relevant code, write the plan file only, change no code, and commit the plan.
- When asked to implement step N: read the plan first, implement that step only, run the tests, commit, then stop and summarise.
- If the plan is unclear or a step seems wrong, stop and ask instead of guessing.

## Import commands
Full reference: `docs/IMPORT_COMMANDS.md`.

| What you want | Command |
|---|---|
| Import all PDFs (annual + monthly) | `manage_inbox_import` |
| Import Activity CSVs from `inbox/` | `import_csv` |
| Import Holdings CSVs into `PositionHistory` | `import_csv --holdings` |
| Parse without writing | `import_csv --dry-run` |
| Watch inbox continuously | `watch_inbox` |
| One-shot inbox import (both PDF types) | `import_inbox` |
| Ledger entries for all open months | `write_ledger --all-open [--account <ID>]` |
| Ledger for one account and month | `write_ledger --account <ID> --month YYYY-MM` |

- `import_csv` is idempotent: deduplicates by `tx_hash`, does not clear existing transactions, skips files already moved to `processed/`.
- **Inbox watcher:** runs as a daemon thread when `runserver` starts; polls `inbox/` every `IMPORT_INBOX_POLL_INTERVAL` seconds (default 300) and routes PDFs to the annual or monthly import.
- **JSON logs:** `manage_inbox_import` writes a JSON log to `processed/json_logs/` for **every** processed PDF (`*_ERROR.json` when the parse has errors). `FileImportLog` still records every import.

## Data sources
- **CSV Activity is the master for transactions.** PDF VL extraction is unreliable (hallucinates and misses transactions).
- **RBC PDF provides scaffolding only:** `RbcMonthly.begin_total_market_value` / `end_total_market_value`, `RbcMonthlyBalance.closing_balance`, holdings snapshots (`RbcMonthlyHolding`). `RbcMonthlyTransaction` is unreliable — don't use it for transactions.
- **Holdings CSV** provides position snapshots for `PositionHistory` (quantity, market_price, market_value, average_cost). Holdings exports are multi-account; the import filters to the account's rows.
- CSV activity + beginning balances + month-end market values → `CoreMonthlyStatement`.

## CSV sign convention
- `amount_cad` is signed: **money out is negative, money in is positive.**
  - Negative: `BUY`, `WDRAW`, `TAX`. Positive: `SELL`, `DEP`, `DIV`/`DIST`/`INT`.
- **Commission is already included in `amount_cad` — never add it separately.**
- `net_flow = sum(tx.amount_cad for tx in month_txs)`.
- Forward: `ending = beginning + net_flow`. Reverse: `beginning = ending - net_flow`.
- Display fields (`total_buys`, `total_commission`) are stored as positive magnitudes.
- Tests that create `type="BUY"` transactions must use **negative** `amount_cad` (e.g. `-1950.00`).

## CSV quantity rules (verified)
- `BUY` +qty; `SELL` −qty.
- **Dividend reinvestment** is a `DIV`/`DIST` row with `qty > 0` and **negative** `amount_cad` (notes start "REI - … REINV@C$X.XX"). Normal dividends have qty 0 and positive amount. Quantity math must add the REI qty.
- `INT` with qty > 0 = RBF2010/RBF2014 interest reinvested as $10 shares (+qty, `amount_cad` 0). CSV `INT` rows with `amount_cad = 0.00` are not cash.
- `REORGANIZATION`: `EXP` +qty for short options, `DIS … STK SPLIT/STK DIV` +qty (qty is the **addition**, e.g. 4:1 on 100 shs → +300), `CIL` 0.
- **Long options** (symbol NOT starting with `9`): `EXP` is −qty (position to 0), unlike short calls (+qty). Branch on `tx.symbol.startswith('9')`.
- `OTHER` `ASN` +qty (short assigned; the share delivery is a separate `SELL` row). `TRANSFER` = 0 (intra-account currency transfer).
- **DLR** (USD currency ETF): every conversion is a paired BUY+SELL (net 0 shares) plus two TRANSFERs.
- Symbol alias: activity `NVRU` = holdings `NVU.UN`.
- The cleaned activity CSV contains some **sign-flipped and missing rows**; known cases are listed in `docs/project-history.md`. Dividend "ON X SHS" counts are the check.

## Currency (sub-account model)
- Most accounts are multi-currency (CAD + USD). **`AccountCurrency`** = one sub-account per (account, currency), with its own `starting_balance` and `starting_balance_date`.
- **Deprecated:** `Account.currency` (now the dominant currency or `'MIX'`; column is `max_length=3`, never `'MIXED'`) and `Account.starting_balance` / `starting_balance_date`.
- Transaction-aware models are per currency: `CoreMonthlyStatement`, `CoreAnnualReport`, `RbcMonthly`, `Transaction`.
- `AccountForm` has no currency/balance fields; use `AccountCurrencyFormSet`. Templates use `account.currencies.all` for currency badges.
- `distinct("currency")` is PostgreSQL-only — use per-currency subqueries (SQLite / SQL Server).

## Ledger
- Models: `LedgerAccount` (per account: CASH, POSITION per symbol, DIVIDEND, INTEREST, TAX, RETURN_OF_CAPITAL), `LedgerEntry` (double entry from Transactions), `LedgerMonthClosure`, `MonthlyLedgerBalance` (quantity, cost_basis_total, cost_basis_per_unit).
- `write_ledger` is **idempotent** (skips transactions that already have a `LedgerEntry`) and **skips closed months** (`LedgerMonthClosure.is_closed`; no close UI yet, so all months are open).
- `REORGANIZATION` uses `tx.notes`: "SPLIT" stock split, "DIV ON" stock dividend, "CIL" cash in lieu, "EXP" option expiration, "DIV REIN" dividend reinvestment, "TFR" transfer. Negative-share reorganization is treated as a stock dividend.
- `TAX` → separate TAX ledger account (DEBIT tax expense + CREDIT cash). `OTHER`/unknown types → warnings, skipped. `OTHER ASSIGNMENT` → ignored (the SELL handles it).
- View: `core/views_ledger.py`, `/ledger/<account_id>/<year>/<month>/` — T-account per position (shares) and cash.

## Where things are
| Feature | Code / command / URL |
|---|---|
| Symbol resolution, fixed prices (RBF2010 CAD 10, RBF2014 USD 10) | `resolve_yahoo_symbol()`, `FixedPriceConfig`, `get_fixed_price()` |
| Current price (yfinance workaround) | `_get_current_price()` in `core/utils/prices.py` |
| Gap analysis | `core/utils/gap_analysis.py`, `/accounts/gap-analysis/` |
| Monthly statements | `generate_monthly_statements`, `/accounts/generate-monthly-statements/` |
| Annual reports | `generate_annual_reports`, `/accounts/generate-annual-reports/` |
| Reverse statements | `reverse_generate_monthly_statements()` in `core/utils/statements.py`, `/accounts/reverse-statements/` |
| Batch statements (anchored on RBC) | `batch_generate_statements [--account ID] [--dry-run]` (all accounts by default); views take `?regenerate=1` / `?batch=1` |
| Reconciliation (CSV vs RBC, $1 threshold) | `reconcile`, `/accounts/reconcile/` |
| Account edit with currency sub-accounts | `/accounts/<id>/edit/` |
| Reconciliation scripts (parked — see docs/architecture.md §11) | `scripts/verify_pdf_integrity.py`, `scripts/ledger_high_change.py`, `scripts/quantity_reconciliation*.py`, `scripts/dump_chains.py` (usage in `docs/project-history.md`) |

## RBC PDF statements
**PDF columns:** `DATE | ACTIVITY | DESCRIPTION | QUANTITY | RATE | DEBIT | CREDIT`. Four sections are parsed: Account Info (`RbcMonthly`), Balances (`RbcMonthlyBalance`), Holdings (`RbcMonthlyHolding`), Account Activity (`RbcMonthlyTransaction`, unreliable).

`RbcMonthlyTransaction.activity_type` is a fixed-choice field (choices in `core/models.py`, enforced by `normalize_activity_type()` in `imports/parsers_monthly_vl.py`): BOUGHT, SOLD, DIST, DIVIDEND, DIV REIN, INTEREST_RECEIVED, INTEREST_PAID, TRANSFER, TFR OUT, WIRE TFR, CONTRIB, DEPOSIT, WITHDRAWAL, ADJUST, RTC, FEE, OTHER.

- **BOUGHT:** cash purchase; `debit` = cost, `quantity` = shares. Description has no "DIST".
- **SOLD:** `debit` or `credit` = proceeds; `quantity` = shares sold.
- **DIST:** cash distribution ("DIST ON X SHS"); `credit` = cash received, `price` = per-share rate (e.g. 0.169), `quantity` = 0. **Cash income, NOT a purchase.**
  - ⚠ An older note says the opposite: `quantity` = per-share rate and the cash is in the `debit` column. Check against an actual PDF before relying on either.
- **DIVIDEND:** `credit` = cash received.
- **DIV REIN:** description has "REINV@C$XX.XX"; `quantity` = new shares, `debit` = cost. Net cash 0.
- **INTEREST_RECEIVED:** RBF2010/RBF2014 or IB interest; `quantity` = new shares (cash value = quantity × 10); price, debit, credit 0. Net cash 0.
- **INTEREST_PAID:** `debit` = amount (IB accounts).
- **TRANSFER / TFR OUT / WIRE TFR / CONTRIB:** `credit` = inflow, `debit` = outflow. **DEPOSIT:** `credit`. **WITHDRAWAL:** `debit`. **FEE:** `debit`. **ADJUST / RTC:** book-cost adjustments, no cash.
- **OPENING / CLOSING BALANCE** lines are not transactions; the parser skips them.
- Cash IN: SOLD, DIVIDEND, DIST, TRANSFER (credit), CONTRIB, DEPOSIT. Cash OUT: BOUGHT, WITHDRAWAL, TFR OUT (debit), FEE, INTEREST_PAID. Net zero: DIV REIN, INTEREST_RECEIVED, ADJUST, RTC.
- PDF net = `credit - debit`. Many older transactions have an empty `symbol` — extract it from `description`.

`normalize_activity_type()` fixes known LLM errors from the description: "DIST ON X SHS" → `DIST`; "REINV@" → `DIV REIN`; "RBC INVESTMENT SAVINGS" → `INTEREST`; `BUY` → `BOUGHT`, `SELL` → `SOLD`, `DIV` → `DIVIDEND`.

**Parser rules:**
- Text matching is case-insensitive (`"- CONTINUED ON NEXT PAGE -"`, Opening/Closing Balance) — use `.lower()`.
- DIST rows put the activity type **after** quantity/rate: date → security → quantity → rate → description → ACTIVITY → "ON X SHS" → "REC … PAY …". The parser scans forward for the activity type.
- Dates like `"JAN. 08"` (dot after the month) must become `datetime.date` via `_parse_date_from_string()` before saving.

### VL model (PDF reading)
- The VL model is env-driven via `.env`: `VL_MODEL_BASE_URL`, `VL_MODEL_NAME`, plus optional `VL_MODEL_API_KEY`, `VL_MAX_TOKENS`, `VL_TEMPERATURE`. Defaults: Halogen on port 11435 (`http://127.0.0.1:11435/v1`), model `halogen-qwen3.8-flash-next` (see `.env.example`; all lines optional).
- If the VL client logs a `finish_reason='length'` warning, the output was truncated — raise `VL_MAX_TOKENS` in `.env` (reasoning tokens count toward the limit).
- PDFs are used only for validation and where CSV data isn't available.

## Architecture notes
- **URLs:** `core.urls` is included at root — routes are `/login/`, `/dashboard/`, etc. (NOT `/core/login/`).
- **yfinance 1.3.0 bug:** `fast_info.last_price` raises `AttributeError: 'PriceHistory' object has no attribute '_dividends'`. Use `_get_current_price()` in `core/utils/prices.py` (falls back to `info[]`, then `history()`).
- **pyright** doesn't understand Django's metaclass magic: use `typeCheckingMode: "basic"`. Known false positives: `logging.handlers` import (string ref in `LOGGING`), `utils_fetch_last_prices` re-export (both `noqa: F401`), `transaction.atomic()` as a context manager, `Style.WARNING` / `Style.SUCCESS`.

## Gotchas
- **Migration numbering:** a data migration must not reuse a schema migration's number (e.g. `0025_populate_…` after `0024_alter_…`).
- **SQL Server `max_length=3` on `Account.currency`:** use `'MIX'`, and `Account.objects.filter(pk=pk).update(currency=...)` instead of `account.save()` to avoid truncation errors where the column was previously wider.
- **Naive datetime warnings** are suppressed in `settings_test.py` (`auto_now_add=True` with TZ support).
- **Tests:** always create a `BrokerStatement` before a `Transaction` (required FK).
- **`compute_tx_hash`** excludes `amount`, so overlapping CSV exports with slightly different amounts produce the same hash.
