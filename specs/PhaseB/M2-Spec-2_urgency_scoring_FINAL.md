# Feature Spec: M2-Spec-2 — Urgency Scoring Engine

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/risk-m2-scoring

Architecture ref: PM_PhaseB_Architecture_Final_v2.0.md
Depends on: M2-Spec-1 fully implemented — rm_regime_state and rm_market_condition populated and verified; M1-Spec-2 complete — rm_portfolio_risk_metrics populated for all entities

---

## Context

This is the spec that makes all prior work actionable. It reads from every previous table — security metrics, portfolio roll-up, regime state, and market conditions — and distils them into a single urgency score per entity.

The score is a weighted composite of four components:

1. M1 metric severity — how extreme are the VaR, drawdown, and volatility numbers relative to the universe?
2. Security driver tier — what is the baseline risk profile of this asset class and sector?
3. Regime adjustment — does the current macro environment amplify or dampen risk for this security?
4. Market condition score — what are the technicals saying right now?

The output is four urgency levels — Low, Medium, High, Critical — produced for every security in every account, every account, both owners, and the portfolio as a whole.

This is the last P1 spec. After this gate passes, the entire M1–M2 quantitative foundation is operational and M3/M4/M6 can proceed in parallel.

**Expected output after first run (June 2, 2026):**
- AAPL in IB_SUSAN: likely High or Critical — massive UGL exposure, concentration, taxable account
- IBIT/ETHA: likely High — crypto volatility, though TFSAs reduce tax urgency
- VDY: likely Medium — largest position, stable but high concentration
- SGOV/RBF2010: Low — cash equivalents
- QQQ/VFV: Medium to High — tech concentration, potential Death Cross territory
These are expectations for sanity-checking, not hardcoded targets.

**Stack:**
- Python 3.12 / Django 5.1
- MSSQL 2025 (dev: PortfolioDEV)
- Project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. New Database Tables

#### 1.1 rm_urgency_score

One row per entity per calculation date. The primary output of the entire M2 module.

```python
class UrgencyScore(models.Model):
    """
    Urgency score for a risk entity. Produced daily after M1 and M2-Spec-1 complete.
    entity_type='security_in_account': per symbol per account (most granular)
    entity_type='account': rolled-up per account
    entity_type='owner': rolled-up per owner
    entity_type='portfolio': whole portfolio
    """
    entity_type = models.CharField(max_length=20)
    # 'security_in_account' | 'account' | 'owner' | 'portfolio'

    entity_id = models.CharField(max_length=100)
    # security_in_account: '{account_id}::{symbol}' e.g. 'TAXABLE_TOM_SUSAN::VFV'
    # account: core_account.account_id string e.g. 'RRSP_TOM'
    # owner: 'TOM' or 'SUSAN'
    # portfolio: 'ALL'

    symbol = models.CharField(max_length=20, blank=True)
    # Populated for entity_type='security_in_account'; blank for account/owner/portfolio

    account_id = models.CharField(max_length=50, blank=True)
    # Populated for entity_type='security_in_account' and 'account'; blank for owner/portfolio

    calc_date = models.DateField()

    # --- Composite Score ---
    score_raw = models.DecimalField(max_digits=6, decimal_places=2)
    # Weighted composite 0.0–100.0

    urgency_level = models.CharField(max_length=10)
    # 'Low' | 'Medium' | 'High' | 'Critical'
    # Thresholds from rm_scoring_config:
    #   Low: score < threshold_low (default 25)
    #   Medium: threshold_low <= score < threshold_medium (default 50)
    #   High: threshold_medium <= score < threshold_high (default 75)
    #   Critical: score >= threshold_high

    # --- Component Scores (0–100 each, before weighting) ---
    component_m1_score = models.DecimalField(max_digits=6, decimal_places=2, null=True)
    # Severity of M1 metrics: VaR percentile rank + drawdown + volatility rank
    component_driver_score = models.DecimalField(max_digits=6, decimal_places=2, null=True)
    # Security driver tier: asset class baseline + sector cyclicality + liquidity tier
    component_regime_score = models.DecimalField(max_digits=6, decimal_places=2, null=True)
    # Regime adjustment: driver_score * regime_sensitivity_multiplier
    component_market_score = models.DecimalField(max_digits=6, decimal_places=2, null=True)
    # Technical indicator score: RSI extreme + crossover + phase classification

    # --- Dominant Factor ---
    dominant_component = models.CharField(max_length=20, blank=True)
    # Which component contributed most to the final score.
    # 'm1_metrics' | 'security_driver' | 'regime' | 'market_condition'
    # Used by M8 explainability: "This security is High primarily because of [dominant_component]"

    component_scores_json = models.TextField()
    # Full JSON snapshot for M8 audit trail:
    # {
    #   "m1": {"score": 62.0, "weight": 0.35, "inputs": {"var_percentile_rank": 78, "drawdown_rank": 55, "vol_rank": 65}},
    #   "driver": {"score": 55.0, "weight": 0.20, "inputs": {"asset_class_baseline": 60, "sector_cyclicality": 45, "liquidity_tier": 60}},
    #   "regime": {"score": 71.5, "weight": 0.25, "inputs": {"driver_score": 55, "multiplier": 1.30, "quadrant": "Q3"}},
    #   "market": {"score": 48.0, "weight": 0.20, "inputs": {"rsi_score": 40, "crossover_score": 60, "phase_score": 45}}
    # }

    calc_timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_urgency_score'
        unique_together = [('entity_type', 'entity_id', 'calc_date')]
        indexes = [
            models.Index(fields=['calc_date', 'entity_type']),
            models.Index(fields=['entity_id', 'calc_date']),
            models.Index(fields=['urgency_level', 'calc_date']),
            models.Index(fields=['symbol', 'calc_date']),
        ]
```

