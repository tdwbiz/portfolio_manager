# Feature Spec: M5-Spec-2 — Monte Carlo & Stress Testing

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/risk-m5-montecarlo

Architecture ref: PM_PhaseB_Architecture_Final_v2.0.md
Depends on: M5-Spec-1 fully implemented — rm_backtest_run, rm_backtest_result, rm_historical_scenario all populated and verified; M1-Spec-2 correlation matrix available; rm_volatility_summary populated for all held symbols

---

## Context

M5-Spec-1 tested strategies against history. M5-Spec-2 tests them against the future — or more accurately, against thousands of plausible futures generated from the statistical properties of past returns.

Monte Carlo simulation addresses a fundamental limitation of backtesting: historical scenarios only happened once. A 10% trailing stop on VFV worked well in 2022, but how does it perform across 10,000 simulated paths with the same volatility profile? What is the probability of losing more than 20% of the portfolio over the next year under the current regime? What does the distribution of outcomes look like with and without the recommended collar on AAPL?

Stress testing in this spec is distinct from the scenario backtest in M5-Spec-1. Here, stress tests apply instantaneous shocks to current positions — no price path, just a point-in-time impact calculation. This is useful for answering "what happens to the portfolio if semiconductors drop 30% tomorrow?"

**Key computations this portfolio specifically needs:**

| Computation | Why |
|---|---|
| Portfolio Monte Carlo with correlated paths | VFV + SPY + VOO correlation means uncorrelated paths would overstate diversification benefit |
| Jump-diffusion for IBIT, ETHA, NVDA | Fat tails and occasional large single-day moves are not captured by GBM |
| Collar on AAPL vs unhedged path comparison | Demonstrate to SUSAN the concrete distribution improvement from hedging |
| Semiconductor cluster stress test | SOXX + SMH + NVDA + DRAM — what happens if semis drop 30% overnight? |
| Taxable vs tax-free outcome comparison | Same VaR number means very different real cost depending on account type |

**Stack:**
- Python 3.12 / Django 5.1
- MSSQL 2025 (dev: PortfolioDEV)
- numpy for all path generation (vectorised — no Python loops over paths)
- scipy for statistical distributions
- Project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. New Database Tables

#### 1.1 rm_simulation_run

One row per Monte Carlo or stress test execution. Captures all input parameters.

```python
class SimulationRun(models.Model):
    """
    Input parameters for a Monte Carlo or stress test run.
    Append-only — no modification after creation.
    """
    simulation_type = models.CharField(max_length=20)
    # 'montecarlo' | 'stress_test' | 'custom_stress'

    # --- Scope ---
    entity_type = models.CharField(max_length=20)
    # 'portfolio' | 'account' | 'symbol'
    entity_id = models.CharField(max_length=50)
    # portfolio: 'ALL', account: account_id string, symbol: ticker

    # --- Monte Carlo parameters (null for stress tests) ---
    n_paths = models.IntegerField(null=True, blank=True)
    # Number of simulated paths. Default 10,000.
    horizon_days = models.IntegerField(null=True, blank=True)
    # Simulation horizon in trading days. Default 252 (1 year).
    use_jump_diffusion = models.BooleanField(default=False)
    # If True: apply Merton jump-diffusion for symbols flagged in rm_security_universe
    random_seed = models.IntegerField(null=True, blank=True)
    # If set: reproducible results. NULL = random seed each run.

    # --- Strategy overlay (optional) ---
    recommendation_id = models.IntegerField(null=True, blank=True)
    # FK to rm_strategy_recommendation (nullable FK to avoid circular imports — store id only)
    # If set: apply this strategy to the simulated paths and compare hedged vs unhedged.

    # --- Stress test parameters (null for Monte Carlo) ---
    scenario_name = models.CharField(max_length=100, blank=True)
    # Named scenario from rm_historical_scenario, or 'custom'
    custom_shocks_json = models.TextField(blank=True)
    # For custom stress: JSON {symbol: shock_pct} e.g. {"SOXX": -0.30, "SMH": -0.28, "NVDA": -0.35}

    # --- Metadata ---
    calc_date = models.DateField()
    # Position weights and prices as of this date
    run_timestamp = models.DateTimeField(auto_now_add=True)
    run_by = models.CharField(max_length=100, blank=True)
    # 'system' or username
    duration_seconds = models.DecimalField(max_digits=8, decimal_places=2, null=True)
    # Wall-clock time for this run — useful for performance monitoring

    class Meta:
        db_table = 'rm_simulation_run'
        indexes = [
            models.Index(fields=['entity_type', 'entity_id', 'calc_date']),
            models.Index(fields=['simulation_type', 'run_timestamp']),
        ]
```

#### 1.2 rm_simulation_result

One row per simulation run. Stores the output distribution and key statistics.

