# Audit 1 of 4 — Data Model

Scope: every Django model in the project (apps `core`, `imports`, `risk`), generated 2026 audit pass.
Method: `models.py` files + grep for readers/writers across views, management commands, scripts, utils, templates, admin and tests. No code was changed.

Totals: **63 concrete models** — `core` 23, `risk` 39 (+1 abstract `ImmutableModel`, no table), `imports` 0 (placeholder `imports/models.py`, no migrations dir).
Migrations: **core 39**, **risk 16**, **imports 0**.

Classification key:
- **core** — in active use by the main data flow (import → statements → ledger → risk).
- **supporting** — config, lookup, log, audit or validation role; active but not the main line.
- **deprecated** — superseded; kept for compatibility.
- **unused** — no live write path and/or no live reader found.

## Summary table

| App | Model | Table | Class | One-line purpose |
|---|---|---|---|---|
| core | Account | `core_account` | core | Brokerage account (RBC/IB); `account_id` may hold real account numbers |
| core | AccountCurrency | `core_accountcurrency` | core | Per-currency sub-account with its own starting balance (replaces Account.currency) |
| core | Transaction | `core_transaction` | core | Master transaction rows from CSV activity (CSV is the master source) |
| core | BrokerStatement | `core_brokerstatement` | core | Imported statement-file wrapper; required FK parent of Transaction |
| core | PositionHistory | `core_position_history` | core | Position snapshots from Holdings CSV (date_from/date_to chains) |
| core | CoreMonthlyStatement | `core_monthly_statement` | core | Generated monthly statement per (account, month, currency) |
| core | CoreAnnualReport | `core_annual_report` | core | Generated annual report per (account, year, currency) |
| core | LedgerAccount | `core_ledger_account` | core | Double-entry ledger account (CASH/POSITION/INCOME) per brokerage account |
| core | LedgerEntry | `core_ledger_entry` | core | Double-entry ledger entries generated from Transactions |
| core | TransactionType | `core_transactiontype` | supporting | Lookup of tx type codes, auto-created on import |
| core | RbcMonthly | `core_rbcmonthly` | supporting | Parsed RBC PDF monthly statement header (scaffolding/validation only) |
| core | RbcMonthlyBalance | `core_rbcmonthlybalance` | supporting | Asset-category balances from RBC PDF |
| core | RbcMonthlyTransaction | `core_rbcmonthlytransaction` | supporting | RBC PDF activity rows — known unreliable, validation only |
| core | RbcMonthlyHolding | `core_rbcmonthlyholding` | supporting | RBC PDF holdings snapshot rows |
| core | RbcAnnual | `core_rbcannual` | supporting | Flat RBC annual performance report (per account/year/currency) |
| core | FileImportLog | `core_fileimportlog` | supporting | Import audit log (file, counts, status) |
| core | BrandingConfig | `core_brandingconfig` | supporting | Singleton branding/theme config |
| core | HistoricalPrice | `core_historicalprice` | supporting | Daily close prices fetched by `fetch_historical_prices` |
| core | FixedPriceConfig | `core_fixed_price_config` | supporting | Manual fixed prices (RBF2010/RBF2014 etc.) |
| core | CoreReconciliation | `core_reconciliation` | supporting | CSV-vs-RBC reconciliation results per period |
| core | MonthlyLedgerBalance | `core_monthly_ledger_balance` | supporting | Month-end ledger snapshot + CSV/RBC verification flags |
| core | LedgerMonthClosure | `core_ledger_month_closure` | supporting | Month-close gate per (account, month, currency); no close UI yet |
| core | DividendSchedule | `core_dividendschedule` | **unused** | Manually tracked expected dividends — **no write path exists** |
| risk | SecurityUniverse | `rm_security_universe` | core | Universe of tracked symbols with flags (held/benchmark/hedge/CEF) |
| risk | MarketPriceHistory | `rm_market_price_history` | core | Daily OHLCV price history for risk symbols |
| risk | SecurityRiskMetrics | `rm_security_risk_metrics` | core | Daily per-symbol risk metrics (vol, drawdown, beta, Sharpe…) |
| risk | RegimeState | `rm_regime_state` | core | Daily macro regime quadrant + indicator scores |
| risk | MarketCondition | `rm_market_condition` | core | Daily per-symbol technical indicators (MA/MACD/BB/OBV/phase) |
| risk | PortfolioRiskMetrics | `rm_portfolio_risk_metrics` | core | Aggregated risk per account/owner/portfolio per date |
| risk | UrgencyScore | `rm_urgency_score` | core | Daily urgency score per risk entity with component breakdown |
| risk | StrategyRecommendation | `rm_strategy_recommendation` | core | Ranked strategy recommendation per position/account/date |
| risk | StrategyTemplate | `rm_strategy_template` | core | Catalogue of risk-management strategies |
| risk | InstrumentType | `rm_instrument_type` | core | Catalogue of instrument types (mechanics, eligibility, tax) |
| risk | SimulationRun | `rm_simulation_run` | core | Monte Carlo / stress run inputs |
| risk | SimulationResult | `rm_simulation_result` | core | Monte Carlo / stress outputs (percentiles, ES, stress) |
| risk | BacktestRun | `rm_backtest_run` | core | Backtest execution record (append-only) |
| risk | BacktestResult | `rm_backtest_result` | core | Backtest outputs (1-1 with BacktestRun) |
| risk | VolatilitySummary | `rm_volatility_summary` | supporting | Stored annualised volatility per symbol/date |
| risk | FxRate | `rm_fx_rates` | supporting | Daily USD/CAD rate |
| risk | EconomicIndicator | `rm_economic_indicators` | supporting | Macro indicator values feeding regime detection |
| risk | CorrelationMatrix | `rm_correlation_matrix` | supporting | Pairwise symbol correlations |
| risk | RegimeSensitivity | `rm_regime_sensitivity` | supporting | Seed config: sensitivity multiplier per (asset class, quadrant) |
| risk | SecurityDriver | `rm_security_driver` | supporting | Seed config: static risk-driver characteristics per symbol |
| risk | ScoringConfig | `rm_scoring_config` | supporting | Key/value scoring weights and thresholds |
| risk | SecurityIndexExposure | `rm_security_index_exposure` | supporting | Seed config: symbol → index exposure weights |
| risk | OwnerConfig | `rm_owner_config` | supporting | Per-owner tax rates and preferences |
| risk | StrategyInstrumentLink | `rm_strategy_instrument_link` | supporting | Strategy ↔ instrument-type join table |
| risk | AlternativePortfolio | `rm_alternative_portfolios` | supporting | Hypothetical "what-if" portfolio for comparison |
| risk | AlternativeHolding | `rm_alternative_holdings` | supporting | Target-weight holdings of an AlternativePortfolio |
| risk | DataQualityLog | `rm_data_quality_log` | supporting | Price-gap / fetch-failure log |
| risk | HistoricalScenario | `rm_historical_scenario` | supporting | Seeded historical stress scenarios |
| risk | CustomStressScenario | `rm_custom_stress_scenario` | supporting | User-defined shock scenarios (admin-managed) |
| risk | TaxDecisionAcknowledgement | `rm_tax_decision_acknowledgement` | supporting | User acknowledgement of tax impact of a recommendation |
| risk | AlertConfig | `rm_alert_config` | supporting | Alert threshold definitions |
| risk | AlertHistory | `rm_alert_history` | supporting | Fired-alert records (written by `evaluate_alerts`; no UI) |
| risk | AuditEntry | `rm_audit_entry` | supporting | Append-only record of each risk calculation run |
| risk | AuditActionLog | `rm_audit_action_log` | supporting | Append-only user actions on recommendations |
| risk | AuditRetentionLog | `rm_audit_retention_log` | supporting | Append-only retention-check runs |
| risk | AuditScoreSnapshot | `rm_audit_score_snapshot` | supporting | Append-only urgency-score snapshots |
| risk | AuditRecommendationLog | `rm_audit_recommendation_log` | supporting | Append-only record of every strategy considered |
| risk | AuditSimulationRun | `rm_audit_simulation_run` | supporting | Append-only simulation/backtest run records |
| risk | AuditRegimeLog | `rm_audit_regime_log` | supporting | Append-only regime + market-condition summary per date |

