# Feature Spec: M1-Spec-2 — Account, Owner & Portfolio Roll-Up

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/risk-m1-rollup

Architecture ref: PM_PhaseB_Architecture_Final_v2.0.md
Depends on: M1-Spec-1 fully implemented — rm_security_risk_metrics and rm_correlation_matrix populated for all held symbols, spot-checked and verified

---

## Context

M1-Spec-1 computed risk metrics per security in isolation. This spec aggregates those into four higher-level entities: account, owner (TOM, SUSAN), and whole portfolio. The critical addition over a naive weighted average is the use of the correlation matrix — correlated VaR is always less than the sum of individual VaRs, and the difference quantifies how much diversification actually reduces risk.

This spec also introduces effective index concentration reporting, which is one of the most practically important outputs of the entire risk module. VFV + SPY + VOO together represent ~22% S&P 500 exposure, but symbol-level concentration metrics miss this entirely. Effective concentration surfaces it.

Tables produced by this spec feed M2 (urgency scoring), M4 (strategy optimisation), and M6 (dashboard).

**Portfolio entities requiring roll-up (June 2, 2026):**

| Entity type | Entities | Notes |
|---|---|---|
| account | 11 accounts | Including TAXABLE_CAR (excluded by default via flag) |
| owner | TOM, SUSAN | TOM: RRSP_TOM, LIF_TOM, IB_TOM, TFSA_TOM + 50% of joint accounts |
| owner | SUSAN | RRIF_SUSAN, LIF_SUSAN, IB_SUSAN, TFSA_SUSAN + 50% of joint accounts |
| portfolio | combined | All accounts where exclude_from_risk = 0 |

Joint accounts (TAXABLE_TOM_SUSAN, IB_JOINT) are attributed 50/50 to TOM and SUSAN for owner-level roll-up. This split is configurable via rm_scoring_config.

**Stack:**
- Python 3.12 / Django 5.1
- MSSQL 2025 (dev: PortfolioDEV)
- numpy for matrix operations (Cholesky decomposition)
- Project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. New Database Table

#### 1.1 rm_portfolio_risk_metrics

One row per entity per calculation date. Stores all aggregated risk metrics.

