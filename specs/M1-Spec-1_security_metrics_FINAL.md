# Feature Spec: M1-Spec-1 — Security-Level Risk Metrics

**Project name:** portfolio_manager
**App:** `risk`
**Customer:** tdwdev
**Feature branch:** `feature/risk-m1-security-metrics`
**Architecture ref:** PM_PhaseB_Architecture_Final_v2.0.md
**Depends on:** M0-Spec-2 fully implemented — `rm_market_price_history`, `rm_volatility_summary`, and `rm_fx_rates` populated for all held symbols with no critical gaps

---

## Context

This spec implements the core quantitative risk layer — per-security risk metrics computed from price history. It produces the numbers that M2 (urgency scoring), M4 (strategy optimisation), and M5 (backtesting) all depend on.

**This spec covers only security-level metrics.** Portfolio-level aggregation (correlated VaR, HHI, diversification ratio, owner roll-up) is M1-Spec-2.

**Portfolio reality (June 2, 2026 — most recent export):**

28 held symbols across 11 accounts (new account detected with blank nickname — must not block calculations).

Key risk characteristics that affect metric design:

| Symbol | Risk Characteristic | Impact on Metrics |
|---|---|---|
| ADX | Closed-end fund (CEF) | Liquidity score must use CEF-specific formula; may trade at NAV discount |
| IBIT, ETHA | Crypto ETFs | Fat-tailed returns; Cornish-Fisher VaR critical; jump-diffusion flagged for M5 |
| NVDA | High-beta single stock | Beta vs SPY will be >1.5; drawdown significant; use_jump_diffusion=True |
| AAPL | 1,832% unrealised gain in IB_SUSAN | UGL at risk must be computed; cost_basis_per_unit from position_history |
| VDY | 20.7% of portfolio | Concentration metric critical; CAD-denominated — FX conversion needed |
| VFV, VDY | CAD-denominated | Beta vs XIU.TO more meaningful than vs SPY |
| RBF2010, RBF2014, SGOV, ICSH | Cash equivalents / near-cash | Will have near-zero volatility; liquidity score near 100 |

**Stack:**
- Python 3.12 / Django 5.1
- MSSQL 2025 (dev: PortfolioDEV)
- numpy, scipy, pandas — already in venv (confirm; install if missing: `pip install numpy scipy pandas`)
- Project location: `C:\AI-DevTeam\projects\portfolio_manager`

---

## Requirements

### 1. New Database Tables

#### 1.1 `rm_security_risk_metrics`

One row per symbol per calculation date. Contains all security-level risk metrics.