## core app (23 models, 39 migrations)

### Account — `core_account` — core
Brokerage account. Key fields: `account_id` (unique, e.g. real RBC number), `name`, `broker` (IB/RBC), `category` (RRSP/TFSA/…), `owner`, `exclude_from_risk`. **Deprecated fields: `currency` (now dominant-currency or `'MIX'`), `starting_balance`, `starting_balance_date`** — superseded by `AccountCurrency`. Written by account CRUD views/forms, `merge_duplicate_accounts`; read almost everywhere (core + risk views, commands, scripts, templates). PII: `account_id`, `name`, `owner`.

### AccountCurrency — `core_accountcurrency` — core
One row per (account, currency) with its own `starting_balance`/`starting_balance_date`; FK → Account (`currencies`). Written by `core/forms.py` (AccountFormSet); read by `utils/statements.py`, views, scripts, tests. Date col for counts: `starting_balance_date`.

### Transaction — `core_transaction` — core
The master transaction table (CSV activity is the source of truth). FKs: `statement` → BrokerStatement (CASCADE), `account` → Account (SET_NULL, nullable). Key fields: `date`, `symbol`, `type`, `quantity`, `price`, `commission`, `amount`, `currency`, `amount_cad` (signed), `tx_hash` (dedup). Written by `import_csv`, `imports/parsers.py`, `auto_import`; read by nearly every core view/util/command, ledger writer, risk tests, many scripts. PII: `notes` (broker description text).

