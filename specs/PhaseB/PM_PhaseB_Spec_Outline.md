# Portfolio Manager — Phase B Risk Management
## Complete Spec Outline — All 14 Specs

| Field | Detail |
|---|---|
| Project | Portfolio Manager — Phase B |
| Architecture Ref | PM_PhaseB_Architecture_Final_v2.0.md |
| Version | 1.0 — May 2026 |
| Run command | `python -m src.dev_crew.main <spec_filename.md>` from `C:\AI-DevTeam\dev_crew\` |
| Output location | `C:\AI-DevTeam\projects\portfolio_manager\` |

---

## Spec Execution Order

The 14 specs are strictly ordered. Do not submit a spec until its gate condition is met. Specs at the same tier can be submitted to parallel crew runs if desired.

```
Tier 1:  M0-Spec-1   (data models)
Tier 2:  M0-Spec-2   (data pipeline)
Tier 3:  M1-Spec-1   (security metrics)
Tier 4:  M1-Spec-2   (roll-up + correlation)
Tier 5:  M2-Spec-1   (regime + technical)  ← can run parallel with M1-Spec-2 in T4 if M0-Spec-2 is stable
Tier 6:  M2-Spec-2   (urgency scoring)
Tier 7:  M3-Spec-1   (catalogue)            ← can run parallel with M6-Spec-1
         M6-Spec-1   (dashboard)
Tier 8:  M4-Spec-1   (optimisation)
Tier 9:  M5-Spec-1   (backtesting)          ← can run parallel with M4-Spec-1
Tier 10: M5-Spec-2   (Monte Carlo)          ← can run parallel with M6-Spec-2
         M6-Spec-2   (security/strategy UI)
Tier 11: M7-Spec-1   (alerting)             ← can run parallel with M8-Spec-1
         M8-Spec-1   (audit trail)
