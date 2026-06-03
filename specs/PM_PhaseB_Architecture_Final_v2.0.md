# Portfolio Manager — Risk Management Module (Phase B)
## Final Architecture & Requirements Overview

| Field | Detail |
|---|---|
| Document Type | Final High-Level Architecture / Statement of Needs |
| Project | Portfolio Manager — Phase B |
| Platform | Django 5.1 + Python 3.12 · CrewAI · saturn dev / mercury production |
| Database | MSSQL 2025 dev (PortfolioDEV) / MSSQL 2022 production (TaxPortfolioPRD) |
| Author | tdwdev · Reviewed: Claude Sonnet + Grok |
| Version | 2.0 — May 2026 |
| Status | **Final — Approved for spec authoring** |

---

## 1. Purpose & Scope

This is the authoritative architecture document for Phase B. It supersedes v1.0 (Claude) and v1.3 (Grok) and incorporates confirmed facts from the Phase A database schema, the live portfolio export (May 24, 2026), and review by both AI planning partners.

Phase B adds a complete Risk Management capability to the Portfolio Manager. It is strictly read-only against Phase A tables, with one exception: the `position_history` table is added to Phase A as a Phase A extension (not a risk module table), because it is foundational infrastructure that Phase A itself would eventually need.

---

## 2. Confirmed Portfolio Reality (as of May 24, 2026)

Understanding the actual portfolio is essential to making sensible data model and prioritisation decisions.

### 2.1 Owners and Accounts

| Account (Nickname) | Type | Market Value (CAD) | Unrealised Gain (CAD) | % Portfolio | Notes |
|---|---|---|---|---|---|
| TAXABLE_TOM_SUSAN | Investment Joint (Taxable) | $921,348 | $520,081 | 20.5% | Largest account; highest tax risk |
| TFSA_TOM | TFSA | $549,554 | $135,219 | 12.2% | Tax-free; no realisation cost |
| TFSA_SUSAN | TFSA | $547,699 | $231,867 | 12.2% | Tax-free; no realisation cost |
| RRSP_TOM | RRSP | $530,841 | $77,807 | 11.8% | Sheltered; withdrawal taxed |
| IB_SUSAN | Investment (Taxable) | $345,625 | $277,698 | 7.7% | High UGL:MV ratio — tax-sensitive |
| LIF_TOM | LIF | $333,974 | $37,070 | 7.4% | Locked-in; withdrawal restricted |
| RRIF_SUSAN | RRIF | $321,937 | $93,601 | 7.1% | Mandatory drawdown account |
| IB_JOINT | Investment Joint (Taxable) | $291,066 | $68,360 | 6.5% | Taxable |
| IB_TOM | Investment (Taxable) | $273,638 | $62,596 | 6.1% | Taxable |
| LIF_SUSAN | LIF | $223,983 | $128,433 | 5.0% | Locked-in |
| **TAXABLE_CAR** | **Investment Joint (Taxable)** | **$118,803** | **$13,697** | **2.6%** | **See §2.2** |
| Cash (CAD + USD) | — | ~$47,301 | — | ~1.0% | |
| **Total Portfolio** | | **~$4,505,000** | **~$1,646,000** | | **Two owners: TOM, SUSAN** |

**Owners:** TOM and SUSAN. All accounts are clearly attributable to one owner or are joint.

### 2.2 TAXABLE_CAR — Inclusion Decision

TAXABLE_CAR holds: RBF2010 (cash-equivalent mutual fund, $51K), XEQT ($10.6K, +$1.1K gain), XDIV ($45K, +$12.6K gain), ICSH ($11.6K, minor loss), RBF2014 (cash equiv, $262).

**Decision: Include TAXABLE_CAR in risk calculations, but with a RESERVE flag.**