### BrokerStatement — `core_brokerstatement` — core
Statement-file wrapper required as FK parent of every Transaction. Fields: `source`, `file_date`, `raw_filename`, `period_start`, `period_end`. Written by import commands/parsers; read by views_transactions/views_portfolio and tests. PII: `raw_filename` may embed account numbers.

### PositionHistory — `core_position_history` — core
Holdings-CSV snapshots: `owner`, FK `account`, `symbol`, `quantity`, `cost_basis_per_unit/total`, `market_price`, `market_value`, `date_from`/`date_to` chain, `currency`. Written by `import_csv --holdings` (via `imports/parsers_holdings.py`), `rebuild_position_history`, `rebuild_positions_from_transactions`; read by risk services (`metrics.py`, `backtest_engine.py`) and many reconcile scripts. PII: `owner`.

### CoreMonthlyStatement — `core_monthly_statement` — core
Generated monthly statement per (account, month, currency): begin/end market value, buys/sells/dividends/interest/commission, `net_flow`, `ending_cash_balance`. Written by `generate_monthly_statements`, `reverse_generate_monthly_statements`, `batch_generate_statements` (via `utils/statements.py`); read by dashboard/views, reconciliation utils, scripts.

### CoreAnnualReport — `core_annual_report` — core
Generated annual report per (account, year, currency). Written by `generate_annual_reports`; read by views, reconciliation. Date col: `report_year` (integer, not a date).

### LedgerAccount — `core_ledger_account` — core
Double-entry ledger accounts (CASH / POSITION / INCOME) auto-created per brokerage account/currency/symbol; carries running `quantity`, `cost_basis_total/per_unit`. Denormalized `account_id_str`, `account_name` (PII mirror). Written by `write_ledger` (+ `scripts/fix_write_ledger.py`); read by `views_ledger`, scripts.

### LedgerEntry — `core_ledger_entry` — core
One DEBIT/CREDIT per Transaction leg; FKs `ledger_account` → LedgerAccount, `source_transaction` → Transaction (audit trail); `statement_month` for period reporting. Written by `write_ledger`; read by `views_ledger`, scripts.

### TransactionType — `core_transactiontype` — supporting
Lookup of tx codes with `affects_position`/`affects_cash`; auto-created during import (`imports/parsers.py`). Read by dashboard view (`views.py`) and admin. Low traffic but live.

### RbcMonthly — `core_rbcmonthly` — supporting
Parsed RBC PDF monthly header, one row per (account, statement_date, currency, file). Provides `begin/end_total_market_value` scaffolding used by statements/reconciliation/gap analysis. **PII: `account_number` field.** Written by `manage_inbox_import` / `manage_pdf_import` (deprecated cmd) via `imports/parsers_monthly_vl.py`; read by `utils/statements.py`, `utils/reconciliation.py`, `utils/gap_analysis.py`, reconcile commands, many scripts.