```python
class PortfolioRiskMetrics(models.Model):
    """
    Aggregated risk metrics at account, owner, or portfolio level.
    All monetary values in USD. Computed from rm_security_risk_metrics
    and rm_correlation_matrix using position weights from vw_current_portfolio.
    """
    entity_type = models.CharField(max_length=20)
    # Values: 'account', 'owner', 'portfolio'
    entity_id = models.CharField(max_length=50)
    # account: core_account.account_id string (e.g. 'TAXABLE_TOM_SUSAN')
    # owner: 'TOM' or 'SUSAN'
    # portfolio: 'ALL'
    calc_date = models.DateField()

    # --- Aggregate VaR ---
    var_portfolio_95_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # Markowitz correlated VaR at 95% confidence, 1-day horizon, in USD.
    # = sqrt(w^T * Sigma * w) * z_0.05
    # where Sigma is the covariance matrix built from individual vols and correlations,
    # and w is the vector of position market values in USD.
    var_portfolio_99_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    var_portfolio_95_pct = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # var_portfolio_95_usd / total_market_value_usd — fraction of portfolio at risk
    var_undiversified_95_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # Simple sum of individual VaRs (ignores correlation) — for comparison only
    diversification_benefit_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # var_undiversified_95_usd - var_portfolio_95_usd — dollar value of diversification

    # --- Portfolio Volatility ---
    portfolio_vol_annualised = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # Annualised portfolio volatility = sqrt(w^T * Sigma_annual * w)
    # where Sigma_annual uses 252d annualised vols on the diagonal
    diversification_ratio = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # weighted_avg_individual_vol / portfolio_vol_annualised
    # > 1.0 means diversification is reducing risk. The higher, the better.
    weighted_avg_vol = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # sum(weight_i * vol_252d_i) — naive weighted average, ignoring correlations

    # --- Beta ---
    portfolio_beta_spy = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # Weighted average of individual beta_spy values using market value weights
    portfolio_beta_xiu = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # Weighted average of individual beta_xiu values

    # --- Risk-Adjusted Returns ---
    portfolio_sharpe = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # Portfolio Sharpe: constructed from daily portfolio return series
    # (sum of weight_i * return_i each day over 252-day window), then standard Sharpe formula
    portfolio_sortino = models.DecimalField(max_digits=8, decimal_places=4, null=True)

    # --- Concentration Metrics ---
    top1_weight = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    # Weight of largest single position as fraction of entity total MV
    top3_weight = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    top5_weight = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    top1_symbol = models.CharField(max_length=20, blank=True)
    # Symbol name of largest single position (for display)

    # --- HHI Concentration ---
    hhi_symbol = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # Herfindahl-Hirschman Index by symbol weight. sum(weight_i^2). Range 0–1.
    # 1.0 = single position; ~0.04 = perfectly equal 25-position portfolio
    hhi_sector = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # HHI computed on sector weights (from rm_security_universe.sector)
    hhi_asset_class = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # HHI computed on asset_class weights

    # --- Effective Index Concentration ---
    # Derived from rm_security_index_exposure × position weights
    # E.g. VFV(13.5% of portfolio) × SP500 exposure(1.0) + SPY(3.4%) × 1.0 + VOO(2.9%) × 1.0 = 19.8% effective SP500
    effective_sp500_weight = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    effective_nasdaq100_weight = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    effective_tsx_weight = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    effective_semiconductor_weight = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    effective_technology_weight = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    effective_dividend_weight = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    # effective_DIVIDEND_CA + effective_DIVIDEND_US combined

    # --- Tax Risk Flag ---
    taxable_ugl_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # Total unrealised gain in USD across all taxable accounts in this entity
    # Only populated for entity_type='account' (taxable) and owner/portfolio roll-up
    tax_risk_flag = models.BooleanField(default=False)
    # True if taxable_ugl_usd > threshold from rm_scoring_config ('taxable_ugl_alert_usd')

    # --- Portfolio Size ---
    total_market_value_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    total_market_value_cad = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    position_count = models.IntegerField(default=0)
    # Number of distinct symbol positions in this entity

    # --- Metadata ---
    calc_timestamp = models.DateTimeField(auto_now_add=True)
    positions_used = models.IntegerField(default=0)
    # Positions for which security-level metrics were available
    positions_skipped = models.IntegerField(default=0)
    # Positions where M1 metrics were NULL (insufficient data) — excluded from VaR calc

    class Meta:
        db_table = 'rm_portfolio_risk_metrics'
        unique_together = [('entity_type', 'entity_id', 'calc_date')]
        indexes = [
            models.Index(fields=['calc_date', 'entity_type']),
            models.Index(fields=['entity_id', 'calc_date']),
        ]
```

---

### 2. Management Command

Create risk/management/commands/calc_portfolio_metrics.py.

```
python manage.py calc_portfolio_metrics [--date YYYY-MM-DD] [--entity-type account|owner|portfolio] [--entity-id ID]
```

- --date: calculation date (default: today)
- --entity-type: restrict to one entity type (default: compute all)
- --entity-id: restrict to one specific entity (default: all)

**Execution order within the command:**
```
1. Load calc_date, FX rate from rm_fx_rates
2. Load position weights from vw_current_portfolio (respects exclude_from_risk)
3. Load rm_security_risk_metrics for all symbols on calc_date
4. Load rm_correlation_matrix for all symbol pairs on calc_date
5. Load rm_security_index_exposure for effective concentration
6. For each account:
   a. Build position weight vector for symbols in that account
   b. Run all roll-up calculations (see §3)
   c. UPSERT into rm_portfolio_risk_metrics (entity_type='account')
7. For each owner (TOM, SUSAN):
   a. Aggregate across owned accounts + 50% of joint accounts
   b. UPSERT (entity_type='owner')
8. For portfolio total:
   a. Aggregate all non-excluded accounts
   b. UPSERT (entity_type='portfolio', entity_id='ALL')
9. Print summary: entities computed, any symbols skipped, time elapsed
```