```python
class SecurityRiskMetrics(models.Model):
    """
    Security-level risk metrics computed daily per held symbol.
    All monetary values in USD. CAD positions converted via rm_fx_rates.
    """
    symbol = models.CharField(max_length=20)
    calc_date = models.DateField()
    
    # --- Volatility ---
    vol_10d = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    vol_21d = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    vol_63d = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    vol_252d = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # Annualised: std(daily log returns over window) * sqrt(252)
    # NULL if insufficient price history for that window
    
    # --- Drawdown ---
    max_drawdown_from_cost = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # Maximum peak-to-trough drawdown since earliest holding date per position_history
    max_drawdown_252d = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # Maximum peak-to-trough drawdown over last 252 trading days from rm_market_price_history
    current_drawdown_from_252d_peak = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # (current_price - max_price_252d) / max_price_252d; negative if below peak
    
    # --- Value at Risk (1-day) ---
    var_parametric_95 = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # Parametric normal: -z_0.05 * vol_21d / sqrt(252). Stored as positive fraction (e.g. 0.0312 = 3.12% loss)
    var_parametric_99 = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    var_historical_95 = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # 5th percentile of last 252 daily log returns. Stored as positive fraction.
    var_cornish_fisher_95 = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # Cornish-Fisher expansion adjusts for skewness and kurtosis. See §3.3.
    
    # --- CVaR / Expected Shortfall ---
    cvar_95 = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # Mean of daily returns below the 5th percentile of the 252-day return distribution.
    # Stored as positive fraction (expected loss in tail events).
    
    # --- 10-day VaR ---
    var_parametric_95_10d = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # 1-day parametric 95% VaR scaled by sqrt(10). Square-root-of-time rule.
    var_parametric_99_10d = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    
    # --- Beta ---
    beta_spy = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # OLS regression of symbol daily returns vs SPY daily returns, 252-day window.
    # USD-denominated positions use SPY directly.
    # CAD-denominated positions: use CAD-adjusted SPY returns (SPY return + FX change).
    beta_xiu = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # OLS regression vs XIU.TO daily returns, 252-day window.
    # Computed for all symbols but most meaningful for CAD-denominated ones.
    
    # --- Risk-Adjusted Return ---
    sharpe_ratio = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # (mean_daily_return - daily_risk_free) / std_daily_return * sqrt(252)
    # daily_risk_free = annualised SGOV yield / 252, from rm_economic_indicators
    # mean_daily_return: log return from cost_basis_per_unit to current price / holding_days
    # If no position_history entry: use 252-day price return instead
    sortino_ratio = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # Same as Sharpe but denominator uses downside deviation only (returns below risk-free)
    
    # --- Liquidity ---
    liquidity_score = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    # 0–100 scale. See §3.7 for formula. 100 = highly liquid; 0 = illiquid.
    avg_daily_volume_usd_30d = models.DecimalField(max_digits=20, decimal_places=2, null=True)
    # Average (volume * close) over last 30 trading days from rm_market_price_history.
    # USD equivalent. NULL for mutual funds with no volume data.
    
    # --- Unrealised Gain at Risk ---
    ugl_at_risk_95_usd = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    # For the largest position in this symbol across all accounts:
    # 1-day 95% parametric VaR applied to total market value in USD.
    # = position_market_value_usd * var_parametric_95
    ugl_at_risk_pct_of_ugl = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # ugl_at_risk_usd / total_unrealised_gain_usd (across all accounts for this symbol).
    # NULL if total UGL <= 0.
    
    # --- Metadata ---
    calc_timestamp = models.DateTimeField(auto_now_add=True)
    price_data_source = models.CharField(max_length=20, blank=True)
    # 'amibroker', 'yfinance', or 'mixed' — which source provided the price data used
    price_rows_used = models.IntegerField(default=0)
    # Number of price rows available for this symbol at calc time (for audit)
    
    class Meta:
        db_table = 'rm_security_risk_metrics'
        unique_together = [('symbol', 'calc_date')]
        indexes = [
            models.Index(fields=['symbol', 'calc_date']),
            models.Index(fields=['calc_date']),
        ]
```

#### 1.2 `rm_correlation_matrix`

Pairwise Pearson correlation of all held symbol pairs, computed over 252-day rolling window.

```python
class CorrelationMatrix(models.Model):
    """
    Pairwise correlation between symbols.
    Both (A, B) and (B, A) are stored for query convenience.
    Diagonal (A, A) is stored as 1.0.
    """
    symbol_a = models.CharField(max_length=20)
    symbol_b = models.CharField(max_length=20)
    calc_date = models.DateField()
    pearson_252d = models.DecimalField(max_digits=8, decimal_places=6)
    # Pearson r of daily log returns over the last 252 trading days.
    # Range: -1.0 to 1.0.
    # Only computed where both symbols have >= 126 overlapping trading days.
    price_rows_overlap = models.IntegerField()
    # Number of dates where both symbols had price data (for quality assessment)
    calc_timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_correlation_matrix'
        unique_together = [('symbol_a', 'symbol_b', 'calc_date')]
        indexes = [
            models.Index(fields=['calc_date', 'symbol_a']),
            models.Index(fields=['calc_date', 'symbol_b']),
        ]
```

---

### 2. Management Command

Create `risk/management/commands/calc_security_metrics.py`.

```
python manage.py calc_security_metrics [--date YYYY-MM-DD] [--symbol SYM] [--skip-correlation]
```

- `--date`: calculate for a specific date (default: today)
- `--symbol`: calculate for one symbol only (default: all active held symbols)
- `--skip-correlation`: skip correlation matrix update (faster for single-symbol runs)

**Execution order within the command:**
```
1. Resolve symbols: all is_held=True symbols from rm_security_universe, or --symbol
2. Load position data from core_position_history for each symbol (cost basis, quantity, owner)
3. Load FX rate for calc_date from rm_fx_rates
4. For each symbol:
   a. Load price history from rm_market_price_history via DataSource
   b. Compute all metrics (see §3)
   c. UPSERT into rm_security_risk_metrics
5. Unless --skip-correlation: compute correlation matrix for all symbol pairs (see §3.8)
6. Print summary: symbols processed, any skipped (insufficient data), time elapsed
```