#### 1.2 rm_security_driver

Reference table: one row per symbol. Records the static risk driver characteristics used in component 2.

```python
class SecurityDriver(models.Model):
    """
    Static risk driver characteristics per symbol.
    Updated by the developer as portfolio changes; not recomputed daily.
    These values represent the inherent riskiness of the instrument itself,
    independent of market conditions.
    """
    symbol = models.CharField(max_length=20, unique=True)

    asset_class_baseline = models.DecimalField(max_digits=5, decimal_places=2)
    # Baseline risk score 0–100 for this asset class. Higher = riskier.
    # Reference baselines:
    #   cash_equiv: 10    mutual_fund: 12    etf_bond: 25
    #   etf_equity: 45    cef: 55            common_stock: 60
    #   crypto_etf: 80

    sector_cyclicality = models.DecimalField(max_digits=5, decimal_places=2)
    # 0–100. Higher = more cyclical = more sensitive to economic regime.
    # Reference:
    #   Utilities/Consumer Staples: 20   Healthcare: 30
    #   Financials/Dividend: 40          Broad Market: 45
    #   Technology: 65                   Semiconductor: 75
    #   Crypto: 85

    liquidity_tier = models.DecimalField(max_digits=5, decimal_places=2)
    # Risk score from liquidity profile. Higher = less liquid = more risky.
    # cash_equiv/mutual_fund: 5    large ETF: 15    mid ETF: 25
    #   CEF (ADX): 45              small-cap stock: 55

    earnings_proximity_flag = models.BooleanField(default=False)
    # True if earnings announcement is within 14 days (updated externally or manually)
    # When True: add earnings_proximity_boost from rm_scoring_config to driver score

    notes = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rm_security_driver'
        ordering = ['symbol']
```

**Seed data for rm_security_driver — all 28 held symbols:**

| Symbol | asset_class_baseline | sector_cyclicality | liquidity_tier | Notes |
|---|---|---|---|---|
| AAPL | 60 | 65 | 15 | Large-cap tech stock |
| ADX | 55 | 40 | 45 | CEF; liquidity risk |
| AIS | 45 | 40 | 25 | Dividend ETF |
| BANK | 45 | 65 | 25 | Canadian banks ETF |
| CAGE | 45 | 50 | 25 | Multi-asset ETF; verify if crypto |
| CGDV | 45 | 40 | 15 | Dividend ETF; large, liquid |
| DRAM | 45 | 75 | 30 | Semiconductor memory ETF |
| ETHA | 80 | 85 | 25 | Crypto ETF; highest risk tier |
| GOOG | 60 | 65 | 15 | Large-cap tech stock |
| IBIT | 80 | 85 | 25 | Crypto ETF; highest risk tier |
| ICSH | 10 | 10 | 5 | Ultra-short bond; near-cash |
| IGM | 45 | 65 | 15 | Tech growth ETF |
| IWY | 45 | 60 | 20 | Large-cap growth ETF |
| MA | 60 | 55 | 15 | Financial services stock |
| NVDA | 60 | 75 | 15 | High-vol semiconductor stock |
| QQQ | 45 | 65 | 5 | Nasdaq-100; most liquid ETF |
| RBF2010 | 12 | 10 | 5 | CAD money market fund |
| RBF2014 | 12 | 10 | 5 | USD money market fund |
| SGOV | 10 | 10 | 5 | T-bill ETF; near risk-free |
| SMH | 45 | 75 | 20 | Semiconductor ETF |
| SOXX | 45 | 75 | 20 | Semiconductor ETF |
| SPY | 45 | 50 | 5 | S&P 500; most liquid ETF |
| V | 60 | 55 | 15 | Financial services stock |
| VDY | 45 | 40 | 15 | Canadian dividend ETF |
| VFV | 45 | 50 | 15 | CAD S&P 500 ETF |
| VOO | 45 | 50 | 5 | USD S&P 500 ETF |
| XDIV | 45 | 40 | 20 | Canadian dividend ETF |
| XEQT | 45 | 50 | 20 | Canadian equity ETF |