### RbcMonthlyBalance — `core_rbcmonthlybalance` — supporting
Asset-category balances (Cash, Mutual Funds, …) per RbcMonthly; `closing_balance` used as reconciliation anchor. Written by monthly PDF parsers; read by reconcile/audit commands and scripts. No own date column (date via parent).

### RbcMonthlyTransaction — `core_rbcmonthlytransaction` — supporting
PDF activity rows with fixed `activity_type` choices. Documented as **unreliable** (AGENTS.md) — used only for validation/cross-checks (`gap_analysis.py`, `scripts/reconcile_tx.py`, `pdf_internal_reconciliation.py`), never as a transaction source. PII: `description`.

### RbcMonthlyHolding — `core_rbcmonthlyholding` — supporting
PDF Asset Review rows (security, symbol, quantity, book cost, price, market value). Written by monthly PDF parsers; read by holdings reconciliation commands/scripts. No own date column.

### RbcAnnual — `core_rbcannual` — supporting
Flat replacement for the old six-table annual hierarchy (created in migration 0017): balances, money-weighted returns, charges, compensation per (account, year, currency). Written by `imports/parsers_annual.py` via `manage_inbox_import`/`manage_annual_report_import` (deprecated cmd); read by annual-report views (bridged via a fake-object shim in `views.py`/`views_reports.py`) and reconciliation. **PII: `account_number`, `account_type_code`.**

### FileImportLog — `core_fileimportlog` — supporting
Per-file import audit (filename, broker, counts, status, `content_hash`). Written by import commands; read by import-history views. PII: `filename`.

### BrandingConfig — `core_brandingconfig` — supporting
Singleton (pk forced to 1) theme/branding config. Written only via admin; read by `core/context_processors.py` and base template.

### HistoricalPrice — `core_historicalprice` — supporting
Daily `symbol/date/close` for portfolio symbols; written by `fetch_historical_prices`; read by `core/utils/prices.py` and `portfolio.py` (performance charts). Overlaps risk's `MarketPriceHistory` (see findings).

### FixedPriceConfig — `core_fixed_price_config` — supporting
Manual prices for non-yfinance instruments (RBF2010/RBF2014 @ $10). Written via admin; read by `get_fixed_price()` in `core/utils/prices.py`.

### CoreReconciliation — `core_reconciliation` — supporting
CSV-vs-RBC match/discrepancy rows per (account, type, period). Written/read by `core/utils/reconciliation.py` (the `reconcile` command) and admin.

### MonthlyLedgerBalance — `core_monthly_ledger_balance` — supporting
Month-end ledger snapshot with CSV/RBC market-value verification columns and close flags. Written by `write_ledger` and `close_ledger_month`; read by `views_ledger`.

### LedgerMonthClosure — `core_ledger_month_closure` — supporting
Per (account, month, currency) closure gate; `write_ledger` skips closed months. Written by `close_ledger_month` command only — **no close UI exists yet** (per AGENTS.md).

### DividendSchedule — `core_dividendschedule` — apparently unused
"Manually tracked expected dividends" for cash-flow planning. Only reader found: a helper inside `core/utils/portfolio.py` (lazy import). **No admin registration, no management command, no view or form writes it** — nothing in the codebase can populate the table. Either dead or populated by hand in the DB. Flagged for tdw.

## imports app (0 models, 0 migrations)

`imports/models.py` is a one-line placeholder ("no models in this app"). The app holds parsers, the watcher and import commands that write core tables. No table, nothing to count.

## risk app (39 concrete models + abstract ImmutableModel, 16 migrations)

The risk subsystem reads core data cross-app (`Account`, `PositionHistory` via `risk/services/metrics.py`, `backtest_engine.py`) and stores its own inputs/outputs below. All risk tables use explicit `rm_*` names.