Rationale:
- It is a taxable account and its XDIV/XEQT holdings do carry real market risk and real unrealised gains that are subject to capital gains tax on disposition.
- At 2.6% of the portfolio it is not large, but ignoring taxable accounts based on their purpose is architecturally wrong — the risk system doesn't care why you hold something.
- Implement a boolean `exclude_from_risk` flag on `core_account` (added via migration or direct SQL) rather than hard-coding the account name. Default = False for all accounts. Set to True for TAXABLE_CAR initially. This lets the user toggle it via admin without code changes, and means it can easily be included in future full-portfolio views.
- All views, dashboards, and urgency scores respect this flag by default; a "Show all accounts" toggle in the UI includes excluded accounts.

### 2.3 Holdings — Key Risk Observations

**25 unique symbols** across 11 accounts. The portfolio is heavily concentrated:

| Symbol | MV (CAD) | UGL (CAD) | % Portfolio | Risk Notes |
|---|---|---|---|---|
| VDY | $919,759 | $335,529 | 20.4% | Single largest position; Canadian dividend ETF; significant sector concentration |
| VFV | $609,873 | $404,150 | 13.5% | S&P 500 ETF; $322K UGL in TAXABLE_TOM_SUSAN alone — highest tax exposure in portfolio |
| QQQ | $521,263 | $208,403 | 11.6% | Nasdaq-100; tech concentration; held across 3 accounts |
| CGDV | $323,598 | $31,543 | 7.2% | Capital Group dividend; spread across 4 accounts |
| ADX | $301,493 | $10,743 | 6.7% | Adams Diversified Equity Fund (closed-end) — liquidity risk |
| AAPL | $285,763 | $270,972 | 6.3% | Single stock; IB_SUSAN; $196K UGL — extremely high UGL ratio |
| IGM | $254,435 | $37,613 | 5.6% | iShares Growth ETF; spread across 3 accounts |
| MA | $206,560 | $41,280 | 4.6% | Single stock; IB_JOINT |
| SPY/VOO | $382,068 | $95,753 | 8.5% | Effectively the same index (S&P 500); combined with VFV = ~22% S&P 500 exposure |

**Critical observations for architecture:**
- VFV + SPY + VOO = effectively ~22% S&P 500; this overlap is invisible unless cross-security correlation and underlying index exposure are tracked.
- AAPL in IB_SUSAN: $196,200 unrealised gain on $10,709 book cost — a ~1,832% gain. Any strategy recommending a hedge or sale triggers a massive taxable event. The system must surface this explicitly.
- IBIT + ETHA (Bitcoin/Ethereum ETFs in TFSAs) — crypto-class assets; high volatility, all in tax-free accounts which is appropriate.
- ADX is a closed-end fund — lower liquidity than typical ETFs; the system must account for this.
- The portfolio has meaningful crypto (IBIT, ETHA), tech concentration (QQQ, AAPL, NVDA, IGM, SOXX, SMH), and dividend income focus (VDY, CGDV, XDIV) all simultaneously.

### 2.4 Phase A Schema — Confirmed Tables

| Table | Key Columns | Notes |
|---|---|---|
| `core_account` | account_id, name, broker, currency, category, owner | No `exclude_from_risk` flag yet — add via migration |
| `core_transaction` | date, symbol, type, quantity, price, commission, amount, currency, account_id, amount_cad, exchange_rate_cad | Transaction history is the only source of positions — no position table exists |
| `core_historicalprice` | symbol, date, close | **Only `close` price — no OHLCV.** M0 must provide full OHLCV separately |
| `core_brokerstatement` | source, file_date, raw_filename | Import tracking |
| `core_fileimportlog` | filename, broker, imported_at, records_imported | Import log |
| `core_pdfstatement` | file_name, statement_date, account_number, fx_rate, account_id | PDF statement metadata |
| `core_pdfaccountbalance` | account_type, opening/closing/market_value, pdf_statement_id | PDF-sourced balances |
| `core_pdftransaction` | date, activity_type, description, symbol, quantity, price, currency, pdf_statement_id | PDF-sourced transactions |

**Important gap:** `core_historicalprice` stores only `close`. M0 must provide OHLCV, adjusted close, and volume in new `rm_` tables — do not attempt to extend this Phase A table.