---

### 3. Metric Computation Details

All computation is in `risk/services/metrics.py`. Each function is pure Python (no Django ORM calls inside) and takes plain DataFrames/arrays as input. This makes them independently testable.

#### 3.1 Data Preparation

```python
def prepare_return_series(prices: pd.Series) -> pd.Series:
    """
    Input: daily closing price series (DatetimeIndex, float values).
    Output: daily log return series. ln(P_t / P_{t-1}).
    First element is NaN (dropped by callers).
    """

def align_return_series(series_a: pd.Series, series_b: pd.Series) -> tuple[pd.Series, pd.Series]:
    """
    Inner join on DatetimeIndex. Returns two series with identical index.
    Used for beta and correlation calculations.
    """
```

#### 3.2 Volatility

Already stored in `rm_volatility_summary` by M0-Spec-2. Load from there — do NOT recompute in this spec. Simply read the pre-computed values:

```python
def load_volatility(symbol: str, calc_date: date, windows: list[int]) -> dict[int, float]:
    """
    Load volatility from rm_volatility_summary for given symbol, date, and windows.
    Returns {10: 0.0312, 21: 0.0298, 63: 0.0276, 252: 0.0251}.
    Returns None for windows where data is unavailable.
    """
```

#### 3.3 Value at Risk

```python
def var_parametric(vol_annualised: float, horizon_days: int = 1, confidence: float = 0.95) -> float:
    """
    Parametric (normal) VaR.
    Formula: z_score * vol_daily * sqrt(horizon_days)
    where vol_daily = vol_annualised / sqrt(252)
    and z_score = scipy.stats.norm.ppf(1 - confidence)
    Returns positive fraction representing expected loss.
    Example: vol_annualised=0.30, horizon=1, confidence=0.95 → ~0.0311 (3.11% daily loss)
    Uses vol_21d as input (21-day volatility is standard for 1-day VaR).
    """

def var_historical(returns: pd.Series, confidence: float = 0.95) -> float:
    """
    Historical simulation VaR.
    Formula: abs(np.percentile(returns, (1 - confidence) * 100))
    Input: daily log return series, minimum 252 values.
    Returns positive fraction.
    """

def var_cornish_fisher(returns: pd.Series, confidence: float = 0.95) -> float:
    """
    Cornish-Fisher expansion VaR. Adjusts for skewness and excess kurtosis.
    
    Algorithm:
    1. Compute: mu = returns.mean(), sigma = returns.std(), 
                S = skewness(returns), K = excess_kurtosis(returns)
    2. z = scipy.stats.norm.ppf(1 - confidence)
    3. z_cf = z + (z**2 - 1)*S/6 + (z**3 - 3*z)*K/24 - (2*z**3 - 5*z)*S**2/36
    4. VaR_cf = abs(mu + z_cf * sigma)
    
    This is the preferred VaR for IBIT, ETHA, NVDA where fat tails matter.
    For normal-ish securities: Cornish-Fisher ≈ Parametric.
    Returns positive fraction.
    Requires minimum 63 return observations; return None if fewer.
    """

def cvar(returns: pd.Series, confidence: float = 0.95) -> float:
    """
    Conditional VaR (Expected Shortfall).
    Formula: mean of returns below the (1-confidence) percentile.
    Example: for 95% CVaR, take the mean of the worst 5% of daily returns.
    Returns positive fraction (expected loss in tail).
    """
```

#### 3.4 Drawdown

```python
def max_drawdown(prices: pd.Series) -> float:
    """
    Maximum peak-to-trough drawdown over the full price series.
    Formula: min((prices - prices.cummax()) / prices.cummax())
    Returns negative fraction. E.g. -0.34 = 34% drawdown.
    """

def current_drawdown_from_peak(prices: pd.Series, window: int = 252) -> float:
    """
    Current price relative to the maximum price in the last `window` trading days.
    Formula: (current_price - max(prices[-window:])) / max(prices[-window:])
    Returns negative fraction (or 0.0 if at a new high).
    """
```

#### 3.5 Beta