```python
class SimulationResult(models.Model):
    """
    Output metrics for a completed simulation run.
    All monetary values in USD.
    """
    run = models.OneToOneField(
        SimulationRun, on_delete=models.CASCADE, related_name='result'
    )

    # --- Portfolio value at start ---
    portfolio_value_start_usd = models.DecimalField(max_digits=18, decimal_places=2)
    # Total market value of positions in scope at calc_date

    # --- Monte Carlo outcome distribution (unhedged) ---
    pct5_outcome_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # 5th percentile of final portfolio value across all paths — worst 5% of outcomes
    pct25_outcome_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    pct50_outcome_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # Median outcome
    pct75_outcome_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    pct95_outcome_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # 95th percentile — best 5% of outcomes

    # --- Loss probability thresholds ---
    prob_loss_5pct = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    # Fraction of paths where final value < start * 0.95 (>5% loss)
    prob_loss_10pct = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    # Fraction of paths where final value < start * 0.90
    prob_loss_20pct = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    # Fraction of paths where final value < start * 0.80
    prob_loss_50pct = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    # Fraction of paths where final value < start * 0.50

    # --- Expected Shortfall (tail risk) ---
    expected_shortfall_5pct_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # Mean portfolio value across the worst 5% of paths (CVaR at 95%)
    expected_shortfall_pct = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # (portfolio_value_start - expected_shortfall_5pct) / portfolio_value_start

    # --- Hedged comparison (populated only if recommendation_id was set) ---
    pct5_outcome_hedged_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    pct50_outcome_hedged_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    pct95_outcome_hedged_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    prob_loss_10pct_hedged = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    expected_shortfall_5pct_hedged_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    hedge_cost_total_usd = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    # Upfront cost of the hedge strategy (option premium or friction)

    # --- Stress test results (populated only for stress_test runs) ---
    stress_loss_unhedged_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # Total dollar loss if scenario shocks applied instantaneously
    stress_loss_unhedged_pct = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    stress_loss_hedged_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # Loss after applying the strategy overlay
    stress_loss_hedged_pct = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    stress_per_position_json = models.TextField(blank=True)
    # JSON: per-symbol contribution to total stress loss
    # [{"symbol": "SOXX", "account": "TFSA_TOM", "loss_usd": -11500, "loss_pct": -0.302}, ...]

    # --- Full path distribution (for charting in M6) ---
    percentile_series_json = models.TextField(blank=True)
    # JSON: time-series of percentile bands for charting.
    # {"days": [0,21,63,126,252],
    #  "p5":  [100000, 95000, 88000, 83000, 79000],
    #  "p25": [100000, 98000, 96000, 95000, 94000],
    #  "p50": [100000, 101000, 103000, 105000, 107000],
    #  "p75": [100000, 104000, 110000, 116000, 122000],
    #  "p95": [100000, 108000, 119000, 131000, 144000]}
    # Sampled at 5 time points to keep storage manageable.

    calc_timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_simulation_result'
```

#### 1.3 rm_custom_stress_scenario

Allows users to define their own shock scenarios beyond the named historical ones.

```python
class CustomStressScenario(models.Model):
    """
    User-defined instantaneous shock scenario for stress testing.
    """
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    owner = models.CharField(max_length=50, blank=True)
    # TOM, SUSAN, or blank (shared)
    shocks_json = models.TextField()
    # JSON: {symbol: shock_fraction}
    # E.g. {"SOXX": -0.30, "SMH": -0.28, "NVDA": -0.35, "DRAM": -0.32}
    # Positive values = price increase. Negative = price drop.
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'rm_custom_stress_scenario'
        ordering = ['-created_at']
```

---

### 2. Management Commands

#### 2.1 run_montecarlo

```
python manage.py run_montecarlo \
    [--entity portfolio|account|symbol] \
    [--entity-id ID] \
    [--n 10000] \
    [--horizon 252] \
    [--date YYYY-MM-DD] \
    [--strategy REC_ID] \
    [--seed INT] \
    [--use-jump-diffusion]
```

- --entity: scope of simulation (default: portfolio)
- --entity-id: account_id string, symbol, or 'ALL' for portfolio (default: ALL)
- --n: number of paths (default: 10,000)
- --horizon: trading days (default: 252)
- --date: use positions and prices as of this date (default: today)
- --strategy: rm_strategy_recommendation.id — run hedged comparison
- --seed: integer seed for reproducibility (optional)
- --use-jump-diffusion: override; apply jump-diffusion to all symbols regardless of flag

**Execution order:**
```
1. Load position weights from vw_current_portfolio for the entity
2. Load rm_security_risk_metrics for all symbols (drift, vol parameters)
3. Load rm_correlation_matrix for portfolio-level runs
4. Load rm_volatility_summary 252d for all symbols
5. For each symbol: compute drift = mean daily log return from rm_market_price_history (252d)
6. Generate correlated price paths (see §3)
7. If --strategy provided: apply strategy overlay to each path (see §4)
8. Compute output distribution (see §5)
9. Insert SimulationRun and SimulationResult
10. Print summary: entity, n_paths, horizon, key percentiles, time elapsed
```

#### 2.2 run_stress_test

```
python manage.py run_stress_test \
    [--scenario SCENARIO_NAME] \
    [--custom-scenario CUSTOM_ID] \
    [--account ACC] \
    [--date YYYY-MM-DD] \
    [--strategy REC_ID]
```

- --scenario: named scenario from rm_historical_scenario
- --custom-scenario: rm_custom_stress_scenario.id for user-defined shocks
- --account: restrict to one account (default: whole portfolio)
- --strategy: apply hedged comparison