**Important gap:** There is no position/holdings table. Current positions must be derived by aggregating `core_transaction` records by symbol+account, netting buys/sells. The `position_history` table added in M0 is the solution.

---

## 3. Design Principles

1. **Strict loose coupling.** Phase B reads Phase A tables and views. It never writes to any `core_` table except adding `exclude_from_risk` to `core_account` (one-time migration, Phase A compatible). All risk tables use the `rm_` prefix in a separate Django app called `risk`.

2. **`exclude_from_risk` flag over hard-coded names.** Never hard-code `TAXABLE_CAR` or any account name in application logic. Use the flag.

3. **Position_history is Phase A infrastructure.** It is added to the `core` app via a Phase A migration because it is a derived fact about portfolio positions, useful to Phase A itself (as-of-date queries, portfolio diff). Risk module reads it as a Phase A consumer.

4. **Currency: USD primary, CAD stored.** All risk metrics computed in USD. CAD-denominated holdings converted using daily FX rates (USD/CAD) stored in `rm_economic_indicators`. Original currency always retained in storage.

5. **Account-type-aware throughout.** TFSA and RRSP/RRIF/LIF accounts have no realisation tax cost. Taxable accounts do. This is a first-class input to every calculation, not a filter at the end.

6. **Two owners, two views.** All metrics available per security, per account, per owner (TOM, SUSAN), and whole portfolio. Owner dimension must be in the data model from the start.