### Data layer
- **SecurityUniverse — `rm_security_universe` — core.** Symbol registry with flags (`is_held`, `is_benchmark`, `is_hedge`, `is_cef`, `use_jump_diffusion`, `amibroker_symbol`). Written by `seed_rm_universe(_update)`, admin; read by nearly every risk service/command/view.
- **MarketPriceHistory — `rm_market_price_history` — core.** Daily OHLCV + `data_source`. Written by `fetch_yfinance_prices`, `import_amibroker_prices`; read by volatility/metrics/gap services. Overlaps core's `HistoricalPrice` (see findings).
- **VolatilitySummary — `rm_volatility_summary` — supporting.** Stored annualised vol per symbol/calc_date; written/read by `services/volatility.py`, used by `calc_strategy_recommendations`.
- **FxRate — `rm_fx_rates` — supporting.** Daily USD/CAD; written by `fetch_fx_rates`; read by metrics/stress/portfolio commands.
- **EconomicIndicator — `rm_economic_indicators` — supporting.** Macro series feeding regime detection; written by `fetch_economic_indicators`; read by `calc_regime`.
- **AlternativePortfolio / AlternativeHolding — `rm_alternative_portfolios` / `rm_alternative_holdings` — supporting.** User-created what-if portfolios (FK chain; holding = symbol + target_weight). Written/read by `risk/views/alternatives.py`, `compare.py`. PII: `owner`.
- **ScoringConfig — `rm_scoring_config` — supporting.** Key/value scoring weights; seeded by `seed_scoring_config`, read by urgency/portfolio/regime engines.
- **SecurityIndexExposure — `rm_security_index_exposure` — supporting.** Seed config of symbol→index exposure; read by `calc_portfolio_metrics`. No date column.
- **DataQualityLog — `rm_data_quality_log` — supporting.** Gap/fetch-failure events; written by `services/gap_detection.py` and `import_amibroker_prices`; read via admin.

### Metrics layer
- **SecurityRiskMetrics — `rm_security_risk_metrics` — core.** Per-symbol daily metrics (drawdown, beta SPY/XIU, Sharpe, Sortino, liquidity, UGL-at-risk). Written by `calc_security_metrics`; read by risk views and urgency engine.
- **CorrelationMatrix — `rm_correlation_matrix` — supporting.** Pairwise correlations; written by `calc_security_metrics`/`calc_portfolio_metrics` (`services/metrics.py`), read by portfolio metrics.
- **RegimeState — `rm_regime_state` — core.** One row per `calc_date`: quadrant, confidence, indicator scores + JSON snapshot. Written by `calc_regime`; read by dashboard/security/account views, urgency engine.
- **MarketCondition — `rm_market_condition` — core.** Per-symbol daily technicals (MA crossover, MACD, Bollinger, OBV, phase). Written by `calc_market_conditions`; read by views and urgency engine.
- **RegimeSensitivity — `rm_regime_sensitivity` — supporting.** Seed multipliers per (asset_class, quadrant) (`seed_regime_sensitivity`). No date column.
- **PortfolioRiskMetrics — `rm_portfolio_risk_metrics` — core.** Aggregated per-entity (entity_type/entity_id string, not FK) risk per date: vol, betas, Sharpe/Sortino, HHI, effective sector weights, tax-risk flag, market values USD/CAD. Written by `calc_portfolio_metrics`; read by dashboard/heatmap/owner/compare views.
- **SecurityDriver — `rm_security_driver` — supporting.** Static per-symbol driver characteristics (seeded by `seed_security_drivers`), read by urgency engine.