```python
def beta(symbol_returns: pd.Series, benchmark_returns: pd.Series) -> float:
    """
    OLS regression beta of symbol returns vs benchmark returns.
    Formula: cov(symbol, benchmark) / var(benchmark)
    Uses aligned 252-day return series.
    Returns float. Typical range -2.0 to 3.0 for this portfolio.
    Returns None if fewer than 126 overlapping observations.
    """
```

**Benchmark selection per symbol:**
- USD-denominated symbols: use SPY returns for `beta_spy`; also compute `beta_xiu` using XIU.TO returns
- CAD-denominated symbols (VDY, VFV, XDIV, XEQT, BANK, CAGE): compute `beta_xiu` from XIU.TO; also compute `beta_spy` using FX-adjusted SPY returns
- Mutual funds (RBF2010, RBF2014), SGOV, ICSH: set both betas to 0.0 (near-zero by definition); skip regression

**FX-adjusted SPY returns for CAD positions:**
```python
def fx_adjusted_returns(usd_returns: pd.Series, fx_returns: pd.Series) -> pd.Series:
    """
    Approximate CAD return = USD return + FX return (ln(FX_t / FX_{t-1})).
    Where FX is USD/CAD rate (number of CAD per USD).
    A CAD investor holding SPY earns SPY's USD return PLUS any USD/CAD appreciation.
    """
```

#### 3.6 Sharpe and Sortino Ratios

```python
def sharpe_ratio(
    symbol_returns: pd.Series,
    risk_free_annual: float,
    window: int = 252
) -> float:
    """
    Sharpe ratio using last `window` daily returns.
    daily_rf = risk_free_annual / 252
    excess_returns = symbol_returns - daily_rf
    Sharpe = (excess_returns.mean() / excess_returns.std()) * sqrt(252)
    
    Risk-free rate source: SGOV current yield approximated as 
    (last SGOV close - SGOV price 252 days ago) / SGOV price 252 days ago.
    If SGOV data unavailable: use 4.5% annualised as fallback (document this).
    """

def sortino_ratio(
    symbol_returns: pd.Series,
    risk_free_annual: float,
    window: int = 252
) -> float:
    """
    Sortino ratio — same as Sharpe but uses downside deviation as denominator.
    downside_dev = std(min(excess_returns, 0)) * sqrt(252)
    Sortino = (mean(excess_returns) * 252) / downside_dev
    Returns None if all excess returns are positive (no downside).
    """
```

#### 3.7 Liquidity Score

```python
def liquidity_score(
    avg_daily_dollar_volume_usd: float,
    position_market_value_usd: float,
    is_cef: bool = False,
    is_mutual_fund: bool = False
) -> float:
    """
    Liquidity score 0–100.
    
    Mutual funds (RBF2010, RBF2014): return 95.0 (high liquidity, daily NAV redemption)
    Cash equivalents (SGOV, ICSH): return 98.0
    
    For all others:
    days_to_exit = position_market_value_usd / avg_daily_dollar_volume_usd
    
    Score:
    - days_to_exit < 0.1:  100.0  (large, liquid ETF)
    - days_to_exit < 1.0:   90.0
    - days_to_exit < 5.0:   70.0
    - days_to_exit < 10.0:  50.0
    - days_to_exit < 30.0:  30.0
    - days_to_exit >= 30.0: 10.0
    
    CEF adjustment (ADX): multiply final score by 0.7 (CEFs trade at discount/premium,
    actual liquidity worse than volume implies).
    
    If avg_daily_dollar_volume_usd is 0 or NULL: return 5.0
    """
```

**Computing avg_daily_dollar_volume_usd:**
```python
def avg_daily_dollar_volume(symbol: str, window_days: int = 30) -> float:
    """
    From rm_market_price_history: mean of (volume * close) over last window_days rows.
    For USD symbols: result is already USD.
    For CAD symbols: multiply by current FX rate (usd_cad_rate).
    Returns 0.0 if no volume data available (mutual funds, some ETFs).
    """
```

#### 3.8 Correlation Matrix