```

---

## M0-Spec-1 — Data Models, position_history, Views, Seed Data

**File:** `M0-Spec-1_data_models.md`
**Branch:** `feature/risk-m0-data-models`
**Gate before authoring:** Phase A schema confirmed ✓ (done)
**Gate before next spec:** All tables created, migrations applied, views working, position_history populated, seed data loaded, tests passing

**Scope:**
- Add `exclude_from_risk BIT NOT NULL DEFAULT 0` to `core_account` (Phase A migration)
- Add `position_history` table to `core` app (Phase A extension)
- Create `rebuild_position_history` management command
- Create `vw_current_portfolio`, `vw_portfolio_as_of`, `vw_portfolio_diff` views via raw SQL migration
- Create all `rm_` tables in `risk` app: `rm_security_universe`, `rm_market_price_history`, `rm_volatility_summary`, `rm_fx_rates`, `rm_economic_indicators`, `rm_alternative_portfolios`, `rm_alternative_holdings`
- Create `rm_scoring_config` table (for M2 weights — defined now, populated in M2-Spec-2)
- Seed `rm_security_universe` with all 25 held symbols + 3 benchmarks (QQQ, SPY, XIU.TO) + hedges (SOXL, SOXS, SPXS, PSQ)
- Set `exclude_from_risk = 1` on TAXABLE_CAR
- Tests: position_history temporal queries, alternative portfolio weight validation, TAXABLE_CAR exclusion, exclude_from_risk flag behaviour
- **DO NOT** implement any fetch logic
- **DO NOT** push to git

**Deliverables:** Django models (core + risk apps), raw SQL migration for views, management command, seed data script, test file

---

## M0-Spec-2 — Data Pipeline

**File:** `M0-Spec-2_data_pipeline.md`
**Branch:** `feature/risk-m0-pipeline`
**Gate before authoring:** M0-Spec-1 implemented and verified
**Gate before next spec:** Price history flowing for all 28 symbols; volatility summaries populated; FX rates current; economic indicators loading; no gaps > 5 business days; Amibroker import operational

**Scope:**
- `DataSource` abstraction class: source-agnostic interface returning price series regardless of Amibroker vs yFinance origin
- Amibroker CSV import: management command to ingest Amibroker/AmiQuote export files from configured watch folder; tag source = `amibroker`
- yFinance fallback: for symbols not covered by Amibroker import or to fill gaps; tag source = `yfinance`
- FX rate fetch: daily USD/CAD from yFinance (`CAD=X` ticker)
- Economic indicator fetch: VIX (`^VIX`), 10y yield (`^TNX`), 2y yield (`^IRX`), yield spread computed; CPI/GDP/PMI from yFinance or FRED API (use yFinance first, document FRED as upgrade path)
- Volatility summary computation: after price load, compute historical vol at 10/21/63/252-day windows for all symbols, store to `rm_volatility_summary`
- Incremental refresh: detect last fetched date per symbol, fetch only missing dates — never full re-fetch if data exists
- Gap detection: log symbols with gaps > 5 business days; surface in admin
- Scheduling: Django management command `python manage.py refresh_market_data` — designed to be called from cron or Windows Task Scheduler on saturn
- Tests: incremental logic (no duplicate rows), gap detection, source fallback behaviour, FX conversion correctness

**Deliverables:** DataSource abstraction, Amibroker importer, yFinance client wrapper, management commands, gap detection, tests

---

## M1-Spec-1 — Security-Level Risk Metrics

**File:** `M1-Spec-1_security_metrics.md`
**Branch:** `feature/risk-m1-security-metrics`
**Gate before authoring:** M0-Spec-2 data flowing and verified
**Gate before next spec:** All metrics computable for all 25 held symbols; spot-checked against manual calculation for VDY, AAPL, QQQ

**Scope:**
- New tables: `rm_security_risk_metrics`, `rm_correlation_matrix`
- Metrics per symbol per calc_date:
  - Historical volatility (annualised): 10d, 21d, 63d, 252d
  - Maximum drawdown (from holding date, from 252d window)
  - Current drawdown from 252d peak
  - VaR parametric 95% and 99% (1-day and 10-day)
  - VaR historical simulation 95% (1-day)
  - VaR Cornish-Fisher 95% (fat-tail adjusted, 1-day)
  - CVaR / Expected Shortfall 95%
  - Beta vs SPY (252d); beta vs XIU.TO (252d, for CAD-denominated securities)
  - Sharpe ratio (cost_basis_per_unit from position_history as reference price; risk-free from rm_economic_indicators SGOV proxy)
  - Sortino ratio
  - Liquidity score: 30d avg daily dollar volume (from rm_market_price_history volume × close) vs position size; 0–100 scale; ADX gets special closed-end fund flag
  - Unrealised gain at risk: for each account holding this symbol, (1-day 95% VaR in $) vs unrealised gain in $ — ratio stored
- Correlation matrix: Pearson 252d rolling, all symbol pairs, stored to `rm_correlation_matrix`
- Index overlap table: `rm_security_index_exposure` — symbol, index_name, weight (seed data for ETFs); used for effective concentration reporting in M1-Spec-2
- Management command: `python manage.py calc_security_metrics [--date YYYY-MM-DD] [--symbol SYM]`
- All USD conversion via `rm_fx_rates`
- Tests: VaR formula correctness (known input → known output), Sharpe formula, correlation matrix symmetry, USD conversion, liquidity score edge cases

**Deliverables:** Models, calculation engine (pure Python/numpy/scipy), management command, tests

---

## M1-Spec-2 — Account, Owner & Portfolio Roll-Up

**File:** `M1-Spec-2_portfolio_rollup.md`
**Branch:** `feature/risk-m1-rollup`
**Gate before authoring:** M1-Spec-1 verified and stable
**Gate before next spec:** Portfolio VaR, owner-level metrics, HHI, diversification ratio all verified; effective index concentration working

**Scope:**
- New table: `rm_portfolio_risk_metrics` — stores metrics at entity_type=account/owner/portfolio level
- Aggregate VaR: Markowitz formula using correlation matrix from `rm_correlation_matrix` and position weights from `vw_current_portfolio`
- Portfolio beta: weighted average of individual betas
- Sharpe/Sortino at portfolio level: portfolio return series constructed from position weights × individual return series
- Diversification ratio: portfolio vol / weighted avg individual vols
- HHI: by symbol, by sector (from rm_security_universe), by asset_class, by index_exposure
- Concentration metrics: largest holding %, top 3 %, top 5 %
- Effective index concentration: aggregate rm_security_index_exposure × portfolio weights → effective exposure to S&P 500, Nasdaq, TSX per entity
- Tax-adjusted risk flag: if top 3 taxable positions by unrealised gain exceed $X (configurable in rm_scoring_config), flag raised
- Owner-level: aggregate across all accounts for TOM, for SUSAN, and combined
- Management command: `python manage.py calc_portfolio_metrics [--date YYYY-MM-DD] [--owner TOM|SUSAN|ALL]`
- Tests: correlated VaR vs uncorrelated sum (correlated must be ≤), diversification ratio > 1 for diversified portfolio, HHI sector calculation, effective index exposure aggregation

**Deliverables:** Models, roll-up calculation engine, management command, tests

---

## M2-Spec-1 — Regime Detection & Market Condition Technical Indicators

**File:** `M2-Spec-1_regime_market_conditions.md`
**Branch:** `feature/risk-m2-regime`
**Gate before authoring:** M0-Spec-2 economic indicators and OHLCV flowing
**Gate before next spec:** Current regime detected and plausible; indicator values verified for VDY and QQQ against known historical dates

**Scope:**
- New tables: `rm_regime_state`, `rm_market_condition`
- Regime detection:
  - Yield curve slope: 10y minus 2y from rm_economic_indicators
  - CPI trend: 3-month rolling direction of CPI YoY
  - PMI composite: above/below 50 trend
  - GDP QoQ: positive/negative trend
  - Scoring: each indicator maps to quadrant scores (Q1–Q4 probabilities); weighted composite; highest probability = current regime
  - Confidence score: gap between highest and second-highest probability
  - Transition flag: if Q1→Q4 shift probability > threshold (configurable)
  - Store: `rm_regime_state` (calc_date, quadrant, confidence, indicator_values_json, transition_flag)
- Market condition per symbol:
  - Require OHLCV from `rm_market_price_history` (not `core_historicalprice`)
  - 50d SMA, 200d SMA; price vs each
  - 50/200 crossover: store cross_type (golden/death/none), cross_date
  - RSI 14-period (Wilder's smoothed)
  - MACD: 12/26 EMA difference, 9-period signal line, histogram
  - Bollinger Bands: 20d SMA ± 2σ; bandwidth; price position (above/within/below)
  - OBV: on-balance volume cumulative
  - Market phase: `uptrend` (price > 200d SMA), `correction` (10–20% below 52w high), `bear` (>20% below 52w high), `oversold` (RSI < 30), `overbought` (RSI > 70)
  - Store: `rm_market_condition` (symbol, calc_date, all indicator columns, phase)
- Management commands: `python manage.py calc_regime [--date]`, `python manage.py calc_market_conditions [--date] [--symbol]`
- Tests: RSI formula on known sequence, SMA crossover detection, Bollinger Band width calculation, regime quadrant assignment for Q1/Q3 test vectors

**Deliverables:** Models, regime engine, technical indicator engine, management commands, tests

---

## M2-Spec-2 — Urgency Scoring Engine

**File:** `M2-Spec-2_urgency_scoring.md`
**Branch:** `feature/risk-m2-scoring`
**Gate before authoring:** M2-Spec-1 stable
**Gate before next spec:** Urgency scores produced for all 25 symbols, all accounts, both owners, portfolio; qualitative review confirms scores make sense

**Scope:**
- New tables: `rm_urgency_score`, `rm_security_driver` (asset class baseline, sector cyclicality, liquidity tier, earnings proximity flag)
- Scoring inputs:
  - M1 metric severity: VaR percentile rank within universe, drawdown vs historical range (0–100 score per metric)
  - Security driver tier: from `rm_security_driver` — asset class baseline (equity/REIT/bond/crypto/CEF each have different baselines), sector cyclicality, liquidity tier, earnings flag
  - Regime adjustment: `rm_regime_sensitivity` table — for each (asset_class, regime_quadrant), a multiplier stored. E.g. growth equity in Q3 (deflation) gets multiplier > 1.0; defensive equity in Q3 gets < 1.0
  - Market condition score: RSI overbought/oversold, Death Cross presence, phase classification → score 0–100
- Composite: weighted sum of four component scores; weights from `rm_scoring_config`
- Urgency thresholds (from `rm_scoring_config`): Low (<25), Medium (25–50), High (50–75), Critical (>75)
- Entity types: `security_in_account` (per symbol per account), `account`, `owner`, `portfolio`
- Output: `rm_urgency_score` — entity_type, entity_id, symbol (nullable), calc_date, score_raw, urgency_level, component_scores_json (for M8)
- Management command: `python manage.py calc_urgency [--date] [--entity-type]`
- Seed: `rm_security_driver` for all 25 symbols; `rm_regime_sensitivity` matrix for all asset classes; `rm_scoring_config` initial weights
- Tests: known input vectors → expected urgency level; weight change propagation; entity roll-up logic

**Deliverables:** Models, scoring engine, management command, seed data, tests

---

## M3-Spec-1 — Instrument & Strategy Catalogue

**File:** `M3-Spec-1_instrument_catalogue.md`
**Branch:** `feature/risk-m3-catalogue`
**Gate before authoring:** M2-Spec-2 complete
**Gate before next spec:** Catalogue seeded; admin interface working; strategy templates tagged and queryable

**Scope:**
- New tables: `rm_instrument_type`, `rm_strategy_template`, `rm_strategy_instrument_link`
- `rm_instrument_type` seed (min 8 types): protective_put, collar, covered_call, trailing_stop, inverse_etf, sector_rotation, partial_liquidation, cash_raise
- Per instrument: mechanics_description, use_cases_json, cost_structure_json, tax_treatment_json (keyed by account_category: TFSA/RRSP/LIF/RRIF/Taxable), account_type_eligible_flags, wash_sale_applicable
- `rm_strategy_template` seed (min 10 templates):
  - High-gain taxable stock: Collar (AAPL, GOOG, MA scenario)
  - High-gain taxable ETF: Partial size reduction with tax modelling (VFV, VDY scenario)
  - Crypto in TFSA: Trailing stop (IBIT, ETHA scenario)
  - Semiconductor concentration: SOXS inverse ETF hedge (SOXX, SMH, NVDA scenario)
  - Nasdaq concentration: PSQ or QQQ put (QQQ, IGM scenario)
  - Bear market/Q3 regime: Defensive rotation to CGDV/SGOV
  - Stagflation/Q4 regime: Cash raise, commodity tilt
  - CEF (ADX): Trailing stop only (no options available)
  - TFSA general: Trailing stop or covered call (no tax penalty)
  - Income-focused (VDY): Covered call for additional yield
- Per template: tagged with urgency_levels[], regime_quadrants[], asset_types[], instrument_type_ids[]
- Django admin: full CRUD on catalogue tables; no custom UI needed (admin is sufficient for M3)
- Tests: template-to-instrument link integrity, account type eligibility filtering, urgency level tag query

**Deliverables:** Models, seed data migration, Django admin registration, tests

---

## M4-Spec-1 — Strategy Optimisation Engine

**File:** `M4-Spec-1_strategy_optimisation.md`
**Branch:** `feature/risk-m4-optimisation`
**Gate before authoring:** M3-Spec-1 seeded and working
**Gate before next spec:** Recommendations produced for AAPL (IB_SUSAN), VFV (TAXABLE_TOM_SUSAN), IBIT (TFSA_TOM); tax cost estimates reviewed

**Scope:**
- New table: `rm_strategy_recommendation`
- Constraint intake: account_category (determines eligible instruments), unrealised_gain_usd (from position_history + rm_market_price_history), holding_period_days (for short/long-term gain classification), user_cost_tolerance_pct (configurable per owner in rm_scoring_config), locked_in_flag (LIF/RRIF)
- Candidate filtering: query rm_strategy_template WHERE urgency_levels contains current urgency AND regime_quadrants contains current regime AND asset_types matches security AND all account_type_eligible_flags pass
- Cost modelling per candidate:
  - Option premium: Black-Scholes using vol from rm_volatility_summary (63d annualised), risk-free from rm_economic_indicators, strike = current price for ATM. If yFinance options chain data available in rm_market_price_history extensions (optional M0-Spec-2 feature), use market price.
  - Friction: bid-ask spread from rm_instrument_type cost_structure_json; commission = configurable in rm_scoring_config
  - Tax cost of realisation: (current_price - cost_basis) × quantity × user_tax_rate_input. Tax rates stored per owner in `rm_owner_config` (new table: owner, marginal_tax_rate_ca, account_tax_rates_json). NOT calculated by system — owner provides rates.
- Risk reduction estimation: protective put reduces VaR by estimated % based on delta (0.5 ATM put assumed); stop-loss reduces to stop level; inverse ETF reduces beta by hedge ratio. Formulas documented in code.
- Ranking: cost_per_var_reduction = total_cost_estimate / estimated_var_reduction_usd; ranked ascending
- Tax penalty: for taxable accounts, if tax_cost_estimate > 0, a tax_penalty_multiplier from rm_scoring_config is added to the cost before ranking. Ensures tax-triggering strategies rank lower unless risk reduction justifies it.
- Output: `rm_strategy_recommendation` — entity_type, entity_id, symbol, account_id, calc_date, strategy_template_id, rank, estimated_cost_usd, estimated_var_reduction_usd, estimated_urgency_delta, tax_cost_estimate_usd, tax_impact_note, constraints_applied_json, is_tax_triggering
- New table: `rm_owner_config` (owner, marginal_tax_rate_ca, cost_tolerance_pct, preferences_json)
- Management command: `python manage.py calc_strategy_recommendations [--date] [--account] [--symbol]`
- Tests: collar ranks above partial liquidation for AAPL-like high-gain taxable position; TFSA strategies never penalised for tax; CEF correctly limited to stop-loss only; cost ranking logic

**Deliverables:** Models, optimisation engine, management command, tests

---

## M5-Spec-1 — Backtesting Engine

**File:** `M5-Spec-1_backtesting.md`
**Branch:** `feature/risk-m5-backtesting`
**Gate before authoring:** M4-Spec-1 complete
**Gate before next spec:** Backtests completed for 2022 rate shock and 2020 COVID crash for VFV and QQQ

**Scope:**
- New tables: `rm_backtest_run`, `rm_backtest_result`, `rm_historical_scenario`
- Backtest engine: apply strategy template to historical price series from rm_market_price_history
- For each backtest run: symbol, account_id, strategy_template_id, start_date, end_date, scenario_name (optional), position_params (quantity, cost_basis from position_history as-of start_date)
- Simulated strategy application: e.g. for trailing stop, compute the stop price each day; trigger exit when breached. For protective put: apply daily option value change (simplified delta model). For covered call: apply premium income and cap gain logic.
- Measure: total_return_hedged %, total_return_unhedged %, max_drawdown_hedged, max_drawdown_unhedged, hedge_cost_total_usd, hedge_effectiveness_ratio (drawdown reduction / hedge cost), breakeven_cost_per_unit
- Regime-split reporting: within the backtest period, identify which regime sub-periods applied (from rm_regime_state historical) and report metrics per sub-period
- Named historical scenarios (seed data in `rm_historical_scenario`): 2022 Rate Shock (Jan–Oct 2022), 2020 COVID Crash (Feb–Apr 2020), 2008 GFC (Sep 2008–Mar 2009), 2024 Tech Correction (custom dates for NVDA/SOXX). Each scenario defines start_date, end_date, description, asset_class_returns_json (benchmark returns for that period).
- Stress test mode: apply scenario benchmark returns to current positions proportionally (no need for full price history if position is newer than scenario)
- Management command: `python manage.py run_backtest --symbol SYM --account ACC --strategy STR --start YYYY-MM-DD --end YYYY-MM-DD [--scenario NAME]`
- Tests: trailing stop trigger logic, hedge effectiveness calculation, scenario application, regime split boundary detection

**Deliverables:** Models, backtest engine, scenario seed data, management command, tests

---

## M5-Spec-2 — Monte Carlo & Stress Testing

**File:** `M5-Spec-2_monte_carlo.md`
**Branch:** `feature/risk-m5-montecarlo`
**Gate before authoring:** M5-Spec-1 complete
**Gate before next spec:** Monte Carlo running for portfolio; correlated paths validated statistically; stress test scenarios return sensible results

**Scope:**
- New tables: `rm_simulation_run`, `rm_simulation_result`
- Monte Carlo per security: GBM paths. Parameters: drift = 252d mean daily return from rm_market_price_history; vol = rm_volatility_summary 252d. N = configurable (default 10,000). Horizon = configurable (default 252 trading days).
- Optional jump-diffusion: for NVDA, IBIT, ETHA where historical return distribution has high kurtosis (detected from skewness/kurtosis of return series). Flag in rm_security_universe `use_jump_diffusion`.
- Portfolio Monte Carlo: correlated paths via Cholesky decomposition on rm_correlation_matrix. Position weights from vw_current_portfolio.
- Output distribution per run: probability of loss > threshold (5%, 10%, 20% of portfolio value), 5th/25th/50th/75th/95th percentile outcomes, Expected Shortfall under simulated paths.
- Strategy application: optionally apply a strategy from rm_strategy_recommendation to the simulated paths; compare outcome distributions hedged vs unhedged.
- Stress testing:
  - Named scenarios from rm_historical_scenario: apply scenario return shock to each held position
  - Portfolio stress result: total loss hedged vs unhedged, per position contribution
  - Custom scenario: user-defined shock per symbol (stored in a custom scenario table)
- Management command: `python manage.py run_montecarlo [--entity portfolio|account|symbol] [--n 10000] [--horizon 252] [--strategy REC_ID]`
- Management command: `python manage.py run_stress_test [--scenario NAME] [--account ACC]`
- Tests: GBM paths have correct mean and std after large N; Cholesky decomposition positive definite check; stress test result consistency; portfolio loss distribution at correct percentiles

**Deliverables:** Models, Monte Carlo engine (numpy), stress test engine, management commands, tests

---

## M6-Spec-1 — Portfolio Dashboard, Account & Owner Views, Risk Heat Map

**File:** `M6-Spec-1_dashboard.md`
**Branch:** `feature/risk-m6-dashboard`
**Gate before authoring:** M2-Spec-2 urgency scores available (M4/M5 not required for basic dashboard)
**Gate before next spec:** Dashboard displays live urgency scores; account views work for all 11 accounts; heat map renders

**Scope:**
- Django views and templates (Bootstrap 5, consistent with Phase A UI):
- **Portfolio Risk Dashboard** (top-level, `risk/dashboard/`):
  - Urgency badge (Low/Medium/High/Critical) for whole portfolio
  - Aggregate VaR (1-day 95%, USD and CAD)
  - Portfolio beta vs SPY
  - Diversification ratio
  - Effective index concentration bar chart: S&P 500 exposure %, Nasdaq %, TSX %
  - Current regime quadrant (badge + description)
  - Market condition summary: count of Death Crosses, RSI extremes
  - Largest unrealised gains (top 5) with tax-sensitive flag
  - exclude_from_risk account toggle (show/hide TAXABLE_CAR)
- **Account Risk Summary** (`risk/account/<account_id>/`):
  - Account-level urgency, VaR, HHI
  - Tax-sensitivity panel (for taxable accounts): total unrealised gain, estimated tax cost to liquidate
  - Locked-in flag panel (for LIF/RRIF): withdrawal restriction note
  - Holdings table: symbol, MV, urgency badge, 1-day VaR, UGL, regime sensitivity
- **Owner Summary** (`risk/owner/<owner>/`):
  - TOM view: aggregates RRSP_TOM, LIF_TOM, IB_TOM, TFSA_TOM, 50% of joint accounts
  - SUSAN view: analogous
  - Side-by-side owner comparison panel
- **Risk Heat Map** (`risk/heatmap/`):
  - Grid: x-axis = symbol, y-axis = account; cell colour = urgency level; cell size proportional to position weight
  - Summary row: portfolio-level urgency per symbol
- All views: respect exclude_from_risk flag; UI toggle to include excluded accounts
- Tests: view returns 200 for all URL patterns; urgency badge renders correct class; exclude flag respected; owner attribution correct

**Deliverables:** Django views, URL config, templates, static assets (chart.js for heat map), tests

---

## M6-Spec-2 — Security Detail, Strategy Reports, Alternative Portfolios, Export

**File:** `M6-Spec-2_security_and_reports.md`
**Branch:** `feature/risk-m6-reports`
**Gate before authoring:** M4 and M5 complete
**Gate before next spec:** All report views rendering; alternative portfolio CRUD working; export producing valid files

**Scope:**
- **Security Risk Card** (`risk/security/<symbol>/`):
  - Urgency badge, all M1 metrics in structured panels
  - Regime sensitivity: how does this security perform in each quadrant?
  - Market condition indicators: RSI gauge, MACD chart, SMA status, Bollinger Band position
  - Top 3 strategy recommendations from M4 with cost/tax/risk-reduction estimates
  - Backtest summary: most recent M5-Spec-1 result for this symbol
  - Urgency history chart (time-series, 90d and 252d)
- **Strategy Recommendation Report** (`risk/strategy/<recommendation_id>/`):
  - Full M4 recommendation detail with all candidates considered
  - Attached M5 backtest result if available
  - "Mark as actioned" button → triggers M8 tax acknowledgement workflow if `is_tax_triggering`
- **Alternative Portfolio Maintenance** (`risk/alternatives/`):
  - List, create, edit, delete alternative portfolios (per account)
  - Weight allocation UI: symbols + target weights, must sum to 100% (client-side validation + server-side)
  - Risk preview: estimated urgency, VaR, HHI for the alternative portfolio (computed on-demand)
- **Side-by-side Comparison** (`risk/compare/`):
  - Compare: current portfolio vs as-of-date portfolio vs alternative portfolio
  - Metrics: VaR, HHI, urgency, effective index concentration, top 5 holdings
- **Urgency History Chart** — also exposed as a component reusable in dashboard and security card
- **Stress Test Results View** (`risk/stress/<run_id>/`): per-scenario hedged vs unhedged tables
- **Export**: PDF (WeasyPrint or reportlab — match existing Phase A approach) and CSV for all report views
- Tests: alternative portfolio weight validation (sum ≠ 100% rejected), side-by-side comparison data consistency, export file is valid PDF/CSV

**Deliverables:** Django views, templates, alternative portfolio CRUD, export logic, tests

---

## M7-Spec-1 — Alerting & Continuous Monitoring

**File:** `M7-Spec-1_alerting.md`
**Branch:** `feature/risk-m7-alerting`
**Gate before authoring:** M2-Spec-2 complete (urgency scores exist)
**Gate before next spec:** At least 3 alert types triggering correctly in dev; alert history recording correctly

**Scope:**
- New tables: `rm_alert_config`, `rm_alert_history`
- `rm_alert_config`: owner (nullable = all), account_id (nullable = all), symbol (nullable = all), alert_type, threshold_value, threshold_operator, is_active, notify_email
- Alert types:
  - `urgency_level_change`: triggers when urgency_level changes (any direction)
  - `var_portfolio_breach`: portfolio VaR > threshold % of portfolio value
  - `regime_shift`: quadrant change with confidence > threshold
  - `death_cross`: 50d SMA crosses below 200d SMA on held symbol
  - `earnings_proximity`: earnings date within N days for held symbol (earnings dates from yFinance via M0-Spec-2 optional extension — if not available, flag as "not configured")
  - `concentration_breach`: single symbol weight > threshold % (VDY at 20.4% — this will fire immediately)
  - `taxable_gain_bracket`: unrealised gain in taxable account crosses $100K / $250K / $500K / custom threshold
- Alert evaluation: Django management command `python manage.py evaluate_alerts [--date]` — compares current state to previous state, fires alerts where conditions met
- `rm_alert_history`: alert_config_id, triggered_at, trigger_value, previous_value, alert_type, entity_description, acknowledged_at, acknowledged_by
- In-app notification: stored in `rm_alert_history`; unacknowledged alerts shown in dashboard header badge
- Email: optional; Django email backend; configurable per alert_config; template for each alert type
- Full recalculation pipeline command: `python manage.py daily_risk_refresh` — calls M0 refresh → M1 calc → M2 calc → alert evaluation in sequence
- Tests: each alert type triggers correctly on known state transition; no duplicate alerts for same event; daily_risk_refresh command executes without error

**Deliverables:** Models, alert evaluation engine, daily pipeline command, email templates, tests

---

## M8-Spec-1 — Audit Trail & Explainability

**File:** `M8-Spec-1_audit_trail.md`
**Branch:** `feature/risk-m8-audit`
**Gate before authoring:** All other modules functionally complete
**Gate before next spec:** N/A (final spec)

**Scope:**
- New tables: `rm_audit_score_snapshot`, `rm_audit_recommendation_log`, `rm_audit_simulation_run`, `rm_audit_regime_log`, `rm_tax_decision_acknowledgement`, `rm_owner_config` (if not already created in M4)
- `rm_audit_score_snapshot`: immutable. Created alongside every `rm_urgency_score` write. Stores: entity_type, entity_id, symbol, calc_date, urgency_level, score_raw, component_scores_json (all four component scores), weights_at_calc_json (snapshot of rm_scoring_config at calc time), input_metrics_json (M1 values used). No UPDATE or DELETE ever on this table — enforced in model's `save()` method.
- `rm_audit_recommendation_log`: immutable. Created alongside every `rm_strategy_recommendation` write. Stores: all candidates evaluated (not just top-ranked), their individual scores, constraints applied, selection rationale, calc_date, input_urgency_level.
- `rm_audit_simulation_run`: parameters and summary of every M5 backtest or Monte Carlo run.
- `rm_audit_regime_log`: detected regime and market conditions at each daily run — serves as historical regime record.
- `rm_tax_decision_acknowledgement`: for any strategy marked `is_tax_triggering`, stores: recommendation_id, presented_to_user (owner), presented_at, acknowledged_at, acknowledged_by (auth_user), tax_cost_estimate_presented, user_notes. Required before recommendation can be marked `actioned`.
- Explainability view (`risk/explain/<urgency_score_id>/`):
  - "Why is this High risk?" → ranked list of contributing factors with their scores and weights
  - Plain English descriptions of each factor (template-driven, not AI-generated)
  - Historical context: "This security has been High for 12 of the last 30 calculated days"
  - Shows which component drove the score most: M1 metric, regime, market condition, or security driver
- Tax acknowledgement workflow:
  - `risk/strategy/<recommendation_id>/acknowledge/` — shows tax cost estimate, asks for confirmation, records to `rm_tax_decision_acknowledgement`
  - Dashboard shows unacknowledged tax-triggering recommendations as a highlighted panel
- Admin: read-only views of all audit tables; export to CSV
- Retention enforcement: management command `python manage.py audit_retention_check` — reports any audit records approaching 7-year mark; no auto-delete
- Tests: audit snapshot written atomically with urgency score; immutability (model.save() raises on UPDATE attempt); tax acknowledgement required before actioning; explainability view returns correct top factor for known input; retention check command runs cleanly

**Deliverables:** Models, explainability view, tax acknowledgement workflow, admin config, retention check command, tests

---

*Portfolio Manager — Phase B Risk Management | Spec Outline v1.0 | May 2026 | tdwdev*