**Execution order:**
```
1. Load shock map: either from rm_historical_scenario.asset_class_returns_json
   or rm_custom_stress_scenario.shocks_json
2. Load current positions from vw_current_portfolio
3. For each position: apply shock appropriate to its asset_class
   (asset_class_returns_json keys map to rm_security_universe.asset_class)
4. If --strategy provided: compute hedge payoff under the shock
5. Sum losses across positions for portfolio total
6. Insert SimulationRun and SimulationResult
7. Print per-position loss table to stdout
```

---

### 3. Monte Carlo Path Generation

Create `risk/services/monte_carlo.py`. All functions are pure numpy — no ORM calls inside.

#### 3.1 Standard GBM (Geometric Brownian Motion)

```python
def generate_gbm_paths(
    S0: float,
    mu: float,           # annualised drift (mean daily return * 252)
    sigma: float,        # annualised volatility from rm_volatility_summary
    horizon_days: int,
    n_paths: int,
    dt: float = 1/252    # daily time step
) -> np.ndarray:
    """
    Generate N independent GBM price paths.

    Formula per time step:
        S[t] = S[t-1] * exp((mu - 0.5*sigma^2)*dt + sigma*sqrt(dt)*Z)
    where Z ~ N(0,1)

    Returns np.ndarray of shape (n_paths, horizon_days + 1).
    Column 0 = S0 (starting price). Column horizon_days = final price.

    Implementation: fully vectorised — generate all random draws at once.
        Z = np.random.standard_normal((n_paths, horizon_days))
        log_returns = (mu - 0.5*sigma**2)*dt + sigma*np.sqrt(dt)*Z
        paths = S0 * np.exp(np.cumsum(log_returns, axis=1))
        paths = np.hstack([np.full((n_paths, 1), S0), paths])
    """
```

#### 3.2 Jump-Diffusion (Merton Model)

Applied to IBIT, ETHA, NVDA — symbols where `rm_security_universe.use_jump_diffusion = True`.

```python
def generate_jump_diffusion_paths(
    S0: float,
    mu: float,
    sigma: float,
    jump_intensity: float,    # lambda: average number of jumps per year
    jump_mean: float,         # mu_J: mean jump size (log scale, e.g. -0.05 = -5% average jump)
    jump_std: float,          # sigma_J: std of jump size (log scale, e.g. 0.10)
    horizon_days: int,
    n_paths: int,
    dt: float = 1/252
) -> np.ndarray:
    """
    Merton jump-diffusion paths: GBM + compound Poisson jump process.

    Per time step:
        N_t ~ Poisson(lambda * dt)    # number of jumps in this step
        J_t = sum of N_t jumps, each drawn from N(mu_J, sigma_J)
        S[t] = S[t-1] * exp((mu - 0.5*sigma^2)*dt + sigma*sqrt(dt)*Z + J_t)

    Default parameters for crypto ETFs (IBIT, ETHA):
        jump_intensity = 5.0   # ~5 significant jumps per year
        jump_mean      = -0.04 # jumps average -4% (negative skew)
        jump_std       = 0.08  # high variance in jump magnitude

    Default parameters for NVDA:
        jump_intensity = 3.0
        jump_mean      = -0.02
        jump_std       = 0.06

    These defaults should be stored in rm_scoring_config for configurability.
    Do NOT hard-code in function body — load from config.

    Returns np.ndarray of shape (n_paths, horizon_days + 1).
    """
```

#### 3.3 Correlated Portfolio Paths

```python
def generate_correlated_paths(
    symbols: list[str],
    S0_values: dict[str, float],       # {symbol: current_price_usd}
    mu_values: dict[str, float],       # {symbol: annualised_drift}
    sigma_values: dict[str, float],    # {symbol: annualised_vol}
    correlation_matrix: np.ndarray,   # shape (n_symbols, n_symbols)
    position_weights: dict[str, float], # {symbol: portfolio_weight}
    horizon_days: int,
    n_paths: int,
    use_jump_diffusion_flags: dict[str, bool],
    jump_params: dict[str, dict],      # {symbol: {intensity, mean, std}}
    dt: float = 1/252
) -> tuple[np.ndarray, np.ndarray]:
    """
    Generate correlated price paths for all symbols using Cholesky decomposition.

    Algorithm:
    1. Compute Cholesky decomposition: L = cholesky(correlation_matrix)
       If matrix is not positive definite: apply Higham nearest PSD fix
       (scipy.linalg.cholesky raises; catch and apply nearPD correction)
    2. Generate independent standard normal draws:
       Z_independent = np.random.standard_normal((n_paths, n_symbols, horizon_days))
    3. Apply correlation: Z_correlated = Z_independent @ L.T
       (matrix multiply; each time step gets correlated draws)
    4. For each symbol: generate paths using correlated Z draws
       - Standard GBM for most symbols
       - Jump-diffusion for symbols where use_jump_diffusion_flags[symbol] = True
       - Add jump component independently (jumps are not correlated across symbols)
    5. Compute portfolio value at each time step:
       portfolio_value[path, t] = sum over symbols of:
           weight[symbol] * price[symbol, path, t] / price[symbol, path, 0] * portfolio_value_start

    Returns:
        symbol_paths: np.ndarray shape (n_symbols, n_paths, horizon_days + 1)
        portfolio_paths: np.ndarray shape (n_paths, horizon_days + 1)
            — portfolio value at each time step (normalised to starting portfolio value)
    """
```