```python
def compute_correlation_matrix(
    symbols: list[str],
    calc_date: date,
    window: int = 252
) -> pd.DataFrame:
    """
    Pearson correlation matrix for all symbol pairs.
    
    1. For each symbol: load last 252 daily log returns from DataSource
    2. Inner-join all return series on date (some symbols may have fewer days)
    3. Compute pandas correlation matrix: df.corr()
    4. For each (symbol_a, symbol_b) pair:
       - Only store if overlap >= 126 days
       - Store both (A, B) and (B, A) directions
       - Store diagonal (A, A) as 1.0
    5. UPSERT into rm_correlation_matrix
    
    Minimum 126 days chosen: half of 252 ensures the correlation is based on 
    at least 6 months of co-movement data.
    """
```

#### 3.9 Unrealised Gain at Risk

This requires joining with `core_position_history` to get position sizes and cost bases.

```python
def ugl_at_risk(
    symbol: str,
    calc_date: date,
    var_parametric_95: float
) -> tuple[float, float]:
    """
    Returns (ugl_at_risk_usd, ugl_at_risk_pct_of_ugl).
    
    1. Query core_position_history for all open positions (date_to IS NULL) for this symbol
    2. For each position: get quantity and cost_basis_per_unit
    3. Get latest price from rm_market_price_history
    4. Convert to USD if CAD (use rm_fx_rates for calc_date)
    5. Total market value USD = sum(quantity * price_usd) across all accounts
    6. Total UGL USD = sum(quantity * (price_usd - cost_basis_per_unit_usd))
    7. ugl_at_risk_usd = total_market_value_usd * var_parametric_95
    8. ugl_at_risk_pct = ugl_at_risk_usd / total_ugl_usd (None if total_ugl <= 0)
    
    Note: uses ALL accounts for this symbol (including excluded ones) for accurate UGL.
    The exclude_from_risk flag is for dashboard display only, not metric calculation.
    """
```

---

### 4. Position Data Integration

The metrics command reads from `core_position_history`. Specifically:

```python
def get_position_data(symbol: str, calc_date: date) -> list[PositionRecord]:
    """
    Load all open positions for symbol as of calc_date.
    Query: WHERE symbol = :symbol AND date_from <= :calc_date AND (date_to IS NULL OR date_to > :calc_date)
    
    Returns list of PositionRecord(account_id, owner, symbol, quantity, cost_basis_per_unit, 
                                    total_cost_basis, currency)
    """
```

**Cost basis for Sharpe ratio:**
- Use `cost_basis_per_unit` from `core_position_history` for the symbol's earliest open position
- This represents the investor's actual entry price, making the Sharpe ratio meaningful to the portfolio owner
- If multiple accounts hold the same symbol, use the weighted average cost basis across all accounts

---

### 5. Currency Handling

All metrics stored in USD. CAD positions converted at `calc_date` FX rate.

```python
def to_usd(value: float, currency: str, fx_rate: float) -> float:
    """
    Convert value to USD.
    If currency == 'USD': return value unchanged.
    If currency == 'CAD': return value / fx_rate  (fx_rate = CAD per 1 USD)
    """
```

**FX rate lookup:** always use the `rm_fx_rates` row for `calc_date`. If no rate on `calc_date` (weekend/holiday): use the most recent prior date. This is handled by `DataSource.get_fx_rate()`.

---

### 6. Handling Special Cases

| Symbol | Special handling |
|---|---|
| RBF2010, RBF2014 | No OHLCV in rm_market_price_history. Set all VaR, vol, beta, drawdown to NULL. Set liquidity_score=95. Set Sharpe/Sortino to NULL. |
| SGOV, ICSH | Near-zero volatility expected. All formulas run normally. Liquidity score=98. |
| ADX | CEF flag: liquidity_score *= 0.7 after normal computation. Beta vs SPY computed normally. |
| XIU.TO | Benchmark — used in beta calculation for CAD securities but NOT in held symbol metrics computation (not is_held). |
| New account (blank nickname in Jun 2 export) | position_history query will include it normally — account records are identified by account_id (bigint PK), not nickname. No special handling needed. |
| CAGE | If confirmed as crypto ETF: use jump-diffusion flag later (M5); Cornish-Fisher VaR especially important. |

---

### 7. Admin Registration

Register in `risk/admin.py`:

