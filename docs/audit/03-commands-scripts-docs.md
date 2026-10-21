# Audit 3 of 4 — Commands, scripts, specs and docs

Scope: every management command, everything in `scripts/`, `specs/`, `docs/` (except `docs/audit/`), loose files in the project root, `.zed/`, and the browser/E2E tests. Method: `ls`, `grep` for references, `git log -1 --format=%cs` per file. Nothing was deleted or moved. Row counts and decisions referenced from `01-data-model.md` and `02-data-flow.md`.

Status key (as instructed):
- **keep** — current, in the live workflow.
- **delete** — already decided for deletion in audit 2 (not yet executed).
- **parked** — belongs to the risk module, the ledger or reconciliation (tdw's parked decisions); leave in place, don't build on.
- **history** — finished one-off work; move to `docs/history/` or delete.
- **stale** — outdated content (Windows paths, port 11440, old models, duplicates).
- **unclear** — question for tdw.

"Refs" = references found outside the file itself (code, AGENTS.md, docs). "Date" = last commit touching the file.

---

## 1. Management commands (49)

### `core/management/commands/` (19)

| Command | One-line purpose | Date | Refs | Status |
|---|---|---|---|---|
| audit_rbc_data | 10-check validation over the Rbc* tables, `--fix` | 2026-06-16 | docs, audit-2 rebuild list | keep |
| batch_generate_statements | Build CoreMonthlyStatement anchored on RBC PDF anchors | 2026-07-13 | views, docs | keep |
| clean_misclassified_annuals | Delete RbcMonthly sets created from misclassified annual PDFs | 2026-07-22 | audit-2 rebuild list | keep |
| close_ledger_month | Close a ledger month (LedgerMonthClosure) | 2026-10-14 | none (docs only) | parked (ledger) |
| fetch_historical_prices | yfinance → HistoricalPrice (canonical price path) | 2026-05-22 | docs, AGENTS | keep |
| generate_annual_reports | Aggregate monthly statements → CoreAnnualReport | 2026-07-06 | views, docs | keep |
| generate_monthly_statements | Forward monthly statements from Transactions | 2026-07-06 | views, docs | keep |
| import_csv | **Canonical** Activity + Holdings CSV import | 2026-10-14 | AGENTS, docs | keep |
| manage_annual_report_import | Annual PDF import (VL) — superseded | 2026-07-22 | watcher, docs | **delete** (audit 2) |
| manage_inbox_import | **Canonical** inbox PDF import (VL classify → monthly/annual) | 2026-07-28 | AGENTS, docs | keep |
| manage_pdf_import | Monthly PDF import — superseded | 2026-07-24 | watcher, docs | **delete** (audit 2) |
| merge_duplicate_accounts | Repair PDF-auto-created accounts against RBC_ACCOUNTS | 2026-10-14 | audit-2 rebuild list, docs | keep |
| rebuild_position_history | Re-derive date_to chains from existing PositionHistory | 2026-10-14 | docs | unclear (Q12 deferred to risk redesign) |
| rebuild_positions_from_transactions | Replay BUY/SELL from anchors, rebuild PositionHistory | 2026-10-14 | none | unclear (Q12 deferred; G9 two-sources conflict) |
| reconcile | CSV vs RBC reconciliation → CoreReconciliation | 2026-07-06 | views, docs | parked (reconciliation) |
| reconcile_holdings_cash | Holdings-vs-cash markdown report | 2026-10-14 | none | parked (reconciliation) |
| reconcile_holdings_from_snapshots | Snapshot-replay holdings report | 2026-10-14 | none | parked (reconciliation) |
| seed_rm_universe | Seeds risk SecurityUniverse/IndexExposure/ScoringConfig | 2026-06-03 | risk docs | parked (risk) |
| write_ledger | Double-entry ledger from Transactions | 2026-08-08 | AGENTS, docs | parked (ledger; also G8 Windows path) |

### `imports/management/commands/` (4)

| Command | One-line purpose | Date | Refs | Status |
|---|---|---|---|---|
| audit_fx | Audit/repair `amount_cad` FX on Transactions | 2026-05-06 | imports/tests, docs | keep |
| auto_import | Duplicate CSV importer (watcher's path; F3 hash bug) | 2026-07-24 | watcher, docs | **delete** (audit 2) |
| import_statements | One-off single-file CSV import (third copy) | 2026-10-14 | docs | **delete** (audit 2) |
| watch_inbox | Standalone inbox watcher loop | 2026-04-29 | docs | keep (must be rewired to canonical paths — audit 2 decision) |

### `risk/management/commands/` (26) — all **parked** (risk module, tdw decision Oct 20)

All dated 2026-06-03 → 2026-06-11, referenced only within risk code/specs. Leave in place, don't run, don't build on.

| Command | One-line purpose | Date | Status |
|---|---|---|---|
| audit_retention_check | Flag old audit-trail records (ImmutableModel blocks deletes) | 2026-06-11 | parked |
| calc_market_conditions | Per-symbol daily technicals → MarketCondition | 2026-06-11 | parked |
| calc_portfolio_metrics | Portfolio-level risk rollup | 2026-06-06 | parked |
| calc_regime | Macro regime quadrant → RegimeState | 2026-06-11 | parked |
| calc_security_metrics | Per-symbol risk metrics → SecurityRiskMetrics | 2026-06-10 | parked |
| calc_strategy_recommendations | Ranked strategies → StrategyRecommendation | 2026-06-11 | parked |
| calc_urgency | Urgency scores → UrgencyScore | 2026-06-11 | parked |
| compute_volatility | VolatilitySummary from price history | 2026-06-03 | parked |
| daily_risk_refresh | Orchestrates the risk calc chain | 2026-06-10 | parked |
| evaluate_alerts | Evaluate AlertConfig → AlertHistory (no UI/email path) | 2026-06-10 | parked |
| fetch_economic_indicators | Macro series → EconomicIndicator | 2026-06-03 | parked |
| fetch_fx_rates | USD/CAD → FxRate | 2026-06-03 | parked |
| fetch_yfinance_prices | OHLCV → MarketPriceHistory (table empty, never run) | 2026-06-03 | parked |
| import_amibroker_prices | AmiQuote CSV watch folder → MarketPriceHistory | 2026-06-03 | parked |
| refresh_market_data | Price + FX refresh wrapper | 2026-06-03 | parked |
| run_backtest | Hedged-vs-unhedged backtests | 2026-06-11 | parked |
| run_montecarlo | Monte Carlo simulation runs | 2026-06-11 | parked |
| run_stress_test | Historical/custom stress runs | 2026-06-11 | parked |
| seed_alert_configs | Seed AlertConfig rows | 2026-06-10 | parked |
| seed_historical_scenarios | Seed HistoricalScenario | 2026-06-06 | parked |
| seed_instrument_catalogue | Seed InstrumentType | 2026-06-06 | parked |
| seed_owner_config | Seed OwnerConfig (tax rates per owner) | 2026-06-06 | parked |
| seed_regime_sensitivity | Seed RegimeSensitivity | 2026-06-06 | parked |
| seed_rm_universe_update | Update risk universe seed data | 2026-06-03 | parked |
| seed_scoring_config | Seed ScoringConfig | 2026-06-06 | parked |
| seed_security_drivers | Seed SecurityDriver | 2026-06-06 | parked |

(core 19 + imports 4 + risk 26 = **49 commands** overall.)

---

## 2. `scripts/` (29 files)

| File | One-line purpose | Date | Refs | Status |
|---|---|---|---|---|
| audit_holdings_data.py | One-off holdings-CSV data audit (Aug holdings work) | 2026-08-02 | none | history |
| check_csv_vs_db.py | Compare CSV dividend predictions vs DB counts | 2026-08-02 | ACCOUNTING_SYSTEM_DESIGN.md | parked (reconciliation) |
| check_db_state.py | Ad-hoc DB state dump; hardcoded `C:\` path | 2026-08-02 | ACCOUNTING_QUICK_START.md | stale |
| check_holdings_csv_keys.py | One-off holdings CSV key-collision check | 2026-08-02 | none | history |
| check_ledger.py | Ledger sanity check; Windows path | 2026-08-02 | none | parked (ledger) |
| comparison_check.py | CSV prediction counts vs DB transaction counts | 2026-08-02 | none | parked (reconciliation) |
| count_csv_activity.py | Count rows/sum Value in inbox activity CSVs | 2026-08-02 | none | history |
| create_portfolio_views.sql | SQL views for the Phase B (risk) data model | 2026-06-03 | specs/PhaseB only | parked (risk) |
| data_quality_audit.py | General data-quality audit over core tables | 2026-08-02 | none | unclear |
| dump_chains.py | Dump PositionHistory date chains | 2026-08-17 | AGENTS.md, project-history | keep |
| export_all_activity.py | Merge all activity CSVs into the master CSV (built the DB) | 2026-08-02 | audit-2 §2.1 | keep |
| fix_bad_transactions.sql | One-off July data fix | 2026-07-22 | none | history |
| fix_open_positions.py | One-off position fix; Windows path | 2026-08-02 | none | history |
| fix_write_ledger.py | Patch/repair write_ledger output | 2026-08-02 | audit-1/2 (ledger) | parked (ledger) |
| generate_accounting_ledger.py | Standalone accounting-ledger generator (uses RBC_ACCOUNTS) | 2026-10-14 | AGENTS.md | parked (ledger) |
| generate_holdings_reports.py | Holdings markdown reports; Windows path | 2026-08-02 | none | history |
| generate_monthly_statements_report.py | Statements markdown report | 2026-08-02 | none | history |
| generate_rbc_monthly_csv.py | Export RbcMonthly to CSV | 2026-06-28 | none | history |
| generate_rbc_pdf_report.py | Generate the RBC PDF coverage report | 2026-06-28 | rbc_pdf_coverage_report.md | history |
| ledger_high_change | Flag high-change ledger months (AGENTS-listed) | 2026-10-14 | AGENTS.md, project-history | parked (ledger) |
| pdf_internal_reconciliation | PDF-internal cross-checks (AGENTS-listed) | 2026-10-14 | AGENTS.md | parked (reconciliation) |
| quantity_reconciliation_all | Quantity reconciliation across sources | 2026-08-17 | project-history, dump_chains | parked (reconciliation) |
| reconcile_data.py | MSSQL reconciliation markdown-report generator | 2026-07-22 | none | parked (reconciliation) |
| reconcile_holdings.py | PositionHistory vs RBC holdings report | 2026-10-14 | AGENTS.md | parked (reconciliation) |
| reconcile_tx.py | Transaction-level CSV-vs-PDF reconciliation | 2026-10-14 | AGENTS.md | parked (reconciliation) |
| test_models.py | Old standalone model smoke script; Windows path | 2026-08-02 | ACCOUNTING_* docs | stale |
| verify_holdings_import.py | Verify holdings CSV import results (Aug holdings work) | 2026-08-02 | none | history |
| verify_ledger_models.py | Ledger model verification | 2026-08-02 | ACCOUNTING_* docs | parked (ledger) |
| verify_pdf_integrity.py | PDF data integrity verification (audit-2 rebuild step) | 2026-10-14 | AGENTS.md, project-history | keep |

Note: AGENTS.md still lists the parked reconciliation scripts as "Reconciliation scripts" — fine while parked, but the section should get a "parked" note when the parked cleanup happens.

---

## 3. `specs/` (64 files, 5 phases)

All specs are historical design documents; none drive current work (current workflow is `docs/plans/`). None reference live code decisions still open, except as noted.

| Phase | Files | Content | Implemented? | Status |
|---|---|---|---|---|
| PhaseA | 25 (`00`–`22`, feature_account_management_ui, Feature Roadmap, recon_annual_vs_monthly, recon_csv_vs_pdf) | Bootstrap, import, dashboard, accounts, auth, PDF import specs | Yes — all shipped (some via later phases) | history |
| PhaseA recon_* (2) | recon_annual_vs_monthly.md, recon_csv_vs_pdf.md | Reconciliation design notes | Superseded by scripts/ | parked (reconciliation) |
| PhaseB | 19 (M0–M8 specs, Architecture v2.0, Spec Outline) | The entire risk module design | Yes (risk app) — module now parked | parked (risk) |
| PhaseC | 7 (C1–C4 specs, Implementation Plan, 2 AI reviews) | Code-review remediation: tx_hash dedup, login security, upload limits, indexes | Yes (tx_hash, auth, validation all in code) | history |
| PhaseD | 9 (01–07, plan, README) | Carry-overs: charts, dashboard, branding, top movers, dividends tracker | Mostly yes — **except 07_upcoming_dividends_tracker** | history (see ⚠) |
| PhaseE | 4 (01–03, plan) | Symbol resolution, CSV↔PDF reconciliation, currency refactor + reverse statements | Yes — all in current code | history |

⚠ `specs/PhaseD/07_updividends… /07_upcoming_dividends_tracker.md` describes the dividends-tracker UI for `DividendSchedule` — a model audit 1 found **unused with no write path** and audit 01-counts confirmed 0 rows. Either the Phase D feature was never finished or it was removed. **unclear** — question for tdw.

Obsolete note: PhaseA `03_rbc_cad_autoimport.md` and `21/22` (PDF import specs) describe the now-deprecated command paths; keep as history, don't follow.

---

## 4. `docs/` (excluding `docs/audit/`)

| File | One-line purpose | Date | Refs | Status |
|---|---|---|---|---|
| ACCOUNTING_IMPLEMENTATION_STATUS.md | Ledger build status report | 2026-08-02 | — | parked (ledger) |
| ACCOUNTING_OPTION_C_UPDATE.md | Ledger "option C" design update | 2026-08-02 | — | parked (ledger) |
| ACCOUNTING_QUICK_START.md | How to run write_ledger etc. | 2026-08-02 | — | parked (ledger) |
| ACCOUNTING_SYSTEM_DESIGN.md | Ledger system design | 2026-08-02 | — | parked (ledger) |
| ACCOUNTING_SYSTEM_SUMMARY.md | Ledger design exec summary | 2026-08-02 | — | parked (ledger) |
| ACCOUNTING_SYSTEM_VIEWS_DESIGN.md | Ledger views design | 2026-10-14 | — | parked (ledger) |
| ADMIN_BRANDING_GUIDE.md | Admin/BrandingConfig theming guide | 2026-06-28 | branding feature live | keep |
| api/models.md, api/services.md, api/views.md | Hand-written API reference (June) | 2026-06-10 | mkdocs | unclear (likely drifted from code; regenerate or drop) |
| APP_STATUS.md | June status snapshot | 2026-06-28 | — | history |
| architecture.md | Architecture overview (June, pre-audit) | 2026-06-10 | mkdocs | unclear (probably drifted; audit 1–2 supersede parts) |
| audit-investigation-sql.md | SQL snippets for RBC data investigation | 2026-06-14 | — | history |
| CODE_REVIEW.md | June code review | 2026-06-28 | PhaseC spec | history |
| CODE_REVIEW_IMPLEMENTATION.md | Fixes from that review | 2026-06-28 | — | history |
| COMPREHENSIVE_CODE_REVIEW.md | Big review; mentions Windows paths + port 11440 | 2026-06-28 | — | stale |
| DEPLOYMENT (2).md | Duplicate/older deployment guide | 2026-06-28 | — | stale (duplicate of deployment.md) |
| deployment.md | Current deployment guide | 2026-10-20 | — | keep |
| DEVELOPMENT_GUIDE.md | Dev guide; Windows paths + port 11440 | 2026-07-05 | — | stale |
| HARDCODING_REFACTORING.md | Summary of a hardcoding cleanup pass | 2026-06-28 | — | history |
| IMPLEMENTATION_PLAN.md | Old implementation plan; Windows paths, 11440 | 2026-06-28 | — | stale |
| IMPLEMENTATION_STATUS_REPORT.md | Status report (Phase C era) | 2026-07-04 | — | history |
| IMPORT_COMMANDS.md | Import command reference (AGENTS points here) | 2026-07-22 | AGENTS.md | keep (update when audit-2 deletions land) |
| index.md | mkdocs landing page | 2026-06-10 | mkdocs.yml | unclear (mkdocs site still wanted?) |
| LOGGING_GUIDE.md | Log locations & troubleshooting | 2026-06-28 | — | keep |
| mkdocs.yml | mkdocs site config | 2026-06-10 | — | unclear (is the mkdocs site built at all?) |
| NEXT_FEATURES.md | Old wishlist | 2026-06-28 | PhaseD spec | history |
| plans/README.md | Plans workflow (current) | 2026-10-20 | AGENTS.md | keep |
| plans/vl-halogen.md | Plan: env-driven VL model → Halogen (implemented) | 2026-10-20 | tests/test_vl_config.py | keep (implemented plan, keep as record) |
| plans/vl-parser-tests.md | Plan: fix VL-parser tests (current) | 2026-10-20 | tests/test_vl_parsers.py | keep |
| project-history.md | Phase history + reconciliation findings (AGENTS reference) | 2026-10-20 | AGENTS.md | keep |
| rbc_balance_reconciliation.sql | Reconciliation SQL pack (uses fake 90000002 example) | 2026-06-28 | — | parked (reconciliation) |
| rbc_csv_vs_pdf_comparison.sql | CSV-vs-PDF comparison SQL | 2026-06-28 | — | parked (reconciliation) |
| rbc-data-load-process.md | Old data-load process doc | 2026-06-14 | — | stale (superseded by IMPORT_COMMANDS.md) |
| rbc_earliest_continuous_data.sql | One-off coverage SQL | 2026-06-28 | — | history |
| rbc_pdf_coverage_report.md | Generated coverage report | 2026-10-14 | generate_rbc_pdf_report.py | history |
| rbc_problematic_files.sql | One-off problem-file SQL | 2026-06-28 | — | history |
| README.md (docs/) | Old docs README; Windows paths | 2026-06-28 | — | stale |
| SECURITY.md | Security hardening guide (Phase C era) | 2026-06-28 | — | unclear (still accurate post-Phase C?) |
| TEXT_PARSER_IMPLEMENTATION.md | Text-based PDF parser "replaces VL" write-up | 2026-10-15 | — | unclear (⚠ see Q below — audit 2 decided to delete the dead text parser) |
| USER_MANUAL.md | User manual (Django 5.1, Windows paths) | 2026-07-13 | — | stale (only user manual; needs rewrite) |
| reconciliation/*.csv (3) | auto_import side files (Imported/Rejected/Duplicates) | untracked (gitignored) | audit-2 §2.1 | runtime artifacts — leave, gitignored; contain only fake account 90000001 |

⚠ `TEXT_PARSER_IMPLEMENTATION.md` (2026-10-15) describes a deterministic text parser "replacing the VL model", but no such parser module exists in `imports/` (only the dead `parsers_pdf.py`, which audit 2 decided to delete). Either the work was lost/reverted or it lives on another branch.

---

## 5. Project root loose files, config, `.zed/`

| File | One-line purpose | Date | Refs | Status |
|---|---|---|---|---|
| calc_march.py | One-off March/April CAD balance calc, hardcoded numbers | 2026-07-13 | none | history (delete candidate) |
| completeness_dashboard.sql | One-off data-completeness SQL dashboard queries | 2026-07-13 | none | history |
| recon_detail.sql | Reconciliation detail SQL | 2026-07-13 | none | parked (reconciliation) |
| recon_report.py | RBC-vs-statements reconciliation console report | 2026-07-13 | none | parked (reconciliation) |
| login_test.html | Saved snapshot of the login page ("TS Money" template) | 2026-07-07 | none | history (delete candidate; no credentials in it) |
| events.jsonl | CrewAI run log from 2026-04-30 agent session | 2026-06-28 | none | history (delete candidate) |
| run_e2e_tests.py | Playwright E2E runner (spawns server + browser tests) | 2026-07-02 | tests/e2e/README | unclear (see §6) |
| config.ini | Non-secret app config — **read by settings.py** | 2026-05-13 | portfolio_manager/settings.py | keep |
| config.ini.example | Template for config.ini | 2026-05-13 | docs | keep |
| db_test.sqlite3 | Empty scratch DB, untracked/gitignored | — | none | history (junk; delete) |
| CONVENTIONS.md | Two-line note: "no hardcoded config, use env vars" | 2026-07-04 | none | unclear (fold into AGENTS.md?) |
| pytest.ini | pytest config for `tests/` (AGENTS-referenced suite) | 2026-07-02 | AGENTS.md | keep |
| requirements.txt / manage.py / .env.example / .gitignore / AGENTS.md | Core project files | — | — | keep |
| .zed/settings.json | Zed editor LSP config; `extraPaths: C:/AI-DevTeam/...` | 2026-07-04 | — | stale (Windows path on a Linux box) |

Also noted (inside `imports/`, outside this audit's scope but stray): `imports/debug_parser.py`, `imports/debug_test.py`, `imports/test_regex.py` — leftover debug scripts; flag for the code audit/cleanup.

---

## 6. E2E / browser tests

Two layers exist: the pytest suite in `tests/` (AGENTS-referenced, runs on saturn) and a Playwright browser suite.

| File | One-line purpose | Date | Refs | Status |
|---|---|---|---|---|
| tests/conftest.py | pytest fixtures (fake accounts, client) | 2026-07-04 | pytest suite | keep |
| tests/test_api_endpoints.py | API endpoint tests (fake `*@test.com` users) | 2026-07-04 | pytest suite | keep |
| tests/test_e2e_user_journey.py | Request-based user-journey tests | 2026-07-04 | pytest suite | keep |
| tests/test_smoke_pytest.py | Smoke tests | 2026-07-04 | pytest suite | keep |
| tests/test_user_workflows.py | Workflow tests | 2026-07-04 | pytest suite | keep |
| tests/test_vl_config.py | VL client config tests (vl-halogen plan) | 2026-10-20 | docs/plans | keep |
| tests/test_vl_parsers.py | VL PDF parser tests | 2026-10-20 | docs/plans | keep |
| tests/README.md | How to run the pytest suite | 2026-07-02 | — | keep |
| tests/e2e/README.md | Playwright setup (references Windows `ai-env`, `run_e2e_tests.py`) | 2026-07-02 | run_e2e_tests.py | unclear |
| tests/e2e/conftest.py, run_all_tests.py | Playwright fixtures + runner | 2026-07-02 | run_e2e_tests.py | unclear |
| tests/e2e/test_login.py, test_portfolio.py, test_reports.py, test_pdf_import.py, test_smoke.py, test_complete_app.py, test_example.py | Browser tests (login, portfolio, reports, PDF upload, smoke) | 2026-07-02/04 | run_e2e_tests.py | unclear (Playwright was set up on Windows `ai-env`; do they run on saturn? `test_pdf_import.py` also targets the web upload views audit 2 decided to delete) |
| tests/e2e/PRODUCTION_READINESS_REPORT.md | June readiness report | 2026-07-02 | — | history |
| tests/e2e/report.html, tests/e2e/tests/e2e/report.html, e2e/report.html | Committed pytest-HTML artifacts (one nested-duplicate folder) | 2026-07-02 | none | history (delete candidates) |
| run_e2e_tests.py | Runner for the Playwright suite | 2026-07-02 | tests/e2e/README | unclear (keep only if the suite is kept) |

---

## Summary counts (per status)

| Group | keep | delete (audit 2) | parked | history | stale | unclear |
|---|---|---|---|---|---|---|
| Commands (49) | 11 | 4 | 32 | 0 | 0 | 2 |
| scripts/ (29) | 3 | 0 | 13 | 10 | 2 | 1 |
| specs/ (64) | 0 | 0 | 21 (PhaseB 19 + PhaseA recon 2) | 42 | 0 | 1 (PhaseD dividends) |
| docs/ (≈40) | 11 | 0 | 8 | 12 | 6 | 6 |
| Root + .zed (14) | 7 | 0 | 2 | 5 | 2 | 2 |
| Tests/E2E (≈20) | 9 | 0 | 0 | 4 | 0 | 10 |
| **Total** | **41** | **4** | **76** | **73** | **10** | **22** |

Parked = risk (27 commands + 1 script + 21 specs = 49), ledger (13), reconciliation (14). Nothing parked is proposed for deletion — only for clear labelling.

## Proposed clean-up (small batches, one fix branch each)

Nothing here is urgent; batches 2–7 are independent of each other.

1. **`pi/remove-deprecated-imports`** — execute audit 2's decided deletions: `manage_pdf_import`, `manage_annual_report_import`, `auto_import` (command), `import_statements`, `imports/parsers_pdf.py`, `imports/parsers_annual.py` (+ its test), web upload views; rewire the watcher to `import_csv` / `import_csv --holdings` / `manage_inbox_import`. Update `IMPORT_COMMANDS.md` in the same branch.
2. **`pi/remove-junk-artifacts`** — delete committed junk: `e2e/report.html`, `tests/e2e/report.html`, the nested `tests/e2e/tests/` folder, `login_test.html`, `events.jsonl`, `calc_march.py`; delete untracked `db_test.sqlite3`.
3. **`pi/history-scripts`** — move the 10 `history` scripts (audit_holdings_data, check_holdings_csv_keys, count_csv_activity, fix_bad_transactions.sql, fix_open_positions, generate_holdings_reports, generate_monthly_statements_report, generate_rbc_monthly_csv, generate_rbc_pdf_report, verify_holdings_import) to `docs/history/scripts/` (or delete if tdw prefers).
4. **`pi/history-docs`** — create `docs/history/` and move: APP_STATUS, CODE_REVIEW, CODE_REVIEW_IMPLEMENTATION, HARDCODING_REFACTORING, IMPLEMENTATION_STATUS_REPORT, NEXT_FEATURES, audit-investigation-sql, rbc_earliest_continuous_data.sql, rbc_problematic_files.sql, rbc_pdf_coverage_report.md, tests/e2e/PRODUCTION_READINESS_REPORT.md.
5. **`pi/stale-docs`** — delete `DEPLOYMENT (2).md`; fix or move to history: COMPREHENSIVE_CODE_REVIEW, IMPLEMENTATION_PLAN, DEVELOPMENT_GUIDE (Windows paths, port 11440), docs/README.md; fix `.zed/settings.json` extraPaths for Linux; decide USER_MANUAL (rewrite for saturn/Linux vs mark as known-stale).
6. **`pi/parked-labels`** — add a one-line "PARKED (see docs/audit)" banner to the 6 ACCOUNTING_* docs, the reconciliation SQL/docs, and the AGENTS.md "Reconciliation scripts" section; move `specs/PhaseB/` to `specs/parked/PhaseB/` (or just note it in `specs/` README). No code or table moves.
7. **`pi/specs-archive`** — move PhaseA/C/D/E specs to `specs/archive/` (or `docs/history/specs/`) once tdw answers the dividends-tracker question; leave PhaseB per batch 6.

## Questions for tdw

1. **Playwright suite** (`tests/e2e/`, `run_e2e_tests.py`): does it still run anywhere (it was set up on Windows `ai-env`)? If not, delete the suite and its committed `report.html` artifacts. `test_pdf_import.py` targets the web upload views that audit 2 decided to delete.
2. **PhaseD dividends tracker** (`specs/PhaseD/07_upcoming_dividends_tracker.md`): the `DividendSchedule` model has 0 rows and no write path (audit 1). Was the tracker ever finished? Delete the spec + model together?
3. **TEXT_PARSER_IMPLEMENTATION.md** (2026-10-15): it describes a text parser "replacing VL", but no such parser is in `imports/` and audit 2 decided to delete the dead text parser. Lost branch, reverted experiment, or should VL stay canonical?
4. **mkdocs site** (`docs/mkdocs.yml`, `index.md`, `api/*`): is the generated site used? The `api/` references are from June and have likely drifted.
5. **rebuild commands** (`rebuild_position_history`, `rebuild_positions_from_transactions`): audit 2 Q12 deferred PositionHistory truth to the risk redesign — park both commands until then?
6. **data_quality_audit.py**: still a useful general check, or superseded by `audit_rbc_data` + the audits?
7. **CONVENTIONS.md**: two lines; fold into AGENTS.md and delete?
8. **SECURITY.md**: still current after Phase C, or archive?
9. **USER_MANUAL.md**: the only user-facing doc but Windows/5.1-era. Rewrite for saturn, or drop until there are real users?
10. **`imports/debug_parser.py`, `imports/debug_test.py`, `imports/test_regex.py`**: stray debug files in the app — delete?

## Personal-data flags (flag only — audit 4)

Searched all tracked files in scope for 8-digit numbers, e-mail patterns and names:
- **No real account numbers found in tracked files.** All hits are the fake test accounts `90000001`–`90000008` (scripts, docs, `docs/reconciliation/*.csv`) and `12345678`.
- **`events.jsonl`** — an unreviewed CrewAI session log (agent prompts/tasks); recommend a manual skim in audit 4 before it's deleted or kept.
- **`docs/project-history.md`** — reconciliation narrative quoting statement text ("REI … ON X SHS" counts, dates, amounts); may quote real statement content — flag for audit 4 review.
- **`docs/reconciliation/*.csv`** — gitignored runtime artifacts; currently only fake-account rows, but they are generated from whatever CSVs are in `inbox/`, so real data can land there.
- **`login_test.html`** — page snapshot only, no credentials.
- **`.env`** — untouched, as always; real account numbers live there and in the mercury DB, not in this repo.
- Test e-mails are all `*@test.com` / `test@example.com` — no PII.