#### 3.4 Nearest Positive Definite Matrix

```python
def nearest_positive_definite(matrix: np.ndarray) -> np.ndarray:
    """
    Find the nearest positive definite matrix to a given symmetric matrix.
    Uses the Higham (1988) algorithm.

    Required because correlation matrices from empirical data can be
    slightly non-positive-definite due to missing data and rounding.

    Algorithm:
    1. Symmetrise: A = (matrix + matrix.T) / 2
    2. Eigendecompose: eigenvalues, eigenvectors = np.linalg.eigh(A)
    3. Clip negative eigenvalues to a small positive value (epsilon = 1e-8)
    4. Reconstruct: A_pd = eigenvectors @ np.diag(clipped_eigenvalues) @ eigenvectors.T
    5. Re-symmetrise and rescale diagonal to 1.0

    This should rarely be needed if correlation matrix quality is good,
    but is essential for robustness.
    """
```

---

### 4. Strategy Overlay on Monte Carlo Paths

```python
def apply_strategy_overlay(
    portfolio_paths: np.ndarray,        # shape (n_paths, horizon_days + 1)
    symbol_paths: np.ndarray,           # shape (n_symbols, n_paths, horizon_days + 1)
    strategy_code: str,                 # e.g. 'trailing_stop', 'collar', 'protective_put'
    strategy_params: dict,              # trail_pct, put_strike_pct, call_strike_pct, etc.
    position_symbol: str,               # which symbol the strategy applies to
    symbol_index: int,                  # index of that symbol in symbol_paths
    position_weight: float,             # weight in portfolio
    vol_annualised: float,
    risk_free_rate: float,
    hedge_cost_per_unit_usd: float,     # pre-computed from Black-Scholes (M4)
) -> np.ndarray:
    """
    Apply a strategy overlay to the portfolio paths.

    For trailing_stop:
        For each path independently:
            Track the high water mark of the symbol price
            If price drops by trail_pct from high: record exit
            From exit day onward: symbol contributes 0 (position is cash at exit price)
        Subtract commission (small, negligible at portfolio level)

    For protective_put / collar:
        hedge_payoff[path, t] = max(0, put_strike - symbol_price[path, t]) * quantity
            at horizon (simplified: only compute at expiry, not path-dependent)
        Subtract hedge_cost_per_unit_usd * quantity from each path (upfront cost)

    For inverse_etf:
        daily_hedge_return = -symbol_daily_return * leverage_factor * (1 - daily_decay)
        hedge_payoff_cumulative[path, t] = cumsum of daily hedge payoffs
        Adjust portfolio_paths[path, t] += hedge_payoff_cumulative[path, t]
        Subtract decay cost

    Returns hedged_portfolio_paths: np.ndarray shape (n_paths, horizon_days + 1)
    """
```

---

### 5. Output Distribution Computation

```python
def compute_output_distribution(
    portfolio_paths: np.ndarray,    # shape (n_paths, horizon_days + 1)
    portfolio_value_start: float
) -> dict:
    """
    Compute the full output distribution from simulated paths.

    final_values = portfolio_paths[:, -1]  # terminal portfolio value per path

    Returns dict:
    {
        'pct5':  np.percentile(final_values, 5),
        'pct25': np.percentile(final_values, 25),
        'pct50': np.percentile(final_values, 50),
        'pct75': np.percentile(final_values, 75),
        'pct95': np.percentile(final_values, 95),
        'prob_loss_5pct':  mean(final_values < portfolio_value_start * 0.95),
        'prob_loss_10pct': mean(final_values < portfolio_value_start * 0.90),
        'prob_loss_20pct': mean(final_values < portfolio_value_start * 0.80),
        'prob_loss_50pct': mean(final_values < portfolio_value_start * 0.50),
        'expected_shortfall': mean of worst 5% of final_values,
        'percentile_series_json': time-series bands at sample points [0,21,63,126,252]
    }
    """

def build_percentile_series_json(
    portfolio_paths: np.ndarray,
    sample_days: list[int] = [0, 21, 63, 126, 252]
) -> str:
    """
    Extract percentile bands at sampled time points for charting.

    For each day in sample_days (capped at horizon_days if shorter):
        slice = portfolio_paths[:, day]
        compute p5, p25, p50, p75, p95

    Returns JSON string matching rm_simulation_result.percentile_series_json schema.
    Only store sampled points — do not store all 252 days worth (too large).
    """
```

---

### 6. Stress Test Engine

Create `risk/services/stress_test.py`.