---

### 3. Calculation Engine

All functions in risk/services/portfolio_metrics.py. Pure Python — no ORM calls inside functions. Takes DataFrames/dicts as inputs.

#### 3.1 Position Weight Vector

```python
def build_position_weights(
    positions: list[dict],
    security_metrics: dict[str, dict],
    fx_rate: float
) -> tuple[np.ndarray, list[str], float]:
    """
    Build a weight vector and symbol list for an entity's positions.

    positions: list of dicts with keys symbol, quantity, currency, account_category
               sourced from vw_current_portfolio filtered to this entity
    security_metrics: dict keyed by symbol, values from rm_security_risk_metrics

    Returns:
        weights: np.ndarray of shape (n,) — each weight = position_mv_usd / total_mv_usd
        symbols: list of str in same order as weights
        total_mv_usd: float

    Skips symbols where:
    - var_parametric_95 is NULL in rm_security_risk_metrics (insufficient price history)
    - vol_252d is NULL
    Logs skipped symbols to Django logger.
    """
```

#### 3.2 Covariance Matrix

```python
def build_covariance_matrix(
    symbols: list[str],
    security_metrics: dict[str, dict],
    correlation_matrix: dict[tuple[str, str], float]
) -> np.ndarray:
    """
    Build annualised covariance matrix for a list of symbols.

    Formula: Sigma[i,j] = corr(i,j) * vol_252d[i] * vol_252d[j]
    Diagonal: Sigma[i,i] = vol_252d[i]^2

    If correlation for pair (i,j) is missing (insufficient overlap in M1):
    assume correlation = 0.5 as a conservative estimate and log a warning.
    This prevents a missing pair from making the entire matrix unusable.

    Returns np.ndarray of shape (n, n).
    Raises ValueError if matrix is not positive semi-definite after construction
    (indicates data quality issue — log the symbols involved).
    """
```

#### 3.3 Correlated Portfolio VaR

```python
def portfolio_var_markowitz(
    weights: np.ndarray,
    cov_matrix: np.ndarray,
    total_mv_usd: float,
    confidence: float = 0.95,
    horizon_days: int = 1
) -> tuple[float, float]:
    """
    Markowitz portfolio VaR using correlation-adjusted covariance.

    Formula:
        portfolio_variance = w^T * Sigma * w
        portfolio_vol_daily = sqrt(portfolio_variance) / sqrt(252)
        z = scipy.stats.norm.ppf(1 - confidence)
        VaR_fraction = z * portfolio_vol_daily * sqrt(horizon_days)
        VaR_usd = abs(VaR_fraction) * total_mv_usd

    Returns (var_fraction, var_usd).

    Note: this formula uses annualised vols on the covariance matrix,
    so divides by sqrt(252) to get daily vol, then scales by sqrt(horizon_days).
    """

def portfolio_var_undiversified(
    weights: np.ndarray,
    individual_vars_95: np.ndarray,
    total_mv_usd: float
) -> float:
    """
    Simple weighted sum of individual VaRs — ignores all correlation.
    = sum(weight_i * var_i) * total_mv_usd
    Always >= correlated VaR. Difference = diversification benefit.
    """
```

#### 3.4 Diversification Ratio

```python
def diversification_ratio(
    weights: np.ndarray,
    individual_vols: np.ndarray,
    portfolio_vol: float
) -> float:
    """
    Diversification ratio = weighted_avg_individual_vol / portfolio_vol.

    weighted_avg_individual_vol = sum(weight_i * vol_i)
    portfolio_vol = from portfolio_var_markowitz (annualised)

    Interpretation:
    - ratio = 1.0: no diversification benefit (perfectly correlated)
    - ratio > 1.0: diversification reducing risk (typical for this portfolio)
    - ratio < 1.0: impossible in theory; indicates data inconsistency

    This portfolio should show ratio ~1.15–1.35 given mixed asset classes.
    """
```

