# Portfolio Manager — Project History and Reconciliation Findings

Moved out of `AGENTS.md` on 2026-10-20 so the agent instructions stay short. Reference only: this records what was done and found, not current instructions. Current rules are in `AGENTS.md`.

## Phase D (COMPLETE)
**All 7 items implemented and tested (122/122 tests at the time, 0 regressions).**
- D1: Portfolio Summary — account/category subtotals, allocation %, currency breakdown (`portfolio_summary.html`, `group_positions_by_account`, `group_positions_by_category`)
- D2: Performance Chart — cost basis, benchmark, income overlay, date range, chart type selector (`portfolio_chart.html`, `compute_cost_basis_series`, `compute_income_series`, `compute_series_by_account`)
- D3: Dashboard Dollar Summary — market value, P&L, YTD income cards (`dashboard.html`, dashboard view)
- D4: Total Return — realised + income + return cards (`portfolio_summary.html`, row 2)
- D5: Brand & UI — `tsmoney.css`, favicon, site banner, footer, `.page-header` on all templates, `:root` CSS variables
- D6: Top Movers — gainers/losers widget on dashboard (`get_top_movers` in `core/utils/portfolio.py`)
- D7: Dividends Tracker — `DividendSchedule` model, `get_upcoming_dividends`, upcoming dividends widget on dashboard (removed — `DividendSchedule` deleted, see `docs/plans/dividend-schedule.md`)

Branch: `feature_misc_outstanding_items` (merged to `dev`).

Phase D was implemented in an earlier session. The code, views, templates, models and tests were already present when `feature_phaseD_coding` started; all 122 tests passed with 0 regressions. No new code was written then — only infrastructure (AGENTS.md, global_agents.md, zed settings, ai-env config, migration 0016).

## Phase E (COMPLETE — E1, E3-A, E3-B, E3-C, E3-D, E3-E, E4)
**Test count at the time: 183 tests (148 existing + 35 new), 0 regressions.**

### E1: Symbol Resolution + Fixed Prices
- `resolve_yahoo_symbol(symbol, primary_currency)` — adds `.TO` for CAD symbols
- `FixedPriceConfig` model — admin-editable fixed prices for RBF2010 (CAD 10), RBF2014 (USD 10)
- `get_fixed_price(symbol)` — skips yfinance for fixed-price securities
- Updated `fetch_last_prices()` and `load_prices_from_yfinance()` to use resolution

### E3-A: Gap Analysis Report
- `core/utils/gap_analysis.py` — finds the longest continuous run, counts gaps
- View at `/accounts/gap-analysis/` — per-account data completeness

### E3-B: Monthly Statement Generation
- `CoreMonthlyStatement` model — one row per (account, month, currency)
- `generate_monthly_statements(account)` — aggregates transactions by month
- Management command `generate_monthly_statements`; view at `/accounts/generate-monthly-statements/`

### E3-C: Annual Report Generation
- `CoreAnnualReport` model — one row per (account, year, currency)
- `generate_annual_reports(account)` — aggregates from monthly statements
- Management command `generate_annual_reports`; view at `/accounts/generate-annual-reports/`

### E3-D: Reconciliation Engine
- `CoreReconciliation` model — CSV vs RBC comparison results
- `reconcile_monthly()`, `reconcile_annual()` — compare CSV vs RBC with a $1 threshold
- Management command `reconcile`; view at `/accounts/reconcile/`

### E3-E: Account Currency Refactor (Sub-Account Model)
- `AccountCurrency` model — sub-accounts per (account, currency) with their own starting balance
- `Account.currency` deprecated — set to the dominant currency or 'MIX' after migration (max_length=3)
- `Account.starting_balance` / `starting_balance_date` deprecated — moved to the sub-account
- Migrations `0024_alter_*` + `0025_populate_account_currencies` — detect currencies from Transaction/RbcMonthly, create sub-accounts, compute starting balances
- `AccountForm` no longer includes the deprecated fields; `AccountCurrencyFormSet` handles the currency inlines
- `AccountAdmin` has `AccountCurrencyInline` (TabularInline)
- Templates show currency badges from sub-accounts
- The account edit view handles both `AccountForm` and `AccountCurrencyFormSet`; account create redirects to the edit page
- View at `/accounts/<id>/edit/` includes the currency sub-account editor