---

### 2. Management Command

Create risk/management/commands/calc_urgency.py.

```
python manage.py calc_urgency [--date YYYY-MM-DD] [--entity-type security_in_account|account|owner|portfolio] [--account ACC] [--symbol SYM]
```

**Execution order:**
```
1. Load calc_date
2. Load weights from rm_scoring_config (see §3.1)
3. Load urgency thresholds from rm_scoring_config
4. Load rm_regime_state for calc_date (current regime + confidence)
5. Load rm_market_condition for all held symbols on calc_date
6. Load rm_security_risk_metrics for all held symbols on calc_date
7. Load rm_portfolio_risk_metrics for all entities on calc_date
8. Load rm_security_driver for all held symbols
9. Load rm_regime_sensitivity for current quadrant
10. For each held symbol in each account (entity_type='security_in_account'):
    a. Compute all four component scores (see §3)
    b. Compute weighted composite
    c. Classify urgency level
    d. UPSERT into rm_urgency_score
11. Roll up to account level (entity_type='account'):
    a. Max urgency level of any security in account
    b. Weighted average score using position weights
12. Roll up to owner level (entity_type='owner')
13. Roll up to portfolio level (entity_type='portfolio')
14. Print summary: entity counts, urgency distribution, any symbols skipped
```

---

### 3. Scoring Engine

All functions in risk/services/urgency_engine.py.

#### 3.1 Configuration Loading

```python
def load_scoring_config(calc_date: date) -> dict:
    """
    Load all scoring parameters from rm_scoring_config.
    Returns a dict with keys:

    Weights (must sum to 1.0):
        weight_m1_metrics      (default 0.35)
        weight_security_driver (default 0.20)
        weight_regime          (default 0.25)
        weight_market_condition (default 0.20)

    Thresholds:
        threshold_low    (default 25)
        threshold_medium (default 50)
        threshold_high   (default 75)

    Adjustments:
        earnings_proximity_boost (default 10)
        regime_transition_dampener (default 0.80)
        # When transition_flag=True, multiply regime component score by this factor
        # (reduces conviction in regime-dependent adjustments during transitions)
    """
```

#### 3.2 Component 1 — M1 Metric Severity Score

```python
def score_m1_metrics(
    symbol: str,
    security_metrics: dict,
    all_security_metrics: list[dict]
) -> tuple[float, dict]:
    """
    Score the M1 metric severity for a symbol relative to the universe.

    Three sub-metrics, equally weighted:
    1. VaR percentile rank (0–100):
       Rank this symbol's var_parametric_95 among all held symbols.
       Highest VaR = rank 100 (most risky). Lowest = rank 0.
    2. Drawdown rank (0–100):
       Rank this symbol's current_drawdown_from_252d_peak (most negative = rank 100).
    3. Volatility rank (0–100):
       Rank this symbol's vol_252d among all held symbols.

    final_score = (var_rank + drawdown_rank + vol_rank) / 3

    Returns (score, inputs_dict) where inputs_dict is stored in component_scores_json.

    Special cases:
    - If all three M1 values are NULL (e.g. mutual fund): return 0.0
      (no data = no measured risk from metrics)
    - If one or two values are NULL: average the available ones only
    """
```

#### 3.3 Component 2 — Security Driver Score

```python
def score_security_driver(
    symbol: str,
    driver: dict,
    config: dict
) -> tuple[float, dict]:
    """
    Score the inherent riskiness of this instrument.

    Three sub-scores equally weighted:
    1. asset_class_baseline (already 0–100)
    2. sector_cyclicality (already 0–100)
    3. liquidity_tier (already 0–100)

    base_score = (asset_class_baseline + sector_cyclicality + liquidity_tier) / 3

    If driver.earnings_proximity_flag = True:
        final_score = min(100, base_score + config['earnings_proximity_boost'])
    Else:
        final_score = base_score

    Returns (score, inputs_dict).
    """
```