```python
def run_portfolio_stress_test(
    positions: list[dict],          # from vw_current_portfolio
    shock_map: dict[str, float],    # {asset_class_or_symbol: shock_fraction}
    current_prices: dict[str, float],  # {symbol: current_price_usd}
    fx_rate: float,
    strategy_overlay: dict | None = None
) -> dict:
    """
    Apply instantaneous shocks to current positions.

    Shock resolution — for each position:
    1. If shock_map has an entry for the specific symbol: use it directly
       E.g. custom_stress with {"SOXX": -0.30} applies exactly -30% to SOXX
    2. Else: look up rm_security_universe.asset_class for the symbol,
       then look up shock_map[asset_class]
       E.g. scenario asset_class_returns_json with {"common_stock_semi": -0.40}
       applies -40% to NVDA (asset_class='common_stock', sector='Technology/Semiconductor')
    3. If no matching key: assume 0% shock (document in output)

    For each position:
        position_mv_usd = quantity * current_price_usd (converted if CAD)
        shocked_mv_usd = position_mv_usd * (1 + shock_fraction)
        position_loss_usd = shocked_mv_usd - position_mv_usd  (negative = loss)

    stress_per_position: list of per-position results for stress_per_position_json

    If strategy_overlay provided:
        Apply simplified hedge payoff at shock magnitude
        E.g. trailing stop: if |shock| > trail_pct, stop triggered; loss = trail_pct * mv
        E.g. protective put: put_payoff = max(0, put_strike - shocked_price) * quantity

    Returns dict matching rm_simulation_result stress fields.
    """

def resolve_shock_for_position(
    symbol: str,
    asset_class: str,
    sector: str,
    shock_map: dict[str, float]
) -> float:
    """
    Resolve the applicable shock fraction for a position.

    Priority:
    1. Exact symbol match in shock_map
    2. Asset class + sector composite key (e.g. 'common_stock_semi')
    3. Asset class alone (e.g. 'etf_equity')
    4. Default: 0.0 (no shock)

    Log which key was used for transparency in output.
    """
```

---

### 7. Admin Registration

```python
@admin.register(SimulationRun)
class SimulationRunAdmin(admin.ModelAdmin):
    list_display = [
        'simulation_type', 'entity_type', 'entity_id',
        'calc_date', 'n_paths', 'horizon_days',
        'use_jump_diffusion', 'scenario_name', 'run_timestamp'
    ]
    list_filter = ['simulation_type', 'entity_type', 'calc_date', 'use_jump_diffusion']
    search_fields = ['entity_id', 'scenario_name']
    ordering = ['-run_timestamp']
    readonly_fields = [f.name for f in SimulationRun._meta.fields]


@admin.register(SimulationResult)
class SimulationResultAdmin(admin.ModelAdmin):
    list_display = [
        'run', 'portfolio_value_start_usd',
        'pct5_outcome_usd', 'pct50_outcome_usd', 'pct95_outcome_usd',
        'prob_loss_10pct', 'expected_shortfall_5pct_usd',
        'stress_loss_unhedged_pct'
    ]
    ordering = ['-calc_timestamp']
    readonly_fields = [f.name for f in SimulationResult._meta.fields]


@admin.register(CustomStressScenario)
class CustomStressScenarioAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner', 'created_at', 'is_active']
    list_filter = ['owner', 'is_active']
    search_fields = ['name', 'description']
    ordering = ['-created_at']
    # Editable — users create these via Django admin
```

---

### 8. Tests

File: `risk/tests/test_monte_carlo.py`