### E4: Reverse Statement Generation
- `reverse_generate_monthly_statements(account, anchor_month, ending_balances)` in `core/utils/statements.py`
- Walks backwards from a known ending balance, computing beginning values
- Auto-detects ending balances from RbcMonthly data, falling back to `AccountCurrency.starting_balance`
- View at `/accounts/reverse-statements/`; template `core/templates/core/reverse_statements.html`; linked from the accounts page quick tools

### Batch Statement Generation
- `batch_generate_all_statements(account)` in `core/utils/statements.py`
- Anchors on RBC `end_total_market_value` per currency, reverse-generates monthly, then forward-generates annual
- Creates placeholder (zero-activity) statements when no transactions exist but RBC data is present
- Command: `python manage.py batch_generate_statements [--account ID] [--dry-run]`
- Views: default shows existing statements; `?regenerate=1` recalculates from transactions; `?batch=1` anchors on RBC data and fills gaps

Branch: `feature_phaseE_changes_and_fixes`. Migrations: `0023_coreannualreport_coremonthlystatement_and_more.py`, `0024_alter_*`, `0025_populate_account_currencies.py`.

## Phase F (CSV Master Import)
Branch at the time: `feature_csv_master_import`. Test count then: 220 (1 failure in `test_chart_data_empty`, a mock issue). **Fixed by Pi's Task 2 (Oct 14 2026, merged into `dev` Oct 20 2026): 220/220 pass.**

### Changes implemented
- Fixed `compute_tx_hash` — removed `amount`, so overlapping CSV exports with slightly different amounts produce the same hash
- New `PositionHistory.market_price` / `market_value` — from Holdings CSV snapshots
- New `Transaction.settlement_date` — parsed from CSV column 6
- New `BrokerStatement.period_start` / `period_end` — track coverage range
- Shrunk `Transaction.symbol` to max_length=50, `commission` to (10,2)
- New `imports/parsers_holdings.py` — parses Holdings CSV → `PositionHistory`
- New `import_csv` management command — import Activity and Holdings CSVs from inbox
- Ledger system (`LedgerAccount`, `LedgerEntry`, `LedgerMonthClosure`, `MonthlyLedgerBalance`, `write_ledger`, ledger view) — rules are in `AGENTS.md`

## Old branch names (from before the repo was recreated Oct 2026)
`feature/phase*-...`, `feature_misc_outstanding_items`, `feature_phaseE_changes_and_fixes`, `feature_csv_master_import`, `feature_rbc_statements_reconciliation`.

## Reconciliation work (`feature_rbc_statements_reconciliation`)
Branch for verifying PDF data integrity and planning CSV reconciliation.

### Scripts
- `scripts/verify_pdf_integrity.py` — verifies RBC PDF internal consistency
  - Usage: `python scripts/verify_pdf_integrity.py --account 90000001 --currency CAD --start-date 2019-01-01 --start-balance 393437.82`
  - Reports saved to `docs/reconciliation/pdf_integrity_<account>_<currency>.md`
  - Each account gets its own start date, decided during inspection
- `scripts/ledger_high_change.py` — detailed ledger for months with >5% price change
  - Usage: `python scripts/ledger_high_change.py --account 90000001 --currency CAD --start-date 2019-01-01`
  - Reports saved to `docs/reconciliation/ledger_high_change_<account>_<currency>.md`
- **Chain-dump tool:** `scripts/dump_chains.py <db_account_id> <SYM...>` — running balance + dividend "ON X SHS" checkpoints.

### ACCOUNT_A (90000001) CAD
- Start date 2019-01-01, start balance $393,437.82
- 90 PDF statements total (2017-11 to 2026-04), 88 in the analysis (excludes 2 pre-2019)
- Balance chain intact across all 88 statements (only a $12.28 offset on the first statement from the start balance)
- Missing statements (2019-12, 2020-12) were re-imported from inbox
- No USD sub-account

### Monthly vs annual cross-check
- Consistent $1,500–$2,200 mismatch across all years — a systematic offset from different valuation dates.