#### 3.5 HHI Concentration

```python
def hhi(weights: np.ndarray) -> float:
    """
    Herfindahl-Hirschman Index. sum(w_i^2).
    weights must sum to 1.0.
    Returns float in [0, 1]. Lower = more diversified.
    Example for this portfolio: VDY at 20.7% alone contributes 0.207^2 = 0.043.
    """

def hhi_by_group(
    symbols: list[str],
    weights: np.ndarray,
    group_map: dict[str, str]
) -> float:
    """
    HHI computed on grouped weights.
    group_map: {symbol: group_name} e.g. {VDY: 'Financial', QQQ: 'Technology'}
    First aggregates weights by group, then computes HHI on group weights.
    """
```

#### 3.6 Effective Index Concentration

```python
def effective_index_weights(
    symbols: list[str],
    position_weights: np.ndarray,
    index_exposures: dict[str, dict[str, float]]
) -> dict[str, float]:
    """
    Compute effective portfolio weight in each underlying index.

    index_exposures: {symbol: {index_name: exposure_weight}}
                     sourced from rm_security_index_exposure

    Formula: effective_index_weight[idx] = sum(position_weight[i] * exposure[i][idx])

    Example for SP500:
        VFV: position weight 0.141, SP500 exposure 1.0  -> 0.141
        SPY: position weight 0.034, SP500 exposure 1.0  -> 0.034
        VOO: position weight 0.029, SP500 exposure 1.0  -> 0.029
        ADX: position weight 0.051, SP500 exposure 0.85 -> 0.043
        IWY: position weight 0.027, SP500 exposure 0.85 -> 0.023
        CGDV:position weight 0.058, SP500 exposure 0.70 -> 0.041
        ...
        Total effective SP500 weight ≈ 0.35+ (35%+ effective S&P 500 exposure)

    Returns dict: {index_name: effective_weight_fraction}
    """
```

#### 3.7 Portfolio Sharpe and Sortino

```python
def portfolio_return_series(
    symbols: list[str],
    weights: np.ndarray,
    price_data: dict[str, pd.Series],
    window: int = 252
) -> pd.Series:
    """
    Construct daily portfolio return series for last `window` trading days.

    Method:
    1. For each symbol: load last 252 daily log returns from DataSource
    2. Inner join on date (some symbols may have fewer days)
    3. Portfolio return on day t = sum(weight_i * return_i_t)
       Weights are the current weights (static, not time-varying)
       This is a simplification — we are not rebalancing the weights daily.
    4. Return pd.Series indexed by date.

    Note: uses same weights as current portfolio.
    If a symbol has fewer than 126 days of history in the window,
    substitute its weight proportionally into remaining symbols
    and log a warning.
    """
```

#### 3.8 Tax Risk Flag

```python
def compute_tax_risk_flag(
    entity_type: str,
    entity_id: str,
    positions: list[dict],
    current_prices: dict[str, float],
    fx_rate: float,
    threshold_usd: float
) -> tuple[float, bool]:
    """
    Compute total unrealised gain in USD for taxable positions in this entity.

    Only includes positions in accounts where category in
    ('Investment', 'Investment Joint') — taxable accounts.
    Excludes TFSA, RRSP, RRIF, LIF.

    taxable_ugl_usd = sum over taxable positions:
        (current_price_usd - cost_basis_per_unit_usd) * quantity

    tax_risk_flag = taxable_ugl_usd > threshold_usd

    threshold_usd sourced from rm_scoring_config key 'taxable_ugl_alert_usd'
    Default if not configured: 100000 (USD 100K)

    Returns (taxable_ugl_usd, tax_risk_flag).
    """
```

#### 3.9 Owner Attribution for Joint Accounts