```python
import numpy as np
import pandas as pd
from django.test import TestCase
from django.core.management import call_command
from risk.services.monte_carlo import (
    generate_gbm_paths, generate_jump_diffusion_paths,
    generate_correlated_paths, nearest_positive_definite,
    compute_output_distribution
)
from risk.services.stress_test import run_portfolio_stress_test, resolve_shock_for_position


class GBMPathTests(TestCase):

    def test_gbm_paths_shape(self):
        paths = generate_gbm_paths(S0=100.0, mu=0.08, sigma=0.20,
                                   horizon_days=252, n_paths=1000)
        self.assertEqual(paths.shape, (1000, 253))  # n_paths x (horizon+1)

    def test_gbm_paths_start_at_s0(self):
        paths = generate_gbm_paths(S0=150.0, mu=0.05, sigma=0.25,
                                   horizon_days=100, n_paths=500)
        # All paths start at S0
        np.testing.assert_array_equal(paths[:, 0], 150.0)

    def test_gbm_paths_positive_prices(self):
        # GBM never produces zero or negative prices
        paths = generate_gbm_paths(S0=100.0, mu=-0.20, sigma=0.50,
                                   horizon_days=252, n_paths=5000)
        self.assertTrue((paths > 0).all())

    def test_gbm_mean_close_to_expected(self):
        # With large N, mean terminal value should converge to S0 * exp(mu * T)
        np.random.seed(42)
        paths = generate_gbm_paths(S0=100.0, mu=0.08, sigma=0.20,
                                   horizon_days=252, n_paths=50000)
        expected_mean = 100.0 * np.exp(0.08)  # S0 * exp(mu*T), T=1 year
        actual_mean = paths[:, -1].mean()
        # Should be within 1% of expected mean with 50K paths
        self.assertAlmostEqual(actual_mean, expected_mean, delta=expected_mean * 0.01)

    def test_gbm_vol_close_to_expected(self):
        # Log returns should have std ≈ sigma / sqrt(252) per day
        np.random.seed(99)
        paths = generate_gbm_paths(S0=100.0, mu=0.0, sigma=0.30,
                                   horizon_days=252, n_paths=100000)
        # Compute daily log returns for all paths
        log_returns = np.log(paths[:, 1:] / paths[:, :-1])
        daily_std = log_returns.std()
        expected_daily_std = 0.30 / np.sqrt(252)
        self.assertAlmostEqual(daily_std, expected_daily_std, delta=expected_daily_std * 0.02)

    def test_reproducible_with_seed(self):
        np.random.seed(123)
        paths1 = generate_gbm_paths(S0=100.0, mu=0.06, sigma=0.22,
                                    horizon_days=50, n_paths=100)
        np.random.seed(123)
        paths2 = generate_gbm_paths(S0=100.0, mu=0.06, sigma=0.22,
                                    horizon_days=50, n_paths=100)
        np.testing.assert_array_equal(paths1, paths2)


class JumpDiffusionTests(TestCase):

    def test_jump_diffusion_shape_matches_gbm(self):
        np.random.seed(7)
        paths = generate_jump_diffusion_paths(
            S0=100.0, mu=0.05, sigma=0.60,
            jump_intensity=5.0, jump_mean=-0.04, jump_std=0.08,
            horizon_days=252, n_paths=1000
        )
        self.assertEqual(paths.shape, (1000, 253))

    def test_jump_diffusion_has_fatter_tails_than_gbm(self):
        # With jumps, the distribution should have higher kurtosis
        np.random.seed(42)
        gbm_paths = generate_gbm_paths(S0=100.0, mu=0.05, sigma=0.60,
                                       horizon_days=252, n_paths=10000)
        jd_paths = generate_jump_diffusion_paths(
            S0=100.0, mu=0.05, sigma=0.60,
            jump_intensity=5.0, jump_mean=-0.04, jump_std=0.08,
            horizon_days=252, n_paths=10000
        )
        gbm_log_returns = np.log(gbm_paths[:, 1:] / gbm_paths[:, :-1]).flatten()
        jd_log_returns = np.log(jd_paths[:, 1:] / jd_paths[:, :-1]).flatten()
        from scipy.stats import kurtosis
        # Jump-diffusion should show higher excess kurtosis
        self.assertGreater(kurtosis(jd_log_returns), kurtosis(gbm_log_returns))

    def test_jump_diffusion_prices_positive(self):
        np.random.seed(1)
        paths = generate_jump_diffusion_paths(
            S0=50.0, mu=-0.10, sigma=0.80,
            jump_intensity=8.0, jump_mean=-0.08, jump_std=0.12,
            horizon_days=252, n_paths=2000
        )
        self.assertTrue((paths > 0).all())


class CorrelatedPathTests(TestCase):

    def test_correlated_paths_preserve_individual_vol(self):
        # Individual symbol volatilities should be approximately preserved
        np.random.seed(55)
        symbols = ['SPY', 'QQQ']
        S0 = {'SPY': 759.0, 'QQQ': 746.0}
        mu = {'SPY': 0.08, 'QQQ': 0.12}
        sigma = {'SPY': 0.18, 'QQQ': 0.22}
        weights = {'SPY': 0.5, 'QQQ': 0.5}
        corr = np.array([[1.0, 0.85], [0.85, 1.0]])
        jd_flags = {'SPY': False, 'QQQ': False}
        jump_params = {}

        symbol_paths, portfolio_paths = generate_correlated_paths(
            symbols, S0, mu, sigma, corr, weights,
            horizon_days=252, n_paths=20000,
            use_jump_diffusion_flags=jd_flags, jump_params=jump_params
        )
        # SPY log return std should be ≈ 0.18/sqrt(252)
        spy_idx = 0
        spy_log_returns = np.log(symbol_paths[spy_idx, :, 1:] / symbol_paths[spy_idx, :, :-1])
        self.assertAlmostEqual(spy_log_returns.std(), 0.18/np.sqrt(252), delta=0.001)

    def test_correlated_portfolio_var_less_than_uncorrelated(self):
        # Correlated paths should produce narrower portfolio distribution
        # than if symbols were simulated independently
        np.random.seed(77)
        symbols = ['A', 'B']
        S0 = {'A': 100.0, 'B': 100.0}
        mu = {'A': 0.07, 'B': 0.07}
        sigma = {'A': 0.25, 'B': 0.25}
        weights = {'A': 0.5, 'B': 0.5}
        jd_flags = {'A': False, 'B': False}

        # High correlation: less diversification benefit
        corr_high = np.array([[1.0, 0.95], [0.95, 1.0]])
        # Low correlation: more diversification benefit
        corr_low = np.array([[1.0, 0.10], [0.10, 1.0]])

        _, port_high = generate_correlated_paths(
            symbols, S0, mu, sigma, corr_high, weights,
            252, 20000, jd_flags, {}
        )
        _, port_low = generate_correlated_paths(
            symbols, S0, mu, sigma, corr_low, weights,
            252, 20000, jd_flags, {}
        )
        # Low-correlation portfolio should have smaller variance of terminal values
        self.assertLess(port_low[:, -1].std(), port_high[:, -1].std())

    def test_nearest_positive_definite_fixes_bad_matrix(self):
        # Construct a non-PD matrix (nearly singular)
        bad_matrix = np.array([
            [1.0, 0.99, 0.98],
            [0.99, 1.0, 0.99],
            [0.98, 0.99, 1.0]
        ])
        # Perturb slightly to make it non-PD
        bad_matrix[0, 0] = 0.999
        result = nearest_positive_definite(bad_matrix)
        # Result must be positive definite: all eigenvalues > 0
        eigenvalues = np.linalg.eigvalsh(result)
        self.assertTrue((eigenvalues > 0).all())


class OutputDistributionTests(TestCase):

    def test_percentiles_ordered_correctly(self):
        np.random.seed(42)
        # Create synthetic portfolio paths
        paths = np.random.lognormal(mean=11.5, sigma=0.2, size=(10000, 253))
        dist = compute_output_distribution(paths, portfolio_value_start=100000)
        self.assertLess(dist['pct5'], dist['pct25'])
        self.assertLess(dist['pct25'], dist['pct50'])
        self.assertLess(dist['pct50'], dist['pct75'])
        self.assertLess(dist['pct75'], dist['pct95'])

    def test_expected_shortfall_less_than_pct5(self):
        # ES at 95% should be worse (lower value) than the 5th percentile
        np.random.seed(42)
        paths = np.random.lognormal(mean=11.5, sigma=0.25, size=(10000, 253))
        dist = compute_output_distribution(paths, portfolio_value_start=100000)
        self.assertLess(dist['expected_shortfall'], dist['pct5'])

    def test_loss_probabilities_sum_consistently(self):
        # prob_loss_20pct should be <= prob_loss_10pct (stricter threshold = fewer events)
        np.random.seed(13)
        paths = np.random.lognormal(mean=11.5, sigma=0.3, size=(10000, 253))
        dist = compute_output_distribution(paths, portfolio_value_start=100000)
        self.assertLessEqual(dist['prob_loss_20pct'], dist['prob_loss_10pct'])
        self.assertLessEqual(dist['prob_loss_10pct'], dist['prob_loss_5pct'])

    def test_percentile_series_json_valid(self):
        import json
        np.random.seed(42)
        paths = np.random.lognormal(mean=11.5, sigma=0.2, size=(5000, 253))
        dist = compute_output_distribution(paths, portfolio_value_start=100000)
        parsed = json.loads(dist['percentile_series_json'])
        self.assertIn('days', parsed)
        self.assertIn('p5', parsed)
        self.assertIn('p50', parsed)
        self.assertIn('p95', parsed)
        self.assertEqual(len(parsed['days']), len(parsed['p50']))


class StressTestEngineTests(TestCase):

    def test_full_position_loss_on_direct_shock(self):
        # 100 shares of SOXX at $607.74 USD; shock = -30%
        positions = [{
            'symbol': 'SOXX', 'quantity': 64,
            'currency': 'USD', 'account_category': 'TFSA',
            'account_id': 'TFSA_TOM', 'owner': 'TOM'
        }]
        current_prices = {'SOXX': 607.74}
        shock_map = {'SOXX': -0.30}  # direct symbol key
        result = run_portfolio_stress_test(positions, shock_map, current_prices, fx_rate=1.384)
        expected_loss = 64 * 607.74 * -0.30
        self.assertAlmostEqual(result['stress_loss_unhedged_usd'], expected_loss, delta=1.0)

    def test_asset_class_shock_applied_when_no_symbol_key(self):
        # NVDA has no direct key; should use 'common_stock_semi' or 'common_stock'
        positions = [{
            'symbol': 'NVDA', 'quantity': 300, 'currency': 'USD',
            'account_category': 'Investment', 'account_id': 'IB_TOM', 'owner': 'TOM'
        }]
        current_prices = {'NVDA': 222.71}
        # No direct NVDA key — use asset class
        shock_map = {'common_stock_semi': -0.28, 'etf_equity': -0.15}
        result = run_portfolio_stress_test(positions, shock_map, current_prices, fx_rate=1.384)
        expected_loss = 300 * 222.71 * -0.28
        self.assertAlmostEqual(result['stress_loss_unhedged_usd'], expected_loss, delta=5.0)

    def test_zero_shock_for_unmatched_symbol(self):
        result_pct = resolve_shock_for_position(
            symbol='SGOV', asset_class='cash_equiv', sector='',
            shock_map={'etf_equity': -0.20}  # no key for cash_equiv
        )
        self.assertEqual(result_pct, 0.0)

    def test_portfolio_stress_sums_across_positions(self):
        positions = [
            {'symbol': 'SOXX', 'quantity': 63, 'currency': 'USD',
             'account_category': 'TFSA', 'account_id': 'TFSA_TOM', 'owner': 'TOM'},
            {'symbol': 'SMH', 'quantity': 55, 'currency': 'USD',
             'account_category': 'TFSA', 'account_id': 'TFSA_TOM', 'owner': 'TOM'},
        ]
        current_prices = {'SOXX': 604.68, 'SMH': 632.13}
        shock_map = {'SOXX': -0.30, 'SMH': -0.28}
        result = run_portfolio_stress_test(positions, shock_map, current_prices, fx_rate=1.384)
        expected = (63 * 604.68 * -0.30) + (55 * 632.13 * -0.28)
        self.assertAlmostEqual(result['stress_loss_unhedged_usd'], expected, delta=2.0)


class IntegrationTests(TestCase):

    def test_full_portfolio_monte_carlo_run(self):
        """
        Seed rm_security_risk_metrics, rm_correlation_matrix, rm_volatility_summary
        for a minimal 3-symbol portfolio.
        Run: python manage.py run_montecarlo --entity portfolio --n 1000 --horizon 63
        Assert SimulationRun and SimulationResult created.
        Assert pct5 < pct50 < pct95.
        Assert percentile_series_json is valid JSON.
        """
        pass  # Implement with test fixtures

    def test_hedged_comparison_improves_tail_outcome(self):
        """
        Run Monte Carlo with --strategy pointing to a trailing_stop recommendation.
        Assert pct5_outcome_hedged_usd > pct5_outcome_usd (hedged tail is better).
        Assert hedge_cost_total_usd > 0.
        """
        pass

    def test_stress_test_command_for_semiconductor_cluster(self):
        """
        Seed positions for SOXX (TFSA_TOM), SMH (TFSA_TOM), NVDA (IB_TOM).
        Run: python manage.py run_stress_test --custom-scenario [id with -30% semi shock]
        Assert stress_loss_unhedged_pct is approximately -0.28 to -0.32
        (weighted across positions)
        Assert per-position breakdown in stress_per_position_json lists all three symbols.
        """
        pass

    def test_simulation_result_stored_with_run(self):
        """
        After any run_montecarlo call: assert SimulationRun has a related SimulationResult.
        Assert result.run = the run object (OneToOneField).
        """
        pass
```