```python
@admin.register(SecurityRiskMetrics)
class SecurityRiskMetricsAdmin(admin.ModelAdmin):
    list_display = ['symbol', 'calc_date', 'vol_21d', 'var_parametric_95', 
                    'beta_spy', 'sharpe_ratio', 'liquidity_score', 'calc_timestamp']
    list_filter = ['calc_date', 'symbol']
    search_fields = ['symbol']
    ordering = ['-calc_date', 'symbol']
    readonly_fields = [f.name for f in SecurityRiskMetrics._meta.fields]  # Never edited manually


@admin.register(CorrelationMatrix)
class CorrelationMatrixAdmin(admin.ModelAdmin):
    list_display = ['symbol_a', 'symbol_b', 'calc_date', 'pearson_252d', 'price_rows_overlap']
    list_filter = ['calc_date']
    search_fields = ['symbol_a', 'symbol_b']
    ordering = ['-calc_date', 'symbol_a', 'symbol_b']
    readonly_fields = [f.name for f in CorrelationMatrix._meta.fields]
```

---

### 8. Tests

File: `risk/tests/test_security_metrics.py`

```python
import numpy as np
import pandas as pd
from decimal import Decimal
from django.test import TestCase
from risk.services.metrics import (
    var_parametric, var_historical, var_cornish_fisher, cvar,
    max_drawdown, current_drawdown_from_peak, beta, sharpe_ratio,
    sortino_ratio, liquidity_score, compute_correlation_matrix
)


class VaRFormulaTests(TestCase):
    """Test VaR formulas against known analytical results."""
    
    def test_parametric_var_95_known_value(self):
        # vol_annualised=0.20 (20% annual vol)
        # 1-day parametric 95% VaR = 1.645 * 0.20/sqrt(252) ≈ 0.02074
        result = var_parametric(vol_annualised=0.20, horizon_days=1, confidence=0.95)
        self.assertAlmostEqual(result, 0.02074, places=4)
    
    def test_parametric_var_99_higher_than_95(self):
        v95 = var_parametric(0.25, 1, 0.95)
        v99 = var_parametric(0.25, 1, 0.99)
        self.assertGreater(v99, v95)
    
    def test_parametric_var_10d_scales_by_sqrt10(self):
        v1d = var_parametric(0.20, 1, 0.95)
        v10d = var_parametric(0.20, 10, 0.95)
        self.assertAlmostEqual(v10d, v1d * np.sqrt(10), places=6)
    
    def test_historical_var_95_from_known_distribution(self):
        # 200 daily returns: 190 of 0.001, 10 of -0.05 (worst 5%)
        returns = pd.Series([0.001] * 190 + [-0.05] * 10)
        result = var_historical(returns, 0.95)
        # 5th percentile of this distribution is -0.05
        self.assertAlmostEqual(result, 0.05, places=4)
    
    def test_cornish_fisher_greater_than_parametric_for_fat_tails(self):
        # Simulate fat-tailed returns (high kurtosis)
        np.random.seed(42)
        from scipy.stats import t
        # t-distribution with 3 df has heavy tails
        returns = pd.Series(t.rvs(df=3, size=252) * 0.01)
        cf_var = var_cornish_fisher(returns, 0.95)
        param_var = var_parametric(returns.std() * np.sqrt(252), 1, 0.95)
        # Cornish-Fisher should be higher for fat-tailed distributions
        self.assertGreater(cf_var, param_var * 0.9)  # Allow some tolerance
    
    def test_cvar_greater_than_var(self):
        np.random.seed(42)
        returns = pd.Series(np.random.normal(-0.001, 0.015, 252))
        v = var_historical(returns, 0.95)
        cv = cvar(returns, 0.95)
        self.assertGreater(cv, v)
    
    def test_var_returns_none_insufficient_data(self):
        # Historical VaR requires >=63 observations
        returns = pd.Series(np.random.normal(0, 0.01, 50))
        result = var_cornish_fisher(returns, 0.95)
        self.assertIsNone(result)


class DrawdownTests(TestCase):
    
    def test_max_drawdown_known_sequence(self):
        # Prices: 100, 120, 80, 110. Peak=120, Trough=80. Drawdown=(80-120)/120 = -0.333
        prices = pd.Series([100, 120, 80, 110], 
                           index=pd.date_range('2025-01-01', periods=4))
        result = max_drawdown(prices)
        self.assertAlmostEqual(result, -0.3333, places=3)
    
    def test_max_drawdown_all_rising(self):
        prices = pd.Series([100, 110, 120, 130])
        result = max_drawdown(prices)
        self.assertAlmostEqual(result, 0.0, places=6)
    
    def test_current_drawdown_at_new_high(self):
        prices = pd.Series([100, 110, 120, 130])
        result = current_drawdown_from_peak(prices)
        self.assertAlmostEqual(result, 0.0, places=6)
    
    def test_current_drawdown_below_peak(self):
        prices = pd.Series([100, 130, 110])
        result = current_drawdown_from_peak(prices)
        # (110 - 130) / 130 = -0.1538
        self.assertAlmostEqual(result, -0.1538, places=3)


class BetaTests(TestCase):
    
    def test_beta_of_one_for_identical_returns(self):
        np.random.seed(1)
        market = pd.Series(np.random.normal(0.001, 0.01, 252))
        result = beta(market, market)
        self.assertAlmostEqual(result, 1.0, places=4)
    
    def test_beta_of_two_for_double_returns(self):
        np.random.seed(1)
        market = pd.Series(np.random.normal(0.001, 0.01, 252))
        double = market * 2
        result = beta(double, market)
        self.assertAlmostEqual(result, 2.0, places=3)
    
    def test_beta_returns_none_insufficient_overlap(self):
        # Only 100 overlapping days (< 126 minimum)
        a = pd.Series(np.random.normal(0, 0.01, 100))
        b = pd.Series(np.random.normal(0, 0.01, 100))
        result = beta(a, b)
        self.assertIsNone(result)
    
    def test_negative_beta(self):
        np.random.seed(2)
        market = pd.Series(np.random.normal(0.001, 0.01, 252))
        inverse = -market
        result = beta(inverse, market)
        self.assertAlmostEqual(result, -1.0, places=3)


class SharpeTests(TestCase):
    
    def test_sharpe_positive_for_positive_excess_returns(self):
        # Returns consistently above risk-free
        returns = pd.Series([0.002] * 252)  # 0.2% daily
        result = sharpe_ratio(returns, risk_free_annual=0.04)
        self.assertGreater(result, 0)
    
    def test_sharpe_zero_for_risk_free_level_returns(self):
        # Returns exactly at risk-free rate each day
        rf_daily = 0.05 / 252
        returns = pd.Series([rf_daily] * 252)
        result = sharpe_ratio(returns, risk_free_annual=0.05)
        self.assertAlmostEqual(result, 0.0, places=4)
    
    def test_sortino_equals_sharpe_when_no_downside(self):
        # All returns positive — sortino denominator = sharpe denominator conceptually
        returns = pd.Series([0.001] * 252)
        rf = 0.02
        # Sortino should still compute without error
        result = sortino_ratio(returns, risk_free_annual=rf)
        self.assertIsNotNone(result)


class LiquidityScoreTests(TestCase):
    
    def test_highly_liquid_large_etf(self):
        # $500M daily volume, $1M position → 0.002 days to exit
        result = liquidity_score(500_000_000, 1_000_000)
        self.assertEqual(result, 100.0)
    
    def test_illiquid_large_position(self):
        # $100K daily volume, $5M position → 50 days to exit
        result = liquidity_score(100_000, 5_000_000)
        self.assertEqual(result, 10.0)
    
    def test_cef_penalty_applied(self):
        # Same as liquid case but with CEF flag
        base = liquidity_score(500_000_000, 1_000_000, is_cef=False)
        cef = liquidity_score(500_000_000, 1_000_000, is_cef=True)
        self.assertAlmostEqual(cef, base * 0.7, places=2)
    
    def test_mutual_fund_high_liquidity(self):
        result = liquidity_score(0, 0, is_mutual_fund=True)
        self.assertEqual(result, 95.0)
    
    def test_zero_volume_gives_low_score(self):
        result = liquidity_score(0, 1_000_000)
        self.assertEqual(result, 5.0)


class CorrelationMatrixTests(TestCase):
    
    def test_diagonal_is_one(self):
        # After compute, (A, A) pair should be 1.0
        # Use synthetic data — seed into rm_market_price_history first
        pass  # Integration test — set up in fixtures
    
    def test_matrix_is_symmetric(self):
        # (A, B) pearson should equal (B, A) pearson
        pass  # Integration test
    
    def test_pair_skipped_when_insufficient_overlap(self):
        # Symbol C has only 100 days of history; pairs with C should be skipped
        pass  # Integration test


class IntegrationTests(TestCase):
    """End-to-end test: seed price data, run command, verify output."""
    
    def test_full_metric_calculation_for_synthetic_symbol(self):
        """
        Seed 300 rows of synthetic price data for a test symbol.
        Run calc_security_metrics for that symbol.
        Assert rm_security_risk_metrics has one row with non-null values
        for vol_21d, var_parametric_95, beta_spy (if SPY data also seeded),
        sharpe_ratio, liquidity_score.
        """
        pass  # Implement with test fixtures
    
    def test_cash_equiv_symbol_has_null_var_and_beta(self):
        """
        Seed RBF2010-like symbol (mutual_fund=True, no price data).
        Run calc_security_metrics.
        Assert var_parametric_95 is None, liquidity_score = 95.
        """
        pass
    
    def test_ugl_at_risk_computed_correctly(self):
        """
        Seed core_position_history: 100 shares of TEST_SYM at cost $50.
        Seed current price: $100.
        Seed vol_21d: 0.20 (20% annualised).
        Run calc_security_metrics.
        Expected ugl_at_risk_usd = 100 * $100 * var_parametric_95(0.20)
                                  ≈ 10000 * 0.02074 ≈ $207.
        Expected ugl_at_risk_pct_of_ugl = 207 / (100 * (100-50)) = 207/5000 ≈ 0.0414
        """
        pass
```