### Catalogue & strategy
- **InstrumentType — `rm_instrument_type` — core.** Instrument catalogue (mechanics/eligibility/tax as JSON text columns). Seeded by `seed_instrument_catalogue`; read by `services/catalogue.py`, strategy views.
- **StrategyTemplate — `rm_strategy_template` — core.** Strategy catalogue with applicability rules; seeded, read by recommendation engine, backtests, views.
- **StrategyInstrumentLink — `rm_strategy_instrument_link` — supporting.** FK join strategy ↔ instrument type. No date column.
- **UrgencyScore — `rm_urgency_score` — core.** Daily urgency per entity (entity_type/entity_id string, symbol/account_id strings). Written by `calc_urgency`/`calc_strategy_recommendations` (`services/urgency_engine.py`); read by most risk views. PII: `account_id`, `symbol`.
- **OwnerConfig — `rm_owner_config` — supporting.** Per-owner marginal tax rate, inclusion rate, cost tolerance, preferences JSON. Seeded by `seed_owner_config`; read by strategy-cost engine. PII: owner name + tax rates.
- **StrategyRecommendation — `rm_strategy_recommendation` — core.** Ranked recommendation per (symbol, account_id string, calc_date) with urgency, position snapshot, cost/tax/ VaR-reduction estimates, status/actioned fields. Written by `calc_strategy_recommendations`; read by strategy/security/dashboard views. PII: `owner`, `account_id`.
- **TaxDecisionAcknowledgement — `rm_tax_decision_acknowledgement` — supporting.** FK → StrategyRecommendation; presented/acknowledged timestamps and user notes. Written by `risk/views/strategy.py`; read by `services/tax_acknowledgement.py` gating. PII: user names.
- **HistoricalScenario — `rm_historical_scenario` — supporting.** Seeded historical scenarios with asset-class/benchmark returns JSON. Written by `seed_historical_scenarios`; read by backtest/stress engines.
- **BacktestRun / BacktestResult — `rm_backtest_run` / `rm_backtest_result` — core.** Append-only run record + 1-1 results (hedged vs unhedged returns, drawdowns, hedge cost/effectiveness). Written by `run_backtest` via `services/backtest_engine.py`; read by strategy/security views, `services/backtest_metrics.py`, export.
- **SimulationRun / SimulationResult — `rm_simulation_run` / `rm_simulation_result` — core.** Monte Carlo / stress inputs and outputs (percentiles, prob-loss buckets, expected shortfall, hedged variants, JSON series). Written by `run_montecarlo` / `run_stress_test`; read by `risk/views/stress.py`.
- **CustomStressScenario — `rm_custom_stress_scenario` — supporting.** User-defined shock JSON; managed via admin, consumed by `run_stress_test`.

### Alerting
- **AlertConfig — `rm_alert_config` — supporting.** Alert definitions (threshold, operator, scope owner/account/symbol, `notify_email` — PII). Seeded by `seed_alert_configs`, managed via admin.
- **AlertHistory — `rm_alert_history` — supporting.** Fired alerts written by `evaluate_alerts` via `services/alert_engine.py`. **No view/URL reads alerts** — `services/alerts.py` (query/acknowledge helpers) is referenced only by tests. Flagged for tdw.

### Audit trail (all inherit abstract `ImmutableModel` — append-only, delete blocked)
- **AuditEntry — `rm_audit_entry`** — generic calc-run record (input/output JSON, status). Written by `services/audit_hooks.py` and most calc commands.
- **AuditActionLog — `rm_audit_action_log`** — user actions on recommendations (FK → StrategyRecommendation). Written by `risk/views/strategy.py`.
- **AuditRetentionLog — `rm_audit_retention_log`** — retention-check runs (`audit_retention_check`).
- **AuditScoreSnapshot — `rm_audit_score_snapshot`** — urgency snapshot with weights/inputs JSON; read by `views/explain.py`, `services/audit_rationale.py`.
- **AuditRecommendationLog — `rm_audit_recommendation_log`** — every strategy considered per (symbol, account, date), eligible or rejected, with rationale JSON. Written by `audit_hooks`.
- **AuditSimulationRun — `rm_audit_simulation_run`** — append-only mirror of backtest/MC/stress runs (`source_run_id` is a bare int, not an FK).
- **AuditRegimeLog — `rm_audit_regime_log`** — per-date regime + market-breadth summary.
All audit tables are also retention-managed by `audit_retention_check` and read via admin (read-only registrations).

## Findings

### Deprecated
1. **`Account.currency`, `Account.starting_balance`, `Account.starting_balance_date`** — documented deprecated in favour of `AccountCurrency`; still present and written (currency auto-set to dominant/`'MIX'`). Candidates for removal once nothing reads them (note: `Account.currency` is still read in a few places and the SQL Server column is `max_length=3` — see AGENTS.md gotcha).
2. **`RbcMonthlyTransaction`** — not a deprecated *model*, but its data is documented unreliable; it survives only for validation scripts. Long-term candidate for "parse but don't store" or a separate validation store.
3. The old six-table RBC annual hierarchy (RbcAnnualReport, RbcAnnualBalances, …) was already flattened into `RbcAnnual` (migration 0017) — no leftovers found in models.

### Apparently unused / no write path
4. **`DividendSchedule`** — read by one helper in `core/utils/portfolio.py`, but **nothing in the codebase writes it** (no admin, command, view or form). Either delete it or add the missing admin/CRUD; ask tdw whether the table has hand-entered data in production.
5. **`AlertConfig`/`AlertHistory`** — the alert pipeline runs only via the `evaluate_alerts` command; `services/alerts.py` (list/acknowledge) has no caller outside tests and there is no alerts UI or email send path found. Confirm whether alerting is actually switched on.
6. **`LedgerMonthClosure` / `MonthlyLedgerBalance.is_closed`** — no close UI exists (AGENTS.md confirms); closure is command-only. Not unused, but half-wired.