#### 3.4 Component 3 — Regime Adjustment Score

```python
def score_regime_adjustment(
    symbol: str,
    driver_score: float,
    security_driver: dict,
    regime_state: dict,
    regime_sensitivities: dict[str, float],
    config: dict
) -> tuple[float, dict]:
    """
    Adjust the driver score by the current regime sensitivity.

    1. Look up regime sensitivity multiplier for (asset_class, current_quadrant)
       from rm_regime_sensitivity. Use rm_security_universe.asset_class for this symbol.
    2. adjusted_score = driver_score * multiplier
    3. If regime_state['transition_flag'] = True:
       adjusted_score *= config['regime_transition_dampener']
       (reduce confidence in regime adjustment during transitions)
    4. Clamp to 0–100

    Returns (adjusted_score, inputs_dict).

    Note: this is not an independent component — it is the driver score amplified
    or dampened by the regime. It shares the driver's inputs but applies a regime lens.
    """
```

#### 3.5 Component 4 — Market Condition Score

```python
def score_market_condition(
    symbol: str,
    market_condition: dict | None
) -> tuple[float, dict]:
    """
    Score current technical indicator state. Higher = more technically concerning.

    If market_condition is None (no data for this symbol): return 0.0

    Four sub-scores, equally weighted:

    1. RSI score (0–100):
       rsi < 20:  score = 90  (deeply oversold — reversal risk)
       rsi < 30:  score = 70  (oversold)
       rsi < 45:  score = 40
       rsi < 55:  score = 30  (neutral)
       rsi < 70:  score = 40
       rsi < 80:  score = 70  (overbought)
       rsi >= 80: score = 90  (deeply overbought — pullback risk)
       Note: BOTH extremes are risky in opposite ways.
       If rsi is NULL: sub-score = 30 (neutral assumption)

    2. Crossover score (0–100):
       crossover_type = 'death': score = 80  (bearish signal)
       crossover_type = 'none' and sma_50 < sma_200: score = 55  (below 200d)
       crossover_type = 'none' and sma_50 >= sma_200: score = 25  (above 200d)
       crossover_type = 'golden': score = 20  (bullish, lower risk)

    3. Market phase score (0–100):
       'bear':       80
       'correction': 60
       'overbought': 55
       'oversold':   65  (oversold in bad context can be worse than overbought)
       'uptrend':    20

    4. Bollinger Band score (0–100):
       pct_b > 0.95:  score = 75  (price near upper band — overbought)
       pct_b > 0.80:  score = 55
       pct_b > 0.20:  score = 25  (within bands — normal)
       pct_b > 0.05:  score = 55
       pct_b <= 0.05: score = 75  (price near lower band — oversold/breakdown)
       If pct_b is NULL: sub-score = 25

    final_score = (rsi_score + crossover_score + phase_score + bb_score) / 4

    Returns (score, inputs_dict).
    """
```

#### 3.6 Composite Score Calculation

```python
def compute_urgency_score(
    component_m1: float,
    component_driver: float,
    component_regime: float,
    component_market: float,
    config: dict
) -> tuple[float, str, str]:
    """
    Combine four component scores into a single urgency score.

    score_raw = (
        component_m1     * config['weight_m1_metrics'] +
        component_driver * config['weight_security_driver'] +
        component_regime * config['weight_regime'] +
        component_market * config['weight_market_condition']
    )

    Note: weights must sum to 1.0 (validated at load time).

    urgency_level classification:
        score_raw < threshold_low:    'Low'
        score_raw < threshold_medium: 'Medium'
        score_raw < threshold_high:   'High'
        score_raw >= threshold_high:  'Critical'

    dominant_component: whichever of the four weighted contributions is largest.
    E.g. if regime component * weight is 22 out of total 65: dominant = 'regime'

    Returns (score_raw, urgency_level, dominant_component).
    """
```

#### 3.7 Account-Level Roll-Up