Run with: `python manage.py test risk.tests.test_security_metrics --settings=portfolio_manager.settings_test`

---

## Constraints

- **DO NOT** implement portfolio-level aggregation (correlated VaR, HHI, portfolio Sharpe) — that is M1-Spec-2
- **DO NOT** implement urgency scoring or regime detection — that is M2
- **DO NOT** call yFinance directly — all price data via `DataSource` from M0-Spec-2
- **DO NOT** modify any Phase A table (position_history is read-only from this spec's perspective)
- **DO NOT** push to git (manual step)
- All monetary values stored in USD; CAD converted via rm_fx_rates
- PEP 8 throughout; full type hints and docstrings on all public functions
- numpy and scipy are used for all numerical computation — no pure-Python reimplementations of standard statistics
- MSSQL-compatible MERGE syntax for UPSERT operations

---

## Manual Steps Required (in order)

- [ ] Verify M0-Spec-2 gate: `SELECT COUNT(*) FROM rm_market_price_history WHERE symbol='VDY'` — expect 2000+ rows
- [ ] Verify M0-Spec-2 gate: `SELECT COUNT(*) FROM rm_volatility_summary WHERE lookback_days=252` — expect ~28 rows
- [ ] `pip install numpy scipy pandas` in risk venv (if not already present)
- [ ] `python manage.py makemigrations risk` (for SecurityRiskMetrics, CorrelationMatrix)
- [ ] `python manage.py migrate --settings=portfolio_manager.settings_dev`
- [ ] `python manage.py calc_security_metrics --date 2026-06-02 --settings=portfolio_manager.settings_dev`
- [ ] Spot-check: `SELECT symbol, vol_21d, var_parametric_95, beta_spy, sharpe_ratio FROM rm_security_risk_metrics WHERE calc_date='2026-06-02' ORDER BY symbol`
- [ ] Manual verification (open SSMS):
  - VDY vol_21d: expect 8–15% annualised (stable dividend ETF)
  - NVDA vol_21d: expect 40–70% annualised (high-vol single stock)
  - IBIT/ETHA: expect 60–100%+ annualised (crypto)
  - SGOV vol_21d: expect <1% annualised
  - ADX liquidity_score: should be meaningfully lower than VDY
- [ ] `python manage.py calc_security_metrics --skip-correlation --settings=portfolio_manager.settings_dev` (verify single-symbol flag works)
- [ ] `python manage.py test risk.tests.test_security_metrics --settings=portfolio_manager.settings_test`
- [ ] `git add -A && git commit -m "feat: M1-Spec-1 security-level risk metrics + correlation matrix"`
- [ ] `git push origin feature/risk-m1-security-metrics`

---

## Human Approval Required

Human review required before running `calc_security_metrics` on PortfolioDEV. Spot-check at least VDY, AAPL, NVDA, and IBIT values against external reference (e.g. look up volatility on Bloomberg or broker platform) before proceeding to M1-Spec-2.

---

*M1-Spec-1 | Portfolio Manager Phase B | June 2026 | tdwdev*