### Duplicate / overlapping models
7. **Monthly statements, twice:** `CoreMonthlyStatement` (generated from CSV — the master) vs `RbcMonthly` + `RbcMonthlyBalance` (parsed from PDF — scaffolding/validation). Intentional, but the overlap should be made explicit in docs; the same applies to **`CoreAnnualReport` vs `RbcAnnual`**.
8. **Transactions, twice:** `Transaction` (CSV master) vs `RbcMonthlyTransaction` (PDF, unreliable). Same intent, very different trust levels.
9. **Holdings snapshots, twice:** `PositionHistory` (Holdings CSV → PositionHistory) vs `RbcMonthlyHolding` (PDF Asset Review). Reconciliation code (`reconcile_holdings*`) exists precisely to bridge them.
10. **Price history, twice, in two apps:** `core.HistoricalPrice` (close only, portfolio charts) vs `risk.MarketPriceHistory` (OHLCV, risk engine). Two fetch pipelines (yfinance in both) with no shared source — a consolidation candidate.
11. **Audit mirrors in risk:** `AuditScoreSnapshot`≈`UrgencyScore`, `AuditRecommendationLog`≈`StrategyRecommendation`, `AuditSimulationRun`≈`SimulationRun`/`BacktestRun`, `AuditRegimeLog`≈`RegimeState`+`MarketCondition`. This is deliberate (append-only audit trail vs mutable working tables), but note `AuditSimulationRun.source_run_id` is an untyped int, so the mirror can drift from its source.
12. **Denormalized account identity:** `LedgerAccount.account_id_str/account_name` and the string `account_id` columns in risk models (`UrgencyScore`, `StrategyRecommendation`, `BacktestRun`, `AlertConfig`, audit tables) duplicate `Account.account_id` instead of FK-ing to it — renames in `core_account` will silently drift.

### Personal-data flags (flag only — separate audit covers handling)
- Real account numbers possible in: `core_account.account_id`, `core_rbcmonthly.account_number`, `core_rbcannual.account_number`, `core_ledger_account.account_id_str`, plus string copies in risk tables (`rm_urgency_score.account_id`, `rm_strategy_recommendation.account_id`, `rm_backtest_run.account_id`, `rm_alert_config.account_id`, `rm_audit_recommendation_log.account_id`).
- Owner/person names: `core_account.name/owner`, `core_position_history.owner`, `rm_alternative_portfolios.owner`, `rm_owner_config` (owner + tax rates), `rm_strategy_recommendation.owner`, `rm_backtest_run.owner`, `rm_audit_*` owner/entity fields, `rm_tax_decision_acknowledgement.acknowledged_by/presented_to_user`.
- Contact: `rm_alert_config.notify_email`.
- Free text that may embed account info: `core_transaction.notes`, `core_rbcmonthlytransaction.description`, `core_brokerstatement.raw_filename`, `core_fileimportlog.filename`.

### Migration counts
- `core`: 39 migrations (latest `0039_alter_ledgeraccount_account_id_str_and_more`).
- `risk`: 16 migrations (latest `0016_alter_alertconfig_symbol_and_more`).
- `imports`: no migrations (no models).

### Questions for tdw
1. `DividendSchedule` — is there hand-entered production data, or can the model (and its reader in `portfolio.py`) be removed?
2. Is the alert pipeline (`evaluate_alerts`, AlertConfig/AlertHistory) actually scheduled/used? There is no UI or email path.
3. Are `Account.currency` / `starting_balance` / `starting_balance_date` still read anywhere in tdw's own scripts/BI, or can a deprecation step be planned?
4. Do `core_historicalprice` and `rm_market_price_history` need to stay separate, or should risk prices feed portfolio charts?
5. `LedgerMonthClosure` — is a month-close UI planned (the model is ready but nothing in the UI sets `is_closed`)?
6. `rm_audit_*` retention: `audit_retention_check` flags records but ImmutableModel blocks deletes — what is the intended retention action?