```python
def rollup_account(
    account_id: str,
    security_scores: list[dict],
    position_weights: dict[str, float]
) -> tuple[float, str]:
    """
    Roll up security_in_account scores to account level.

    score_raw = sum(security_score * position_weight) for all securities in account
              (weighted average, not max)
    urgency_level = classify(score_raw) using same thresholds

    Additionally: if any security in account has urgency_level='Critical',
    the account urgency_level is at least 'High' regardless of weighted average.
    (One critical position cannot be averaged away by many low-risk ones.)

    Returns (score_raw, urgency_level).
    """
```

#### 3.8 Owner and Portfolio Roll-Up

```python
def rollup_owner(
    owner: str,
    account_scores: list[dict],
    account_mv_weights: dict[str, float]
) -> tuple[float, str]:
    """
    Roll up account scores to owner level.
    Weighted average by account market value (same MV-weighting as M1-Spec-2).
    Same Critical floor rule: if any account is Critical, owner is at least High.
    """

def rollup_portfolio(
    account_scores: list[dict],
    account_mv_weights: dict[str, float]
) -> tuple[float, str]:
    """
    Roll up all non-excluded account scores to portfolio level.
    Same MV-weighted average. Same Critical floor rule.
    """
```

---

### 4. rm_scoring_config Seed Data

This table was created in M0-Spec-1 with placeholder values. This spec populates it with operational values via a data migration.

```python
SCORING_CONFIG_SEED = [
    # Component weights
    ('weight_m1_metrics',       '0.35', 'Weight of M1 metric severity component'),
    ('weight_security_driver',  '0.20', 'Weight of security driver tier component'),
    ('weight_regime',           '0.25', 'Weight of regime adjustment component'),
    ('weight_market_condition', '0.20', 'Weight of market condition component'),

    # Urgency thresholds
    ('threshold_low',    '25', 'Score below this = Low urgency'),
    ('threshold_medium', '50', 'Score 25-50 = Medium urgency'),
    ('threshold_high',   '75', 'Score 50-75 = High; above = Critical'),

    # Adjustments
    ('earnings_proximity_boost',    '10',   'Score boost when earnings within 14 days'),
    ('regime_transition_dampener',  '0.80', 'Multiplier on regime score during transitions'),

    # Portfolio alerts (used by M7)
    ('var_portfolio_alert_pct',    '5.0',    'Alert if portfolio VaR > this % of portfolio value'),
    ('concentration_alert_pct',    '20.0',   'Alert if any symbol > this % of portfolio'),
    ('taxable_ugl_alert_usd',      '100000', 'Tax risk flag if taxable UGL exceeds this (USD)'),

    # Owner attribution
    ('joint_account_owner_split',  '0.50', 'Each owner gets this fraction of joint account positions'),
]
```

Create management command seed_scoring_config that inserts these rows using MSSQL MERGE (upsert — do not fail if already exists).

---

### 5. Admin Registration

```python
@admin.register(UrgencyScore)
class UrgencyScoreAdmin(admin.ModelAdmin):
    list_display = [
        'entity_type', 'entity_id', 'symbol', 'calc_date',
        'urgency_level', 'score_raw',
        'component_m1_score', 'component_driver_score',
        'component_regime_score', 'component_market_score',
        'dominant_component'
    ]
    list_filter = ['calc_date', 'entity_type', 'urgency_level', 'dominant_component']
    search_fields = ['entity_id', 'symbol']
    ordering = ['-calc_date', '-score_raw']
    readonly_fields = [f.name for f in UrgencyScore._meta.fields]


@admin.register(SecurityDriver)
class SecurityDriverAdmin(admin.ModelAdmin):
    list_display = [
        'symbol', 'asset_class_baseline', 'sector_cyclicality',
        'liquidity_tier', 'earnings_proximity_flag', 'updated_at'
    ]
    list_filter = ['earnings_proximity_flag']
    search_fields = ['symbol']
    ordering = ['symbol']
    # Editable — developer updates these as portfolio changes
```

---

### 6. Tests

File: risk/tests/test_urgency_scoring.py