Run with: python manage.py test risk.tests.test_monte_carlo --settings=portfolio_manager.settings_test

---

## Constraints

- DO NOT implement any UI — that is M6-Spec-2
- DO NOT call yFinance directly — all data via DataSource from M0-Spec-2
- DO NOT push to git (manual step)
- All path generation is vectorised numpy — no Python loops over individual paths
- n_paths=10,000 at horizon=252 must complete in under 60 seconds on saturn (benchmark after implementation)
- All monetary values in USD; convert CAD positions via rm_fx_rates at calc_date
- Simulation results are append-only — no update or delete after creation
- If Cholesky fails (non-PD matrix): apply nearest_positive_definite fix and log a WARNING — do not abort
- Jump-diffusion parameters (intensity, mean, std) stored in rm_scoring_config — never hard-coded
- PEP 8 throughout; full type hints; docstrings on all public functions

---

## Manual Steps Required (in order)

- [ ] Verify gate: SELECT COUNT(*) FROM rm_backtest_result — expect rows from M5-Spec-1 runs
- [ ] Verify gate: SELECT COUNT(*) FROM rm_correlation_matrix WHERE calc_date='2026-06-02' — expect ~28*28 rows
- [ ] Add jump-diffusion config to rm_scoring_config:
  - mc_jump_intensity_crypto = 5.0
  - mc_jump_mean_crypto = -0.04
  - mc_jump_std_crypto = 0.08
  - mc_jump_intensity_nvda = 3.0
  - mc_jump_mean_nvda = -0.02
  - mc_jump_std_nvda = 0.06