```python
def get_owner_positions(
    owner: str,
    all_positions: list[dict],
    joint_split: float = 0.5
) -> list[dict]:
    """
    Return positions attributed to this owner.

    For accounts where core_account.owner == owner: include 100% of position.
    For joint accounts (owner field is 'JOINT' or similar):
        include joint_split fraction (default 0.5) of each position quantity.
        Adjust quantity and total_cost_basis by the fraction.

    joint_split sourced from rm_scoring_config key 'joint_account_owner_split'.
    Default 0.5. Both TOM and SUSAN get 50% of joint accounts.

    Joint accounts as of June 2 export: TAXABLE_TOM_SUSAN, IB_JOINT.
    """
```

---

### 4. MSSQL UPSERT Pattern

```sql
MERGE rm_portfolio_risk_metrics AS target
USING (VALUES (
    :entity_type, :entity_id, :calc_date,
    :var_portfolio_95_usd, :var_portfolio_99_usd, :var_portfolio_95_pct,
    :var_undiversified_95_usd, :diversification_benefit_usd,
    :portfolio_vol_annualised, :diversification_ratio, :weighted_avg_vol,
    :portfolio_beta_spy, :portfolio_beta_xiu,
    :portfolio_sharpe, :portfolio_sortino,
    :top1_weight, :top3_weight, :top5_weight, :top1_symbol,
    :hhi_symbol, :hhi_sector, :hhi_asset_class,
    :effective_sp500_weight, :effective_nasdaq100_weight, :effective_tsx_weight,
    :effective_semiconductor_weight, :effective_technology_weight, :effective_dividend_weight,
    :taxable_ugl_usd, :tax_risk_flag,
    :total_market_value_usd, :total_market_value_cad,
    :position_count, :positions_used, :positions_skipped
)) AS source (
    entity_type, entity_id, calc_date,
    var_portfolio_95_usd, var_portfolio_99_usd, var_portfolio_95_pct,
    var_undiversified_95_usd, diversification_benefit_usd,
    portfolio_vol_annualised, diversification_ratio, weighted_avg_vol,
    portfolio_beta_spy, portfolio_beta_xiu,
    portfolio_sharpe, portfolio_sortino,
    top1_weight, top3_weight, top5_weight, top1_symbol,
    hhi_symbol, hhi_sector, hhi_asset_class,
    effective_sp500_weight, effective_nasdaq100_weight, effective_tsx_weight,
    effective_semiconductor_weight, effective_technology_weight, effective_dividend_weight,
    taxable_ugl_usd, tax_risk_flag,
    total_market_value_usd, total_market_value_cad,
    position_count, positions_used, positions_skipped
)
ON target.entity_type = source.entity_type
   AND target.entity_id = source.entity_id
   AND target.calc_date = source.calc_date
WHEN MATCHED THEN UPDATE SET
    var_portfolio_95_usd = source.var_portfolio_95_usd,
    -- (all columns)
    calc_timestamp = SYSDATETIMEOFFSET()
WHEN NOT MATCHED THEN INSERT (...) VALUES (...);
```

---

### 5. Admin Registration

```python
@admin.register(PortfolioRiskMetrics)
class PortfolioRiskMetricsAdmin(admin.ModelAdmin):
    list_display = [
        'entity_type', 'entity_id', 'calc_date',
        'var_portfolio_95_usd', 'var_portfolio_95_pct',
        'diversification_ratio', 'hhi_symbol',
        'effective_sp500_weight', 'portfolio_beta_spy',
        'tax_risk_flag', 'total_market_value_usd'
    ]
    list_filter = ['calc_date', 'entity_type', 'tax_risk_flag']
    search_fields = ['entity_id']
    ordering = ['-calc_date', 'entity_type', 'entity_id']
    readonly_fields = [f.name for f in PortfolioRiskMetrics._meta.fields]
```

---

### 6. Tests

File: risk/tests/test_portfolio_metrics.py