```python
import json
from decimal import Decimal
from django.test import TestCase
from risk.services.urgency_engine import (
    score_m1_metrics, score_security_driver,
    score_regime_adjustment, score_market_condition,
    compute_urgency_score, rollup_account
)


class M1ComponentTests(TestCase):

    def test_highest_var_symbol_scores_100(self):
        # Symbol with highest VaR in universe gets rank 100
        all_metrics = [
            {'symbol': 'A', 'var_parametric_95': 0.05, 'current_drawdown_from_252d_peak': -0.10, 'vol_252d': 0.30},
            {'symbol': 'B', 'var_parametric_95': 0.02, 'current_drawdown_from_252d_peak': -0.02, 'vol_252d': 0.10},
        ]
        score_a, _ = score_m1_metrics('A', all_metrics[0], all_metrics)
        score_b, _ = score_m1_metrics('B', all_metrics[1], all_metrics)
        self.assertGreater(score_a, score_b)
        self.assertAlmostEqual(score_a, 100.0, places=1)
        self.assertAlmostEqual(score_b, 0.0, places=1)

    def test_null_metrics_return_zero(self):
        # Mutual fund: all M1 metrics are NULL
        metrics = {'var_parametric_95': None, 'current_drawdown_from_252d_peak': None, 'vol_252d': None}
        score, _ = score_m1_metrics('RBF2010', metrics, [metrics])
        self.assertEqual(score, 0.0)

    def test_partial_null_averages_available(self):
        # Only vol_252d available; var and drawdown NULL
        metrics = {'var_parametric_95': None, 'current_drawdown_from_252d_peak': None, 'vol_252d': 0.50}
        all_metrics = [
            metrics,
            {'var_parametric_95': None, 'current_drawdown_from_252d_peak': None, 'vol_252d': 0.10},
        ]
        score, _ = score_m1_metrics('IBIT', metrics, all_metrics)
        # Only vol_rank is available; IBIT has higher vol = rank 100
        self.assertAlmostEqual(score, 100.0, places=1)


class DriverComponentTests(TestCase):

    def test_crypto_scores_higher_than_cash_equiv(self):
        crypto_driver = {'asset_class_baseline': 80, 'sector_cyclicality': 85, 'liquidity_tier': 25, 'earnings_proximity_flag': False}
        cash_driver   = {'asset_class_baseline': 10, 'sector_cyclicality': 10, 'liquidity_tier': 5,  'earnings_proximity_flag': False}
        config = {'earnings_proximity_boost': 10}
        score_crypto, _ = score_security_driver('IBIT', crypto_driver, config)
        score_cash, _   = score_security_driver('SGOV', cash_driver, config)
        self.assertGreater(score_crypto, score_cash)

    def test_earnings_proximity_adds_boost(self):
        driver_no_earnings  = {'asset_class_baseline': 60, 'sector_cyclicality': 65, 'liquidity_tier': 15, 'earnings_proximity_flag': False}
        driver_with_earnings = {'asset_class_baseline': 60, 'sector_cyclicality': 65, 'liquidity_tier': 15, 'earnings_proximity_flag': True}
        config = {'earnings_proximity_boost': 10}
        score_no, _   = score_security_driver('AAPL', driver_no_earnings, config)
        score_yes, _  = score_security_driver('AAPL', driver_with_earnings, config)
        self.assertAlmostEqual(score_yes, min(100, score_no + 10), places=1)

    def test_earnings_boost_does_not_exceed_100(self):
        driver = {'asset_class_baseline': 95, 'sector_cyclicality': 95, 'liquidity_tier': 95, 'earnings_proximity_flag': True}
        config = {'earnings_proximity_boost': 10}
        score, _ = score_security_driver('EXTREME', driver, config)
        self.assertLessEqual(score, 100.0)


class RegimeComponentTests(TestCase):

    def test_q3_amplifies_equity_risk(self):
        # In Q3 (recession), equity risk is amplified (multiplier > 1.0)
        driver_score = 50.0
        regime = {'quadrant': 'Q3', 'transition_flag': False}
        sensitivities = {'etf_equity': 1.20}
        driver = {'asset_class': 'etf_equity'}
        config = {'regime_transition_dampener': 0.80}
        score, _ = score_regime_adjustment('VFV', driver_score, driver, regime, sensitivities, config)
        self.assertGreater(score, driver_score)
        self.assertAlmostEqual(score, min(100, driver_score * 1.20), places=1)

    def test_transition_flag_dampens_regime_score(self):
        driver_score = 50.0
        regime_stable     = {'quadrant': 'Q3', 'transition_flag': False}
        regime_transition = {'quadrant': 'Q3', 'transition_flag': True}
        sensitivities = {'etf_equity': 1.20}
        driver = {'asset_class': 'etf_equity'}
        config = {'regime_transition_dampener': 0.80}
        score_stable, _     = score_regime_adjustment('VFV', driver_score, driver, regime_stable, sensitivities, config)
        score_transition, _ = score_regime_adjustment('VFV', driver_score, driver, regime_transition, sensitivities, config)
        self.assertLess(score_transition, score_stable)

    def test_q1_reduces_cash_risk(self):
        # Cash is even safer in Q1 (multiplier < 1.0)
        driver_score = 10.0
        regime = {'quadrant': 'Q1', 'transition_flag': False}
        sensitivities = {'cash_equiv': 0.60}
        driver = {'asset_class': 'cash_equiv'}
        config = {'regime_transition_dampener': 0.80}
        score, _ = score_regime_adjustment('SGOV', driver_score, driver, regime, sensitivities, config)
        self.assertLess(score, driver_score)


class MarketConditionComponentTests(TestCase):

    def test_death_cross_raises_score(self):
        mc_death   = {'rsi_14': 45, 'crossover_type': 'death', 'sma_50': 90, 'sma_200': 100,
                      'market_phase': 'correction', 'bb_pct_b': 0.30}
        mc_golden  = {'rsi_14': 55, 'crossover_type': 'golden', 'sma_50': 110, 'sma_200': 100,
                      'market_phase': 'uptrend', 'bb_pct_b': 0.60}
        score_death, _  = score_market_condition('VFV', mc_death)
        score_golden, _ = score_market_condition('VFV', mc_golden)
        self.assertGreater(score_death, score_golden)

    def test_none_market_condition_returns_zero(self):
        score, _ = score_market_condition('RBF2010', None)
        self.assertEqual(score, 0.0)

    def test_bear_phase_scores_high(self):
        mc = {'rsi_14': 28, 'crossover_type': 'death', 'sma_50': 70, 'sma_200': 100,
              'market_phase': 'bear', 'bb_pct_b': 0.05}
        score, _ = score_market_condition('SYM', mc)
        self.assertGreater(score, 60)

    def test_uptrend_golden_cross_scores_low(self):
        mc = {'rsi_14': 58, 'crossover_type': 'golden', 'sma_50': 110, 'sma_200': 100,
              'market_phase': 'uptrend', 'bb_pct_b': 0.55}
        score, _ = score_market_condition('SYM', mc)
        self.assertLess(score, 35)


class CompositeScoreTests(TestCase):

    def test_thresholds_classify_correctly(self):
        config = {'weight_m1_metrics': 0.35, 'weight_security_driver': 0.20,
                  'weight_regime': 0.25, 'weight_market_condition': 0.20,
                  'threshold_low': 25, 'threshold_medium': 50, 'threshold_high': 75}
        score, level, _ = compute_urgency_score(10, 10, 10, 10, config)
        self.assertEqual(level, 'Low')
        score, level, _ = compute_urgency_score(40, 40, 40, 40, config)
        self.assertEqual(level, 'Medium')
        score, level, _ = compute_urgency_score(65, 65, 65, 65, config)
        self.assertEqual(level, 'High')
        score, level, _ = compute_urgency_score(90, 90, 90, 90, config)
        self.assertEqual(level, 'Critical')

    def test_weights_sum_to_one(self):
        config = {'weight_m1_metrics': 0.35, 'weight_security_driver': 0.20,
                  'weight_regime': 0.25, 'weight_market_condition': 0.20,
                  'threshold_low': 25, 'threshold_medium': 50, 'threshold_high': 75}
        total = (config['weight_m1_metrics'] + config['weight_security_driver'] +
                 config['weight_regime'] + config['weight_market_condition'])
        self.assertAlmostEqual(total, 1.0, places=4)

    def test_dominant_component_identified(self):
        config = {'weight_m1_metrics': 0.35, 'weight_security_driver': 0.20,
                  'weight_regime': 0.25, 'weight_market_condition': 0.20,
                  'threshold_low': 25, 'threshold_medium': 50, 'threshold_high': 75}
        # M1 component at 90 with weight 0.35 = 31.5 contribution — dominant
        score, level, dominant = compute_urgency_score(90, 30, 30, 30, config)
        self.assertEqual(dominant, 'm1_metrics')


class AccountRollupTests(TestCase):

    def test_critical_security_floors_account_at_high(self):
        security_scores = [
            {'symbol': 'AAPL', 'score_raw': 85, 'urgency_level': 'Critical'},
            {'symbol': 'SGOV', 'score_raw': 8,  'urgency_level': 'Low'},
        ]
        weights = {'AAPL': 0.90, 'SGOV': 0.10}
        score, level = rollup_account('IB_SUSAN', security_scores, weights)
        # Weighted avg: 85*0.90 + 8*0.10 = 77.3 (would be High anyway here)
        # Even if average were lower, Critical floor ensures at least High
        self.assertIn(level, ['High', 'Critical'])

    def test_all_low_gives_low_account(self):
        security_scores = [
            {'symbol': 'SGOV', 'score_raw': 8,  'urgency_level': 'Low'},
            {'symbol': 'RBF2010', 'score_raw': 5, 'urgency_level': 'Low'},
        ]
        weights = {'SGOV': 0.70, 'RBF2010': 0.30}
        score, level = rollup_account('TAXABLE_CAR', security_scores, weights)
        self.assertEqual(level, 'Low')


class IntegrationTests(TestCase):

    def test_full_urgency_run_produces_all_entity_types(self):
        # Seed M1, M2-Spec-1, and M1-Spec-2 output for a minimal portfolio
        # Run calc_urgency --date 2026-06-02
        # Assert rm_urgency_score has rows for entity_type in
        #   ['security_in_account', 'account', 'owner', 'portfolio']
        pass

    def test_cash_equiv_scores_low(self):
        # After full run: SGOV and RBF2010 urgency_level should be 'Low'
        pass

    def test_crypto_etf_scores_high_or_critical(self):
        # After full run: IBIT and ETHA urgency_level should be 'High' or 'Critical'
        pass

    def test_component_scores_json_is_valid_json(self):
        # After full run: assert json.loads(score.component_scores_json) succeeds
        # and contains keys: 'm1', 'driver', 'regime', 'market'
        pass

    def test_excluded_account_not_in_portfolio_rollup(self):
        # TAXABLE_CAR scores should not affect portfolio entity urgency
        # (exclude_from_risk flag respected)
        pass
```