- [ ] python manage.py makemigrations risk
- [ ] python manage.py migrate --settings=portfolio_manager.settings_dev
- [ ] python manage.py run_montecarlo --entity portfolio --n 10000 --horizon 252 --date 2026-06-02 --seed 42 --settings=portfolio_manager.settings_dev
- [ ] Verify results in SSMS: SELECT portfolio_value_start_usd, pct5_outcome_usd, pct50_outcome_usd, pct95_outcome_usd, prob_loss_10pct, expected_shortfall_5pct_usd FROM rm_simulation_result ORDER BY calc_timestamp DESC
- [ ] Sanity check: prob_loss_10pct should be 10–30% for this portfolio (diversified but concentrated)
- [ ] python manage.py run_montecarlo --entity symbol --entity-id AAPL --n 10000 --horizon 252 --strategy [collar recommendation id] --seed 42 --settings=portfolio_manager.settings_dev
- [ ] Verify hedged vs unhedged: pct5_outcome_hedged_usd should be higher (less loss) than pct5_outcome_usd
- [ ] python manage.py run_stress_test --scenario 2022_rate_shock --date 2026-06-02 --settings=portfolio_manager.settings_dev
- [ ] Verify stress result: total loss should be approximately 15–25% of portfolio value
- [ ] python manage.py test risk.tests.test_monte_carlo --settings=portfolio_manager.settings_test
- [ ] git add -A && git commit -m "feat: M5-Spec-2 Monte Carlo + jump-diffusion + stress testing"
- [ ] git push origin feature/risk-m5-montecarlo

---

## Human Approval Required

Review the portfolio Monte Carlo result before proceeding to M6-Spec-2. The pct5 outcome (worst 5% of simulated years) should feel plausible — something in the range of 15–35% below the starting portfolio value for a portfolio with this much equity concentration. If the number seems too extreme or too benign, check the correlation matrix quality and the volatility inputs.

---

M5-Spec-2 | Portfolio Manager Phase B | June 2026 | tdwdev