### CSV row findings (verified 2026-08-15, all-accounts quantity recon)
- **Sign-flipped rows in the cleaned activity CSV** — some BUY rows carry POSITIVE `amount_cad`. Six bogus rows identified (SPHD/VO 2020-11-06 in SUSAN_RRIF; MSFT 2021-07-19, ARKK 2020-12-16 in TOM_TFSA; ZNQ 2020-08-24 in TS_CAR_LIVING) plus PLTR 2020-12-02 "BUY" in TOM_TFSA — dividends' "ON X SHS" counts prove the position never changed. Details: `docs/reconciliation/quantity_recon_findings_2026-08-15.md`.
- **Cleaned CSV is missing all RBF2010/RBF2014 INT rows for Dec 2023, Dec 2024, Dec 2025** (zero INT rows in those months anywhere). Causes the small fractional RBF residuals in every account; restore from source statements.
- **Anchor file gotchas (2019-09-30 hand-made holdings):** TS_HUISGELD VFV must be 3791 (not 5872); TOM_RRSP needs VDY 295 added; SUSAN_RRIF ZLB must be 1612 (REI +9 posts 2019-10-02, so the Sep-30 count is pre-REI).
- `scripts/quantity_reconciliation_all.py::classify()` handles dividend reinvestment as 'DIV-REIN' and branches long vs short options on `tx.symbol.startswith('9')`.
- **Account DB ids:** 60 TOM_LIF, 61 TS_HUISGELD, 62 SUSAN_LIF, 63 TOM_RRSP, 64 SUSAN_RRIF, 65 TOM_TFSA, 66 TS_CAR_LIVING, 67 SUSAN_TFSA (filter `Transaction.account_id` by DB id, NOT broker id).

### Quantity reconciliation, TOM_RRSP (start snapshot + activity = end snapshot)
- **Script:** `scripts/quantity_reconciliation.py` — replays all quantity-affecting transactions from the 2019-04-14 anchor to the 2026-08-02 end snapshot for TOM_RRSP (DB id 63). Report: `docs/reconciliation/quantity_recon_tom_rrsp.md`.
- Both holdings CSVs are multi-account exports; the import filters to the account's rows, so snapshots are complete per account.
- Options in this account are SHORT CALLS: SELL "OPEN CONTRACT" opens the short (negative state is normal), BUY "CLOSE CONTRACT" closes it, EXP/ASN extinguish. All 34 option symbols close to exactly 0.
- DLR (USD currency ETF): not in either snapshot; nets to zero at total level.
- **Result: 25/38 real positions reconcile exactly.** 13 documented gaps = missing rows in the "Cleaned UP" activity CSV, each corroborated by dividend "ON X SHS" share counts: CHP.UN −1518 (missing buy May/Jun 2019), XDIV −1233, RQI −1000, SPY −52 (Sep 2019; Oct 2019 div "ON 309 SHS" = 187+70+52), VDY −295, NVU.UN −700 (anchor price $28.49 vs sale $0.135 — needs review), DGRO +1900 (original anchor lot left the account by Jun/Sep 2019 — dividends prove only 2417 held, not 4317), PCI +772, IRM +278, VOO +50, SHOP +10, RBF2010 −0.419 / RBF2014 −1.718 (unrecorded interest accruals, both liquidated 2026-05-20).
- The PDF cross-check (`RbcMonthlyTransaction`) confirmed the overall picture but is too noisy/hallucinated to fill the gaps.

## Old VL model notes
- VL model was `qwen2.5-vl-7b-instruct-q8_0.gguf` on port 11440 via llama.cpp with `--mmproj`.
- Error `500 - image input is not supported - hint: if this is unexpected, you may need to provide the mmproj` came from the old qwen2.5-vl config. Gemma 4 has a built-in vision encoder (~550M params) and needs no separate `mmproj`.
- A `vl_model` skill lived at `~/.agents/skills/vl_model/` (old agent set-up).

## Old fixes
- Fixed malformed HTML in `accounts.html` — the currency badge span was empty with a stray `</span>`.

## Old agent set-up (retired Oct 2026)
- Windows-only development with `C:\AI-DevTeam\ai-env\` as the only venv (the project's own `venv/` folder was removed), PowerShell only.
- Aider with llama.cpp: `qwen35B-code` on port 11437, `qwen27B-code` on port 11438.
- A global agent file at `C:\Users\tdwdev\.agents\AGENTS.md`, mirrored in the repo as `global_agents.md`, plus global skills in `~/.agents/skills/` (`web-app-debug`). Replaced by Pi on saturn Ubuntu, whose machine notes live in `~/.pi/agent/AGENTS.md`.