Run with: python manage.py test risk.tests.test_urgency_scoring --settings=portfolio_manager.settings_test

---

## Constraints

- DO NOT implement any dashboard or UI — that is M6
- DO NOT implement alerting — that is M7
- DO NOT push to git (manual step)
- Weights in rm_scoring_config must sum to 1.0 — validate at startup and raise ImproperlyConfigured if not
- All component scores are clamped to 0–100 before weighting
- The Critical floor rule (one Critical security = account is at least High) is enforced in rollup, not in scoring
- Score history is preserved — do not delete old rm_urgency_score rows; UPSERT by (entity_type, entity_id, calc_date)
- PEP 8 throughout; full type hints; docstrings on all public functions

---

## Manual Steps Required (in order)

- [ ] Verify gate: SELECT COUNT(*) FROM rm_regime_state WHERE calc_date='2026-06-02' — expect 1 row
- [ ] Verify gate: SELECT COUNT(*) FROM rm_market_condition WHERE calc_date='2026-06-02' — expect 28 rows
- [ ] Verify gate: SELECT COUNT(*) FROM rm_portfolio_risk_metrics WHERE calc_date='2026-06-02' — expect 14+ rows
- [ ] python manage.py makemigrations risk
- [ ] python manage.py migrate --settings=portfolio_manager.settings_dev
- [ ] python manage.py seed_scoring_config --settings=portfolio_manager.settings_dev
- [ ] python manage.py seed_security_drivers --settings=portfolio_manager.settings_dev
- [ ] python manage.py calc_urgency --date 2026-06-02 --settings=portfolio_manager.settings_dev
- [ ] Verify in SSMS: SELECT entity_type, entity_id, symbol, urgency_level, score_raw, dominant_component FROM rm_urgency_score WHERE calc_date='2026-06-02' ORDER BY entity_type, score_raw DESC
- [ ] Sanity check — scores should match these approximate expectations:
  - AAPL in IB_SUSAN: High or Critical (massive UGL, tech stock, taxable)
  - IBIT/ETHA: High or Critical (crypto)
  - SGOV, RBF2010, RBF2014: Low (cash equivalents)
  - VDY: Medium (stable dividend ETF, but high concentration)
  - Portfolio entity: Medium (well-diversified but concentrated in tech and S&P 500)
- [ ] python manage.py test risk.tests.test_urgency_scoring --settings=portfolio_manager.settings_test
- [ ] git add -A && git commit -m "feat: M2-Spec-2 urgency scoring engine + security drivers"
- [ ] git push origin feature/risk-m2-scoring

---

## Human Approval Required

This is the gate spec for the entire P1 foundation. Before marking complete, review the urgency distribution across all entities. If more than 50% of securities are Critical or more than 50% are Low, the scoring weights likely need adjustment. The goal is a distribution that surfaces genuine concerns without crying wolf on everything.

---

M2-Spec-2 | Portfolio Manager Phase B | June 2026 | tdwdev