7. **Data source priority:** Amibroker/AmiQuote (primary for OHLCV and technical data) → yFinance (fallback and for data Amibroker doesn't cover, e.g. economic indicators, options chains). The data pipeline must be source-agnostic at the consumer level.

8. **Alternative portfolios are account-scoped only.** User-defined watchlists for what-if analysis are per-account. No cross-account alternative portfolios. No version history on alternatives.

9. **Configurable, not hard-coded.** Scoring weights, alert thresholds, and regime parameters are stored in the database. No constants in Python that users need code changes to adjust.

10. **~30 securities, two owners.** Performance targets are sized accordingly. Daily full recalculation should complete in under 5 minutes on saturn.

---

## 4. Full Module Architecture

| Module | Name | Priority | Est. Specs | Key Dependency |
|---|---|---|---|---|
| M0 | Data Foundation & Market Data Pipeline | P1 | 2 | Phase A schema confirmed |
| M1 | Risk Quantification Engine | P1 | 2 | M0 stable |
| M2 | Risk Assessment & Urgency Scoring | P1 | 2 | M0 + M1 stable |
| M3 | Instrument & Strategy Catalogue | P2 | 1 | M1, M2 |
| M4 | Strategy Optimisation Engine | P2 | 1–2 | M1, M2, M3 |
| M5 | Simulation, Backtesting & Monte Carlo | P2 | 2 | M0, M1, M3, M4 |
| M6 | Reporting & Presentation Layer | P2 | 2 | M1, M2, M4, M5 |
| M7 | Alerting & Continuous Monitoring | P3 | 1 | M1, M2 |
| M8 | Audit Trail & Explainability | P3 | 1 | All modules |

*P1 = foundational / build first. P2 = core capability. P3 = operational maturity.*

---

### M0 — Data Foundation & Market Data Pipeline

**Priority:** P1 | **Estimated specs:** 2

**M0 has two distinct parts that must be done in order.**

**Part A (M0-Spec-1): Data Models.** All new database tables and views. No fetching logic. Gate: tables exist, migrations applied, views work, seed data loaded.

**Part B (M0-Spec-2): Data Pipeline.** The fetch, transform, and refresh logic. Gate: data flows, gaps are detected and filled, schedule works.

**Phase A extension — `position_history` table (added to `core` app):**
- Derived from `core_transaction` by replaying all transactions chronologically per account+symbol
- Temporal validity model: `date_from` / `date_to` — open-ended current record has `date_to = NULL`
- Columns: `owner`, `account_id` (FK → core_account), `symbol`, `quantity`, `cost_basis_per_unit`, `total_cost_basis`, `currency`, `date_from`, `date_to`, `as_of_timestamp`
- Rebuilt by a management command (`python manage.py rebuild_position_history`) — not maintained incrementally at first
- Supporting MSSQL views (created via raw SQL migration or manual script):
  - `vw_current_portfolio` — current open positions, excludes accounts where `exclude_from_risk = 1` (default behaviour; override available)
  - `vw_portfolio_as_of(@as_of_date)` — positions as of any date
  - `vw_portfolio_diff(@date1, @date2)` — what changed between two dates

**Phase A account extension:**
- Add `exclude_from_risk BIT NOT NULL DEFAULT 0` to `core_account`
- TAXABLE_CAR seeded with `exclude_from_risk = 1`
- Admin UI allows toggling without code changes

**Risk module data tables (`rm_` prefix, `risk` Django app):**

`rm_security_universe` — master list of all symbols the system knows about:
- symbol, name, type (`held` / `benchmark` / `hedge` / `alternative` / `watchlist`), asset_class, sector, currency, is_active, notes
- Seed: all 25 held symbols + benchmarks (QQQ, SPY, XIU.TO) + hedges (SOXL, SOXS, leveraged QQQ/SPY variants)
- Note: QQQ and SPY appear here as both `held` and `benchmark` — `type` can be a comma-separated set or use a separate flags column

`rm_market_price_history` — full OHLCV (distinct from `core_historicalprice` which has only `close`):
- symbol, date, open, high, low, close, adj_close, volume, currency, data_source (`amibroker`/`yfinance`), fetched_at

`rm_volatility_summary` — precomputed volatility at standard windows:
- symbol, calc_date, lookback_days (10/21/63/252), volatility_annualised, data_source, calc_timestamp

`rm_fx_rates` — daily USD/CAD rates:
- date, usd_cad_rate, data_source, fetched_at

`rm_economic_indicators` — macro data for regime detection:
- date, indicator_name (`yield_curve_10y2y`, `cpi_yoy`, `pmi_composite`, `gdp_growth_qoq`, `vix_close`, `vix_3m`, `unemployment_rate`), value, data_source, fetched_at

`rm_alternative_portfolios` — per-account user-defined watchlists:
- id, owner, account_id (FK → core_account), name, description, is_active, created_at

`rm_alternative_holdings` — securities in an alternative portfolio:
- id, portfolio_id, symbol, target_weight (decimal; all weights for a portfolio must sum to 1.0 — enforced in application logic and tested)

**Data source strategy:**
- Amibroker export files: CSV exports from Amibroker/AmiQuote, imported via management command or watched folder. Source tag = `amibroker`.
- yFinance fallback: called programmatically when Amibroker data is absent or stale. Source tag = `yfinance`.
- Economic indicators: yFinance (VIX, yield spread via ^TNX/^IRX), FRED API if available (CPI, GDP, PMI). Source tag per indicator.
- The pipeline consumer code asks the data layer for a price series — it does not know or care which source provided it.

---

### M1 — Risk Quantification Engine

**Priority:** P1 | **Dependency:** M0 stable | **Estimated specs:** 2

**M1-Spec-1: Security-level metrics** — computed per symbol for each account where it is held, and aggregated to portfolio level for that symbol.

**M1-Spec-2: Account, owner, and portfolio roll-up** — proper correlated aggregation using the correlation matrix.

**Output tables (all `rm_` prefix):**

`rm_security_risk_metrics` — per symbol, per calc_date:
- Annualised historical volatility (10d, 21d, 63d, 252d windows)
- Maximum drawdown over holding period; current drawdown from peak
- VaR parametric 95%, VaR parametric 99%, VaR historical simulation 95%, VaR Cornish-Fisher 95%
- CVaR (Expected Shortfall) at 95%
- Beta vs SPY (USD benchmark); beta vs XIU.TO (CAD benchmark where applicable)
- Sharpe ratio (using cost basis as reference, risk-free rate from SGOV or rm_economic_indicators)
- Sortino ratio
- Liquidity score: average 30d daily dollar volume vs. position size (0–100 scale)
- Correlation stored as a matrix in `rm_correlation_matrix` (symbol_a, symbol_b, calc_date, pearson_252d)

`rm_portfolio_risk_metrics` — per owner, per account, per portfolio level, per calc_date:
- Aggregate VaR (Markowitz: uses correlation matrix, not simple sum)
- Portfolio beta, portfolio Sharpe, portfolio Sortino
- Diversification ratio: portfolio vol / weighted avg individual vols
- HHI by sector, by asset class
- Concentration: top holding %, top 3 holdings %
- Tax-adjusted risk flag: if top taxable unrealised gain positions > configurable threshold

**Special metrics for this portfolio (architecture-aware):**
- Index overlap detection: VFV + SPY + VOO + QQQ all have S&P 500 exposure. The system must track underlying index exposure per ETF (seed data) and report effective index concentration.
- Closed-end fund liquidity flag: ADX is a CEF; liquidity scoring must account for this.
- Crypto asset class segregation: IBIT, ETHA — separate asset class with distinct volatility profile.

---

### M2 — Risk Assessment & Urgency Scoring

**Priority:** P1 | **Dependency:** M0 + M1 stable | **Estimated specs:** 2

**M2-Spec-1: Regime detection and market condition detection**

Regime detection (4-quadrant model):
- Input data from `rm_economic_indicators`: yield curve slope (10y-2y), CPI YoY trend, PMI composite, GDP QoQ
- Each indicator scored and mapped to quadrant probabilities
- Output: `rm_regime_state` table — calc_date, quadrant (Q1/Q2/Q3/Q4), confidence_score, indicator_snapshot_json
- Transition detection: flag when regime probability shift exceeds threshold

Market condition per security:
- Trend: price vs 50d SMA, price vs 200d SMA, 50/200 crossover (Golden Cross / Death Cross)
- Momentum: RSI 14-period
- MACD: signal line, histogram
- Bollinger Bands: width, price position
- Volume: OBV trend (requires volume from M0 OHLCV — not available in `core_historicalprice`)
- Market phase: uptrend / correction / bear by standard thresholds
- Output: `rm_market_condition` table — symbol, calc_date, all indicator values, phase classification

**M2-Spec-2: Urgency scoring engine**

Composite scoring inputs:
- M1 metric severity (VaR percentile rank, drawdown, volatility vs historical range)
- Security driver tier (asset class baseline, sector cyclicality, liquidity tier, earnings proximity)
- Regime adjustment (each asset class has a sensitivity multiplier per quadrant, stored in config table)
- Market condition score (from M2-Spec-1)

Output: `rm_urgency_score` table — entity_type (security/account/owner/portfolio), entity_id, calc_date, score_raw, urgency_level (Low/Medium/High/Critical), contributing_factors_json (for M8 explainability)

Weights: stored in `rm_scoring_config` table (config_key, config_value, description) — never hard-coded.

---

### M3 — Instrument & Strategy Catalogue

**Priority:** P2 | **Dependency:** M1, M2 | **Estimated specs:** 1

Reference data only. The catalogue defines what is available; M4 decides what to use.

Instrument types relevant to this portfolio:
- Protective put (on individual stocks: AAPL, NVDA, MA, GOOG, V — all optionable)
- Collar (buy put + sell call — for AAPL position specifically, reduces cost significantly)
- Covered call (income generation on positions where upside is acceptable to cap)
- Trailing stop-loss (appropriate for ETF positions where options aren't needed)
- Inverse ETF (SOXS for SOXX/SMH hedge; PSQ for QQQ; SH for SPY/VOO/VFV)
- Sector rotation (reduce tech concentration, increase defensive)
- Partial liquidation / position size reduction (most important for TAXABLE accounts — must show tax cost)
- Diversification (add uncorrelated position — alternative portfolio feature)
- Cash raising (increase SGOV/RBF2010 allocation)

Tables: `rm_instrument_type`, `rm_strategy_template`, `rm_strategy_instrument_link`

Per instrument: mechanics, when to use, cost structure, tax treatment per account type (TFSA/RRSP-RRIF-LIF/Taxable), account type compatibility, wash sale applicability (US rules for IB accounts).

Per strategy template: tagged with applicable urgency levels, applicable regime quadrants, applicable asset types.

---

### M4 — Strategy Optimisation Engine

**Priority:** P2 | **Dependency:** M1, M2, M3 | **Estimated specs:** 1–2**

Given a target entity and its urgency score, produce a ranked list of actionable strategies.

Key constraints specific to this portfolio:
- AAPL in IB_SUSAN: 1,832% gain. Any sell triggers ~$196K CAD taxable gain. The system must show this as a dominant constraint and rank strategies that do not require sale highest.
- VFV in TAXABLE_TOM_SUSAN: $322K unrealised gain. Same issue. A collar strategy (no sale required) is structurally superior to selling.
- ADX (closed-end fund): options likely unavailable; strategy set is limited to stop-loss, rotation, or size reduction.
- IBIT/ETHA in TFSAs: no tax cost to sell; strategies without tax penalty are all viable.
- LIF/RRIF accounts: withdrawal restrictions mean raising cash has friction beyond tax; system must flag this.

Cost modelling:
- Option premium: Black-Scholes using rm_volatility_summary for vol input; or live price from yFinance options chain (M0 must fetch this)
- Tax cost of realisation: (current price - cost_basis_per_unit) × quantity × applicable_tax_rate_input. Tax rates are user-configured inputs, not calculated by the system.
- Friction: bid-ask spread estimate from rm_security_universe (asset class defaults), commission estimate

Output: `rm_strategy_recommendation` — entity_type, entity_id, calc_date, strategy_template_id, rank, estimated_cost, estimated_var_reduction, estimated_urgency_change, tax_cost_estimate, tax_impact_note, constraints_applied_json

---

### M5 — Simulation, Backtesting & Monte Carlo

**Priority:** P2 | **Dependency:** M0, M1, M3, M4 | **Estimated specs:** 2**

**M5-Spec-1: Backtesting engine**

Apply strategy templates to historical price series from `rm_market_price_history`. Measure: drawdown reduction, cost drag, net P&L vs unhedged. Test across historical regime periods (use rm_regime_state retrospectively). Report hedge effectiveness ratio and breakeven cost.

Output: `rm_backtest_run`, `rm_backtest_result`

**M5-Spec-2: Monte Carlo and stress testing**

Monte Carlo:
- N=10,000 paths per security via GBM; parameters from rm_volatility_summary
- Portfolio-level: correlated paths via Cholesky decomposition on rm_correlation_matrix
- Output percentile distribution: 5th/25th/50th/75th/95th

Stress tests — named scenarios relevant to this portfolio:
- 2022 rate shock: bonds repriced, growth equities (QQQ, AAPL, NVDA) down significantly
- 2020 COVID crash: rapid 34% drawdown
- 2008 GFC: prolonged drawdown, high correlation
- 2024 tech correction: NVDA/SOXX/SMH specific drawdown
- Custom: user-defined return shock per security

Output: `rm_stress_test_run`, `rm_stress_test_result`

---

### M6 — Reporting & Presentation Layer

**Priority:** P2 | **Dependency:** M1, M2, M4, M5 | **Estimated specs:** 2**

**M6-Spec-1: Portfolio and account dashboards**
- Portfolio Risk Dashboard: urgency badge, aggregate VaR, portfolio beta, diversification ratio, regime quadrant indicator, market condition summary
- Account Risk Summary: per-account metrics, tax-sensitivity flag (prominent for TAXABLE accounts), locked-in flag for LIF/RRIF
- Owner summary: TOM view, SUSAN view, joint view
- Risk Heat Map: all securities colour-coded by urgency × portfolio weight

**M6-Spec-2: Security detail, strategy, and comparison views**
- Security Risk Card: urgency badge, all M1 metrics, regime sensitivity, top M4 strategy with cost + tax note
- Strategy Recommendation Report: full M4 output with M5 backtest attached
- Alternative Portfolio Maintenance: create/edit per-account watchlists, weight allocation UI (must sum to 100%)
- Side-by-side comparison: current portfolio vs historical date vs alternative portfolio
- Urgency score history chart: time-series per security or portfolio
- Stress test results view: hedged vs unhedged, per scenario
- Export: PDF and CSV for all reports

---

### M7 — Alerting & Continuous Monitoring

**Priority:** P3 | **Dependency:** M1, M2 | **Estimated specs:** 1**

Scheduled daily recalculation: M0 refresh → M1 → M2. Triggered by Django management command, called from cron on saturn (dev) or mercury (production).

Alert triggers:
- Urgency level change (any direction: upgrade or downgrade)
- Portfolio VaR exceeds user-set threshold (e.g. >5% of portfolio value in USD)
- Regime shift detected (quadrant change with confidence > threshold)
- Death Cross on any held security (50d SMA crosses below 200d SMA)
- Earnings announcement within N days (configurable)
- Single security exceeds portfolio weight threshold (e.g. VDY currently at 20.4%)
- Any taxable unrealised gain moves to a new bracket (e.g. crosses $100K, $250K, $500K)

Alert delivery: in-app Django notification. Email optional. Alert history immutable.

Tables: `rm_alert_config`, `rm_alert_history`

---

### M8 — Audit Trail & Explainability

**Priority:** P3 | **Dependency:** All modules | **Estimated specs:** 1**

Immutable score snapshot: every urgency score stores its full input vector and weights at calculation time.

Recommendation log: every M4 output stores candidates considered, scores, constraints, and selection rationale.

Backtest/Monte Carlo run log: parameters and summary output of every M5 run.

Regime log: detected quadrant and market state at each daily run.

Explainability UI: "Why High risk?" → ranked contributing factors in plain English, with the weight each factor carried.

Tax decision audit: for taxable accounts, any strategy recommendation that would trigger a taxable event requires explicit user acknowledgement before being marked actioned. Acknowledgement recorded with timestamp and user.

Retention: 7 years minimum. No automated purge.

Tables: `rm_audit_score_snapshot`, `rm_audit_recommendation_log`, `rm_audit_simulation_run`, `rm_audit_regime_log`, `rm_tax_decision_acknowledgement`

---

## 5. Dependency Graph & Build Order

| Tier | Module(s) | Gate Condition Before Next Tier |
|---|---|---|
| 1 | M0-Spec-1 | All rm_ tables created, migrations applied, position_history built and verified against manual position check, vw_current_portfolio returns correct current holdings, seed data loaded |
| 2 | M0-Spec-2 | Price history flowing for all 25 held symbols + benchmarks; volatility summaries populated; FX rates current; no data gaps > 5 business days; Amibroker import path operational |
| 3 | M1-Spec-1 | Security-level VaR, beta, volatility, drawdown computable for all held symbols; results spot-checked against manual calculation for at least 3 symbols |
| 4 | M1-Spec-2 | Portfolio-level VaR (correlated), HHI, diversification ratio verified; owner-level roll-up working for both TOM and SUSAN |
| 5 | M2-Spec-1 | Regime quadrant detected and plausible for current macro environment; Death Cross / Golden Cross status verified for VDY and QQQ against known dates |
| 6 | M2-Spec-2 | Urgency scores produced for all securities, all accounts, both owners, and portfolio; scores reviewed and make qualitative sense |
| 7 | M3-Spec-1, M6-Spec-1 | Catalogue seeded; basic dashboard visible with live urgency scores |
| 8 | M4-Spec-1, M5-Spec-1 | Recommendations produced for at least AAPL (high UGL, high risk) and VFV (high UGL, taxable); backtests run for 2022 rate shock scenario |
| 9 | M5-Spec-2, M6-Spec-2 | Monte Carlo running; full reporting suite complete |
| 10 | M7, M8 | Alerting and audit trail operational; system reviewed and approved for production use on mercury |

---

## 6. Cross-Cutting Concerns

**Tax-awareness at every layer.** The three taxable accounts (TAXABLE_TOM_SUSAN, IB_SUSAN, IB_TOM, IB_JOINT, TAXABLE_CAR) together hold over $1.1M in unrealised gains. Every metric, score, and recommendation must know the account type and factor in realisation cost. This is the single most important non-functional requirement.

**Index overlap / effective concentration.** VFV + SPY + VOO + QQQ collectively represent massive S&P 500 and Nasdaq exposure. The system must track underlying index weights per ETF (seed table in M0) and report effective index concentration — not just symbol-level concentration.

**Multi-owner integrity.** TOM and SUSAN each have their own accounts. IB_JOINT and TAXABLE_TOM_SUSAN are joint. The `owner` field on `core_account` drives this. No code should assume a single owner.

**MSSQL dialect.** No `RETURNING`, no `ON CONFLICT`, no PostgreSQL-specific constructs. Use `OUTPUT` clause for MSSQL inserts where needed. Full path for sqlcmd in cron: `/opt/mssql-tools18/bin/sqlcmd` (mercury). Views use MSSQL syntax.

**Django app structure.** New `risk` app within the `portfolio_manager` project. All `rm_` models live in `risk/models.py`. Phase A extension models (`position_history`) live in `core/models.py`.

**Amibroker-first, yFinance fallback.** The data pipeline layer is source-agnostic at the consumer level. A `DataSource` abstraction returns price series regardless of origin. This means M1/M2/M3 code never calls yFinance directly.

**No real data on saturn.** Dev uses synthetic/anonymised test data on PortfolioDEV. Production data (TaxPortfolioPRD, real portfolio) lives only on mercury. Migrations are applied to dev first, reviewed, then applied to mercury.

---

## 7. Spec Authoring Plan — All 14 Specs

| Spec ID | Module | Title | Size | Gate Before Authoring |
|---|---|---|---|---|
| M0-Spec-1 | M0 | Data Models, position_history, Views, Seed Data | Large | Phase A schema confirmed ✓ |
| M0-Spec-2 | M0 | Data Pipeline — Amibroker import, yFinance fallback, FX, Economic Indicators | Large | M0-Spec-1 implemented |
| M1-Spec-1 | M1 | Security-level Risk Metrics | Large | M0-Spec-2 data flowing |
| M1-Spec-2 | M1 | Account, Owner & Portfolio Roll-up, Correlation Matrix | Medium | M1-Spec-1 verified |
| M2-Spec-1 | M2 | Regime Detection (4-Quadrant) + Market Condition Technical Indicators | Large | M0-Spec-2 economic data flowing |
| M2-Spec-2 | M2 | Urgency Scoring Engine + Scoring Config | Medium | M2-Spec-1 stable |
| M3-Spec-1 | M3 | Instrument Catalogue + Strategy Templates + Seed Data | Medium | M2-Spec-2 complete |
| M4-Spec-1 | M4 | Strategy Optimisation — Constraint filtering, Cost modelling, Tax-adjusted ranking | Large | M3-Spec-1 seeded |
| M5-Spec-1 | M5 | Backtesting Engine — Historical application, Regime-split reporting | Large | M4-Spec-1 complete |
| M5-Spec-2 | M5 | Monte Carlo + Stress Testing (2008/2020/2022/custom scenarios) | Large | M5-Spec-1 complete |
| M6-Spec-1 | M6 | Portfolio Dashboard, Account Summary, Owner Views, Risk Heat Map | Large | M2-Spec-2 scores available |
| M6-Spec-2 | M6 | Security Detail, Strategy Report, Alternative Portfolio UI, Comparisons, Export | Large | M4, M5 complete |
| M7-Spec-1 | M7 | Alerting, Scheduled Recalculation, Alert History | Medium | M2-Spec-2 complete |
| M8-Spec-1 | M8 | Audit Trail, Explainability UI, Tax Acknowledgement Workflow | Medium | All other modules complete |

**Total: 14 specs.** Start with M0-Spec-1 immediately.

---

*Portfolio Manager — Phase B Risk Management Module | Final Architecture v2.0 | May 2026 | tdwdev*