```python
import numpy as np
import pandas as pd
from django.test import TestCase
from risk.services.portfolio_metrics import (
    build_covariance_matrix, portfolio_var_markowitz,
    portfolio_var_undiversified, diversification_ratio,
    hhi, hhi_by_group, effective_index_weights
)


class CorrelatedVaRTests(TestCase):

    def test_correlated_var_less_than_undiversified(self):
        # Two assets, partial correlation (rho=0.5)
        # Correlated VaR must be less than sum of individual VaRs
        weights = np.array([0.6, 0.4])
        vols = np.array([0.20, 0.30])
        corr = np.array([[1.0, 0.5], [0.5, 1.0]])
        cov = np.outer(vols, vols) * corr
        total_mv = 1_000_000
        var_corr, var_usd = portfolio_var_markowitz(weights, cov, total_mv)
        var_univ = portfolio_var_undiversified(weights, vols / np.sqrt(252) * 1.645, total_mv)
        self.assertLess(var_usd, var_univ)

    def test_perfectly_correlated_assets_equal_undiversified(self):
        # rho=1.0: no diversification benefit
        weights = np.array([0.5, 0.5])
        vols = np.array([0.20, 0.20])
        corr = np.array([[1.0, 1.0], [1.0, 1.0]])
        cov = np.outer(vols, vols) * corr
        total_mv = 1_000_000
        var_corr_frac, var_usd = portfolio_var_markowitz(weights, cov, total_mv)
        var_univ = portfolio_var_undiversified(weights, vols / np.sqrt(252) * 1.645, total_mv)
        self.assertAlmostEqual(var_usd, var_univ, delta=10)

    def test_single_asset_var_matches_security_var(self):
        # Portfolio with one asset: portfolio VaR should equal individual VaR * MV
        weights = np.array([1.0])
        vol = 0.25
        cov = np.array([[vol**2]])
        total_mv = 500_000
        from scipy.stats import norm
        expected_var = total_mv * (vol / np.sqrt(252)) * norm.ppf(0.95)
        _, var_usd = portfolio_var_markowitz(weights, cov, total_mv)
        self.assertAlmostEqual(var_usd, expected_var, delta=1)

    def test_missing_correlation_uses_conservative_default(self):
        # Symbol pair with no correlation data defaults to 0.5 — not zero
        symbols = ['AAPL', 'NVDA']
        metrics = {
            'AAPL': {'vol_252d': 0.30},
            'NVDA': {'vol_252d': 0.55},
        }
        corr_data = {}  # No correlation data available
        cov = build_covariance_matrix(symbols, metrics, corr_data)
        # Off-diagonal should be 0.5 * 0.30 * 0.55 = 0.0825
        self.assertAlmostEqual(cov[0, 1], 0.5 * 0.30 * 0.55, places=4)


class DiversificationRatioTests(TestCase):

    def test_ratio_greater_than_one_for_imperfect_correlation(self):
        weights = np.array([0.5, 0.5])
        vols = np.array([0.20, 0.30])
        # Portfolio vol with rho=0.3 is less than weighted avg vol
        corr = 0.3
        port_var = (0.5**2 * 0.20**2 + 0.5**2 * 0.30**2 + 2 * 0.5 * 0.5 * corr * 0.20 * 0.30)
        port_vol = np.sqrt(port_var)
        weighted_avg = 0.5 * 0.20 + 0.5 * 0.30
        ratio = diversification_ratio(weights, vols, port_vol)
        self.assertGreater(ratio, 1.0)
        self.assertAlmostEqual(ratio, weighted_avg / port_vol, places=4)

    def test_ratio_equals_one_for_perfect_correlation(self):
        weights = np.array([0.5, 0.5])
        vols = np.array([0.25, 0.25])
        # rho=1.0: portfolio vol = weighted avg vol
        port_vol = 0.25
        ratio = diversification_ratio(weights, vols, port_vol)
        self.assertAlmostEqual(ratio, 1.0, places=4)


class HHITests(TestCase):

    def test_single_position_hhi_is_one(self):
        weights = np.array([1.0])
        self.assertAlmostEqual(hhi(weights), 1.0, places=6)

    def test_equal_weight_25_positions(self):
        weights = np.full(25, 1/25)
        result = hhi(weights)
        self.assertAlmostEqual(result, 1/25, places=4)

    def test_vdy_dominance_raises_hhi(self):
        # VDY at 20.7% of portfolio contributes 0.0428 to HHI alone
        # Remaining 79.3% spread evenly across 27 symbols
        vdy_weight = 0.207
        rest = (1 - vdy_weight) / 27
        weights = np.array([vdy_weight] + [rest] * 27)
        result = hhi(weights)
        self.assertGreater(result, vdy_weight**2)

    def test_hhi_by_sector_groups_correctly(self):
        symbols = ['AAPL', 'NVDA', 'VDY', 'CGDV']
        weights = np.array([0.25, 0.25, 0.25, 0.25])
        group_map = {
            'AAPL': 'Technology',
            'NVDA': 'Technology',
            'VDY': 'Financial',
            'CGDV': 'Financial',
        }
        result = hhi_by_group(symbols, weights, group_map)
        # Two groups each with weight 0.5: HHI = 0.5^2 + 0.5^2 = 0.5
        self.assertAlmostEqual(result, 0.5, places=4)


class EffectiveIndexConcentrationTests(TestCase):

    def test_sp500_concentration_from_overlapping_etfs(self):
        # VFV 14% of portfolio (SP500 exposure 1.0) + SPY 3.4% (1.0) + VOO 2.9% (1.0)
        # + ADX 5.1% (0.85) + IWY 2.7% (0.85) + CGDV 5.8% (0.70)
        # Expected effective SP500: 0.14 + 0.034 + 0.029 + 0.0434 + 0.023 + 0.041 = 0.310
        symbols = ['VFV', 'SPY', 'VOO', 'ADX', 'IWY', 'CGDV']
        weights = np.array([0.14, 0.034, 0.029, 0.051, 0.027, 0.058])
        exposures = {
            'VFV':  {'SP500': 1.00},
            'SPY':  {'SP500': 1.00},
            'VOO':  {'SP500': 1.00},
            'ADX':  {'SP500': 0.85},
            'IWY':  {'SP500': 0.85},
            'CGDV': {'SP500': 0.70},
        }
        result = effective_index_weights(symbols, weights, exposures)
        expected = 0.14 + 0.034 + 0.029 + 0.051*0.85 + 0.027*0.85 + 0.058*0.70
        self.assertAlmostEqual(result['SP500'], expected, places=4)

    def test_symbol_without_exposure_contributes_zero(self):
        # AAPL has no SP500 exposure entry
        symbols = ['AAPL', 'VFV']
        weights = np.array([0.50, 0.50])
        exposures = {'VFV': {'SP500': 1.0}}
        result = effective_index_weights(symbols, weights, exposures)
        self.assertAlmostEqual(result['SP500'], 0.50, places=4)


class OwnerAttributionTests(TestCase):

    def test_joint_account_split_50_50(self):
        from risk.services.portfolio_metrics import get_owner_positions
        positions = [
            {'symbol': 'VDY', 'quantity': 100, 'owner': 'TOM', 'account_id': 'RRSP_TOM'},
            {'symbol': 'VFV', 'quantity': 200, 'owner': 'JOINT', 'account_id': 'TAXABLE_TOM_SUSAN'},
        ]
        tom_positions = get_owner_positions('TOM', positions, joint_split=0.5)
        # TOM gets 100% of RRSP_TOM VDY and 50% of TAXABLE VFV
        vdy_pos = next(p for p in tom_positions if p['symbol'] == 'VDY')
        vfv_pos = next(p for p in tom_positions if p['symbol'] == 'VFV')
        self.assertEqual(vdy_pos['quantity'], 100)
        self.assertAlmostEqual(vfv_pos['quantity'], 100.0, places=2)

    def test_owner_does_not_get_other_owners_accounts(self):
        from risk.services.portfolio_metrics import get_owner_positions
        positions = [
            {'symbol': 'GOOG', 'quantity': 100, 'owner': 'SUSAN', 'account_id': 'TFSA_SUSAN'},
            {'symbol': 'VDY', 'quantity': 100, 'owner': 'TOM',   'account_id': 'RRSP_TOM'},
        ]
        tom_positions = get_owner_positions('TOM', positions)
        symbols = [p['symbol'] for p in tom_positions]
        self.assertIn('VDY', symbols)
        self.assertNotIn('GOOG', symbols)


class IntegrationTests(TestCase):
    """
    End-to-end: seed rm_security_risk_metrics + rm_correlation_matrix,
    run calc_portfolio_metrics, verify rm_portfolio_risk_metrics output.
    """

    def test_portfolio_var_less_than_sum_of_individual_vars(self):
        # Seed metrics for 3 symbols with known vols and correlations
        # Run calc_portfolio_metrics --entity-type portfolio
        # Assert portfolio VaR < undiversified VaR
        pass  # Implement with fixtures

    def test_diversification_ratio_above_one(self):
        # Seed partially correlated portfolio
        # Assert diversification_ratio > 1.0
        pass

    def test_effective_sp500_above_20_pct(self):
        # Seed full VFV + SPY + VOO positions
        # Assert effective_sp500_weight > 0.20 for portfolio entity
        pass

    def test_tax_risk_flag_set_for_taxable_accounts(self):
        # Seed position with $150K USD unrealised gain in taxable account
        # Set threshold to 100000
        # Assert tax_risk_flag = True
        pass

    def test_excluded_account_not_in_portfolio_total(self):
        # TAXABLE_CAR (exclude_from_risk=True) positions should not appear
        # in entity_type='portfolio' roll-up
        pass
```

Run with: python manage.py test risk.tests.test_portfolio_metrics --settings=portfolio_manager.settings_test

---

## Constraints

- DO NOT implement urgency scoring — that is M2
- DO NOT recompute individual security metrics — read from rm_security_risk_metrics only
- DO NOT call yFinance or DataSource directly — all price data already in rm_ tables
- DO NOT push to git (manual step)
- Correlated VaR must always be less than or equal to undiversified VaR — add assertion in code
- Joint account split default is 0.5 (50/50); sourced from rm_scoring_config, not hard-coded
- All monetary output in USD; also store CAD total for display
- PEP 8 throughout; full type hints on all functions

---

## Manual Steps Required (in order)

- [ ] Verify gate: SELECT COUNT(*) FROM rm_security_risk_metrics WHERE calc_date = '2026-06-02' — expect 28 rows
- [ ] Verify gate: SELECT COUNT(*) FROM rm_correlation_matrix WHERE calc_date = '2026-06-02' — expect ~28*28 rows
- [ ] python manage.py makemigrations risk
- [ ] python manage.py migrate --settings=portfolio_manager.settings_dev
- [ ] python manage.py calc_portfolio_metrics --date 2026-06-02 --settings=portfolio_manager.settings_dev
- [ ] Spot-check in SSMS: SELECT entity_type, entity_id, var_portfolio_95_usd, diversification_ratio, effective_sp500_weight, hhi_symbol FROM rm_portfolio_risk_metrics WHERE calc_date='2026-06-02' ORDER BY entity_type, entity_id
- [ ] Verify: portfolio-level effective_sp500_weight should be 0.30 or higher (VFV+SPY+VOO+ADX+IWY+CGDV)
- [ ] Verify: diversification_ratio > 1.0 for portfolio entity
- [ ] Verify: var_portfolio_95_usd < var_undiversified_95_usd for portfolio entity
- [ ] Verify: TOM and SUSAN owner rows both present
- [ ] python manage.py test risk.tests.test_portfolio_metrics --settings=portfolio_manager.settings_test
- [ ] git add -A && git commit -m "feat: M1-Spec-2 portfolio roll-up + effective index concentration"
- [ ] git push origin feature/risk-m1-rollup

---

## Human Approval Required

Review the effective_sp500_weight output for the portfolio entity before proceeding to M2. The number should surprise you — it will likely be above 30%. That is the hidden concentration this module exists to surface.

---

M1-Spec-2 | Portfolio Manager Phase B | June 2026 | tdwdev
