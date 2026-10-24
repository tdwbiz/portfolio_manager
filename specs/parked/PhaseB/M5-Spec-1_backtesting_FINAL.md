# Feature Spec: M5-Spec-1 — Backtesting Engine

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/risk-m5-backtesting

Architecture ref: PM_PhaseB_Architecture_Final_v2.0.md
Depends on: M4-Spec-1 fully implemented — rm_strategy_recommendation populated; rm_strategy_template and rm_instrument_type seeded; rm_market_price_history has at least 5 years of OHLCV for all held symbols

---

## Context

M5-Spec-1 answers the question: "Would this strategy have actually worked?" Before recommending a collar on AAPL or a trailing stop on VFV, we should be able to show TOM and SUSAN how those strategies performed during the 2022 rate shock, the 2020 COVID crash, and other relevant historical stress periods.

The backtesting engine applies strategy templates to historical price series and measures the hedged vs unhedged outcome — drawdown reduction, cost drag, net P&L impact, and hedge effectiveness ratio. It also splits results by regime period, so you can see how a strategy performed when the economy was in Q1 (growth) vs Q3 (recession).

This spec does NOT implement Monte Carlo or stress testing — that is M5-Spec-2. This spec is concerned only with replaying actual historical price data with a simulated strategy overlay.

**Key backtests required after implementation:**

| Symbol | Account | Strategy | Scenario | Why it matters |
|---|---|---|---|---|
| VFV | TAXABLE_TOM_SUSAN | trailing_stop (10%) | 2022 Rate Shock | $331K UGL at risk; would stop have protected it? |
| QQQ | TFSA_TOM | trailing_stop (12%) | 2022 Rate Shock | Tech concentration; TFSA so no tax drag |
| AAPL | IB_SUSAN | collar | 2022 Rate Shock | High-gain taxable stock; collar mechanics |
| VDY | TAXABLE_TOM_SUSAN | trailing_stop (8%) | 2020 COVID Crash | Dividend ETF; would dividend income offset hedge cost? |
| QQQ | TFSA_TOM | inverse_etf (PSQ) | 2022 Rate Shock | Nasdaq hedge via inverse ETF |
| NVDA | IB_TOM | protective_put | 2024 Tech Correction | High-vol semi stock; put effectiveness |

**Stack:**
- Python 3.12 / Django 5.1
- MSSQL 2025 (dev: PortfolioDEV)
- numpy, pandas for all time-series computation
- Project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. New Database Tables

#### 1.1 rm_historical_scenario

Named historical stress periods. Seed data provided in §6.

```python
class HistoricalScenario(models.Model):
    """
    A named historical market stress period.
    Defines the date range and benchmark return profile for the scenario.
    Used by both the backtesting engine and the stress test mode.
    """
    name = models.CharField(max_length=100, unique=True)
    # Machine-readable key used in management commands.
    # E.g. '2022_rate_shock', '2020_covid_crash', '2008_gfc', '2024_tech_correction'

    display_name = models.CharField(max_length=150)
    # Human-readable. E.g. '2022 Rate Shock (Jan–Oct 2022)'

    start_date = models.DateField()
    end_date = models.DateField()

    description = models.TextField()
    # Plain English description of the scenario: what happened, why it matters for this portfolio.

    asset_class_returns_json = models.TextField()
    # JSON object: approximate total return for each asset class over the scenario period.
    # Used for stress test mode when full price history is unavailable for a position.
    # E.g. {
    #   "etf_equity_us": -0.18,      # broad US equities down 18%
    #   "etf_equity_ca": -0.10,      # Canadian equities down 10%
    #   "common_stock_tech": -0.35,   # tech stocks down 35%
    #   "common_stock_semi": -0.40,   # semiconductors down 40%
    #   "crypto_etf": -0.60,          # crypto down 60%
    #   "cash_equiv": 0.02,           # cash equivalents up 2%
    #   "etf_bond": -0.15,            # bonds down 15% (rate shock)
    # }

    benchmark_spy_return = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # SPY total return over scenario period. For reference and display.
    benchmark_qqq_return = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    benchmark_vdy_return = models.DecimalField(max_digits=8, decimal_places=4, null=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_historical_scenario'
        ordering = ['-start_date']
```

#### 1.2 rm_backtest_run

One row per backtest execution. Captures the inputs.

```python
class BacktestRun(models.Model):
    """
    A single backtest run: one strategy applied to one position over one date range.
    """
    symbol = models.CharField(max_length=20)
    account_id = models.CharField(max_length=50)
    owner = models.CharField(max_length=50)

    strategy_template = models.ForeignKey(
        'StrategyTemplate', on_delete=models.PROTECT, related_name='backtest_runs'
    )
    scenario = models.ForeignKey(
        HistoricalScenario, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='backtest_runs'
    )
    # NULL if a custom date range was specified rather than a named scenario

    start_date = models.DateField()
    end_date = models.DateField()

    # --- Position parameters at start of backtest ---
    quantity = models.DecimalField(max_digits=12, decimal_places=4)
    # Quantity as of start_date from core_position_history (as-of query)
    cost_basis_per_unit_usd = models.DecimalField(max_digits=12, decimal_places=4)
    # Cost basis as of start_date; used to compute UGL at risk
    start_price_usd = models.DecimalField(max_digits=12, decimal_places=4)
    # Price at start_date from rm_market_price_history
    currency = models.CharField(max_length=3, default='USD')

    # --- Strategy parameters used ---
    strategy_params_json = models.TextField()
    # JSON: parameters used for this specific run.
    # E.g. trailing_stop: {"trail_pct": 0.10}
    # E.g. collar: {"put_strike_pct": 1.0, "call_strike_pct": 1.12, "expiry_months": 6}
    # E.g. protective_put: {"put_strike_pct": 1.0, "expiry_months": 6}
    # E.g. inverse_etf: {"hedge_ratio": 0.5, "etf_ticker": "PSQ"}

    # --- Metadata ---
    run_timestamp = models.DateTimeField(auto_now_add=True)
    run_by = models.CharField(max_length=100, blank=True)
    # Username or 'system' if triggered by management command

    class Meta:
        db_table = 'rm_backtest_run'
        indexes = [
            models.Index(fields=['symbol', 'account_id']),
            models.Index(fields=['strategy_template', 'scenario']),
            models.Index(fields=['run_timestamp']),
        ]
```

#### 1.3 rm_backtest_result

One row per backtest run — the output metrics.

```python
class BacktestResult(models.Model):
    """
    Outcome metrics for a completed backtest run.
    All monetary values in USD.
    """
    run = models.OneToOneField(
        BacktestRun, on_delete=models.CASCADE, related_name='result'
    )

    # --- Return metrics ---
    total_return_unhedged_pct = models.DecimalField(max_digits=10, decimal_places=4)
    # (end_price - start_price) / start_price. Negative in drawdown scenarios.
    total_return_hedged_pct = models.DecimalField(max_digits=10, decimal_places=4)
    # Net return including hedge payoff and hedge cost.
    return_improvement_pct = models.DecimalField(max_digits=10, decimal_places=4)
    # total_return_hedged_pct - total_return_unhedged_pct. Positive = hedge helped.

    # --- Drawdown metrics ---
    max_drawdown_unhedged = models.DecimalField(max_digits=8, decimal_places=4)
    # Maximum peak-to-trough drawdown of the unhedged position over the backtest period.
    # Stored as negative fraction. E.g. -0.34 = 34% drawdown.
    max_drawdown_hedged = models.DecimalField(max_digits=8, decimal_places=4)
    # Maximum drawdown of the hedged position (strategy payoff offsets some of the loss).
    drawdown_reduction_pct = models.DecimalField(max_digits=8, decimal_places=4)
    # abs(max_drawdown_unhedged) - abs(max_drawdown_hedged). Positive = hedge reduced drawdown.

    # --- Cost metrics ---
    hedge_cost_total_usd = models.DecimalField(max_digits=12, decimal_places=2)
    # Total cost of the hedge over the backtest period in USD.
    # For options: premium paid (or net premium for collar).
    # For trailing stop: commission if triggered, else 0.
    # For inverse ETF: estimated decay cost + commission.
    hedge_cost_pct_of_position = models.DecimalField(max_digits=8, decimal_places=4)
    # hedge_cost_total_usd / (start_price_usd * quantity)

    # --- Effectiveness ---
    hedge_effectiveness_ratio = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # drawdown_reduction_pct / hedge_cost_pct_of_position.
    # Interpretation: drawdown reduction per unit of hedge cost.
    # Higher is better. A ratio > 1 means each $1 of hedge cost reduced drawdown by more than $1.
    # NULL if hedge_cost is zero (free hedge — e.g. stop-loss never triggered).
    breakeven_annual_cost_pct = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # What annual cost (as % of position) would break even with the unhedged drawdown?
    # breakeven = abs(max_drawdown_unhedged - max_drawdown_hedged) / years_in_period
    # If the hedge costs less than this, it was worth it.

    # --- Strategy-specific outcomes ---
    stop_triggered = models.BooleanField(null=True)
    # For trailing_stop: True if the stop was triggered during the backtest period.
    stop_trigger_date = models.DateField(null=True, blank=True)
    # Date stop was triggered, if applicable.
    stop_trigger_price_usd = models.DecimalField(max_digits=12, decimal_places=4, null=True)
    # Price at which stop triggered.
    option_expired_worthless = models.BooleanField(null=True)
    # For protective_put/collar: True if put expired without being exercised.
    # Worthless expiry = full premium cost, no payoff. Still may have been worth it for peace of mind.
    option_payoff_usd = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    # For options: intrinsic value at expiry or at exercise. Gross payoff before premium cost.

    # --- Dollar outcomes ---
    position_value_start_usd = models.DecimalField(max_digits=15, decimal_places=2)
    # quantity * start_price_usd
    position_value_end_unhedged_usd = models.DecimalField(max_digits=15, decimal_places=2)
    # quantity * end_price_usd
    position_value_end_hedged_usd = models.DecimalField(max_digits=15, decimal_places=2)
    # position_value_end_unhedged + hedge payoff - hedge cost

    # --- Regime split ---
    regime_split_json = models.TextField(blank=True)
    # JSON array: backtest period split by detected regime from rm_regime_state.
    # E.g. [
    #   {"regime": "Q1", "start": "2022-01-03", "end": "2022-03-15",
    #    "return_unhedged": 0.02, "return_hedged": 0.01},
    #   {"regime": "Q3", "start": "2022-03-15", "end": "2022-10-31",
    #    "return_unhedged": -0.22, "return_hedged": -0.08}
    # ]
    # Empty string if no historical regime data available for the period.

    calc_timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_backtest_result'
```

---

### 2. Management Command

Create `risk/management/commands/run_backtest.py`.

```
python manage.py run_backtest \
    --symbol SYM \
    --account ACC \
    --strategy STRATEGY_CODE \
    --start YYYY-MM-DD \
    --end YYYY-MM-DD \
    [--scenario SCENARIO_NAME] \
    [--trail-pct 0.10] \
    [--hedge-ratio 0.5] \
    [--option-expiry-months 6]
```

- --symbol: held symbol to backtest (e.g. VFV)
- --account: account_id (e.g. TAXABLE_TOM_SUSAN)
- --strategy: strategy_template.code (e.g. trailing_stop_etf_taxable)
- --start / --end: date range for the backtest
- --scenario: optional named scenario (overrides start/end with scenario dates if provided)
- --trail-pct: trailing stop percentage (default 0.10)
- --hedge-ratio: inverse ETF hedge ratio (default 0.50)
- --option-expiry-months: option contract expiry in months (default 6)

**Execution order:**
```
1. Validate inputs: symbol in rm_security_universe, account in core_account,
   strategy in rm_strategy_template, price data available for date range
2. Load position as-of start_date from core_position_history
   (use vw_portfolio_as_of function)
3. Load price series for symbol from rm_market_price_history via DataSource
4. Dispatch to strategy-specific simulation function (see §3)
5. Compute all result metrics (see §4)
6. Load regime splits from rm_regime_state for the period (see §5)
7. Insert BacktestRun and BacktestResult records
8. Print summary report to stdout
```

---

### 3. Strategy Simulation Functions

Create `risk/services/backtest_engine.py`. All functions are pure Python — no ORM calls inside.

#### 3.1 Data Preparation

```python
def load_price_series(
    symbol: str,
    start_date: date,
    end_date: date,
    data_source: DataSource
) -> pd.DataFrame:
    """
    Load OHLCV DataFrame for the backtest period.
    Columns: date (index), open, high, low, close, adj_close, volume.
    Raises DataNotAvailableError if fewer than 10 trading days of data.
    """

def get_position_at_date(
    symbol: str,
    account_id: str,
    as_of_date: date
) -> PositionRecord | None:
    """
    Query core_position_history for open position as of as_of_date.
    Returns None if no position held at that date.
    """
```

#### 3.2 Trailing Stop Simulation

```python
def simulate_trailing_stop(
    prices: pd.DataFrame,
    trail_pct: float,
    quantity: float,
    cost_basis_per_unit_usd: float,
    commission_per_trade: float = 1.00
) -> dict:
    """
    Simulate a trailing stop-loss on a daily price series.

    Algorithm:
    1. Set initial stop = prices['close'].iloc[0] * (1 - trail_pct)
    2. For each trading day:
       a. If today's low <= current stop: TRIGGER — record exit at stop price
          (use stop price as exit; in reality could gap through, but this is standard assumption)
          position_value = stop_price * quantity
          stop_triggered = True; stop_trigger_date = today; break
       b. If today's close > high_water_mark:
          high_water_mark = today's close
          current_stop = high_water_mark * (1 - trail_pct)
    3. If stop never triggered: exit at end_date close price

    Returns dict:
    {
        'stop_triggered': bool,
        'stop_trigger_date': date | None,
        'stop_trigger_price_usd': float | None,
        'exit_price_usd': float,
        'hedge_cost_total_usd': float,  # commission only
        'hedge_payoff_daily': pd.Series,  # 0 until triggered, then position offset
    }

    Note: hedge_payoff_daily represents the economic benefit of the stop in dollar terms.
    Before trigger: 0 (stop not activated).
    On trigger day: (stop_trigger_price - current_price_without_stop) * quantity
    This captures: "without the stop, I would have held and lost X more dollars."
    """
```

#### 3.3 Protective Put Simulation (Simplified Delta Model)

```python
def simulate_protective_put(
    prices: pd.DataFrame,
    quantity: float,
    put_strike_pct: float = 1.0,   # ATM = 1.0; OTM = 0.95; ITM = 1.05
    expiry_months: int = 6,
    vol_annualised: float = 0.25,
    risk_free_rate: float = 0.045,
    commission_per_contract: float = 1.50
) -> dict:
    """
    Simulate a protective put using a simplified daily delta model.

    Simplification: rather than full Black-Scholes path simulation,
    use a delta-based approximation for the put's daily P&L contribution.

    Algorithm:
    1. Compute initial put premium using black_scholes_put() at start_date
    2. For each day:
       a. Compute days_to_expiry remaining
       b. Compute put delta using Black-Scholes: delta ≈ -N(-d1) where d1 uses current price
       c. Put daily P&L ≈ delta * (price_change) * quantity
          (approximation; ignores gamma and theta for simplicity — document this)
    3. At expiry (or end of backtest, whichever comes first):
       a. Compute put intrinsic value = max(0, strike - current_price)
       b. option_payoff_usd = intrinsic_value * quantity
       c. If intrinsic > 0: option expired in-the-money (exercised)
       d. Else: option expired worthless
    4. If backtest ends before expiry: use Black-Scholes to mark put to market at end_date

    Returns dict:
    {
        'put_premium_total_usd': float,    # Initial cost: premium_per_share * quantity
        'option_payoff_usd': float,        # Intrinsic value at expiry * quantity
        'option_expired_worthless': bool,
        'hedge_cost_total_usd': float,     # put_premium - option_payoff (net cost)
        'hedge_payoff_daily': pd.Series,   # Daily put P&L contribution
    }
    """
```

#### 3.4 Collar Simulation

```python
def simulate_collar(
    prices: pd.DataFrame,
    quantity: float,
    put_strike_pct: float = 1.0,
    call_strike_pct: float = 1.12,
    expiry_months: int = 6,
    vol_annualised: float = 0.25,
    risk_free_rate: float = 0.045,
    commission_per_contract: float = 1.50
) -> dict:
    """
    Simulate a collar strategy: long put + short call.

    The long put provides downside protection (same as simulate_protective_put).
    The short call caps upside at call_strike_pct * start_price.

    Upside cap simulation:
    - If price rises above call strike at expiry: position is called away at call_strike price
      (investor does not participate in gains above call strike)
    - Record: call_exercised = True, effective_exit_price = call_strike_price
    - This is a cost — the investor forfeits gains above the strike

    net_premium = put_premium - call_premium_received (may be near zero or slightly positive)

    Returns dict combining put and call simulations:
    {
        'put_premium_total_usd': float,
        'call_premium_received_usd': float,
        'net_premium_usd': float,
        'option_payoff_usd': float,
        'call_exercised': bool,
        'effective_exit_price_usd': float,
        'hedge_cost_total_usd': float,     # net_premium - option_payoff
        'hedge_payoff_daily': pd.Series,
    }
    """
```

#### 3.5 Inverse ETF Simulation

```python
def simulate_inverse_etf(
    symbol_prices: pd.DataFrame,
    hedge_ratio: float = 0.5,
    leverage_factor: float = 1.0,  # 1.0 for PSQ; 3.0 for SOXS
    annual_decay_rate: float = 0.05,
    quantity: float = 1.0,
    commission_per_trade: float = 1.00
) -> dict:
    """
    Simulate an inverse ETF hedge applied to a position.

    Daily hedge P&L:
    inverse_etf_return_today = -symbol_return_today * leverage_factor * (1 - daily_decay)
    where daily_decay = annual_decay_rate / 252

    hedge_notional = position_market_value_start * hedge_ratio
    hedge_payoff_today = inverse_etf_return_today * hedge_notional

    Important: for leveraged inverse ETFs (leverage_factor > 1), apply a volatility decay
    penalty to capture the daily rebalancing cost:
    daily_decay = annual_decay_rate / 252  (applies regardless of market direction)

    total_hedge_payoff = sum(hedge_payoff_daily)
    total_decay_cost = hedge_notional * annual_decay_rate * (days / 252)

    Returns dict:
    {
        'hedge_payoff_daily': pd.Series,
        'total_hedge_payoff_usd': float,
        'total_decay_cost_usd': float,
        'hedge_cost_total_usd': float,    # total_decay_cost + commission
        'commission_usd': float,
    }
    """
```

---

### 4. Result Metrics Computation

Create `risk/services/backtest_metrics.py`.

```python
def compute_backtest_metrics(
    prices: pd.DataFrame,
    hedge_payoff_daily: pd.Series,
    hedge_cost_total_usd: float,
    quantity: float,
    start_price_usd: float,
    strategy_specific_outcomes: dict
) -> dict:
    """
    Compute all result metrics from the simulation outputs.

    Inputs:
        prices: DataFrame with 'close' column, indexed by date
        hedge_payoff_daily: daily dollar P&L of hedge (positive = hedge gained value)
        hedge_cost_total_usd: total upfront and running cost of the hedge
        quantity: number of shares/units
        start_price_usd: price at backtest start

    Returns dict matching BacktestResult fields.
    """

def compute_max_drawdown_of_series(values: pd.Series) -> float:
    """
    Maximum peak-to-trough drawdown of a value series.
    Returns negative fraction. E.g. -0.22 = 22% drawdown.
    """

def build_regime_split(
    start_date: date,
    end_date: date,
    prices: pd.DataFrame,
    hedge_payoff_daily: pd.Series,
    quantity: float
) -> list[dict]:
    """
    Split backtest results by detected regime from rm_regime_state.

    Query rm_regime_state for all rows between start_date and end_date.
    For each contiguous regime period, compute:
        - return_unhedged: price return for that sub-period
        - return_hedged: price return + hedge contribution for that sub-period

    Returns list of dicts matching regime_split_json schema.
    Returns empty list if no regime data available for the period.
    """
```

---

### 5. Stress Test Mode

When full price history is unavailable (e.g. IBIT didn't exist in 2022), use the scenario's
`asset_class_returns_json` to apply a proportional shock to the current position.

```python
def run_stress_test_mode(
    symbol: str,
    account_id: str,
    scenario: HistoricalScenario,
    strategy_template: StrategyTemplate,
    current_position: PositionRecord,
    current_price_usd: float,
    strategy_params: dict
) -> dict:
    """
    Apply scenario benchmark returns to current position without full price history.

    1. Look up asset_class for this symbol from rm_security_universe
    2. Get benchmark return from scenario.asset_class_returns_json for that asset_class
    3. Compute simulated loss: current_mv * abs(benchmark_return)
    4. For trailing_stop: if benchmark_return < -trail_pct, stop triggered; loss = trail_pct * mv
    5. For protective_put: compute put payoff at simulated end price
       end_price_simulated = current_price * (1 + benchmark_return)
       put_payoff = max(0, put_strike - end_price_simulated) * quantity
    6. Return simplified result dict with _is_stress_test_mode=True flag

    Document clearly in output: "This result uses scenario benchmark returns,
    not actual price history for this symbol."
    """
```

---

### 6. Seed Data — Named Historical Scenarios

Create management command `seed_historical_scenarios` in `risk/management/commands/seed_historical_scenarios.py`.

```python
HISTORICAL_SCENARIOS = [
    {
        'name': '2022_rate_shock',
        'display_name': '2022 Rate Shock (Jan 3 – Oct 31, 2022)',
        'start_date': '2022-01-03',
        'end_date': '2022-10-31',
        'description': (
            'The Federal Reserve began its most aggressive rate-hiking cycle since 1980. '
            'Both equities and bonds sold off simultaneously — rare and damaging for diversified portfolios. '
            'Growth equities (QQQ, AAPL, NVDA) fell 30–50%. Bond ETFs fell 15–20%. '
            'Dividend ETFs (VDY) and Canadian equities were relatively defensive. '
            'This scenario is the primary test for tech concentration risk in this portfolio.'
        ),
        'asset_class_returns_json': {
            'etf_equity': -0.18,
            'etf_equity_ca': -0.06,
            'common_stock_tech': -0.38,
            'common_stock_semi': -0.42,
            'common_stock_financial': -0.15,
            'crypto_etf': -0.62,
            'cash_equiv': 0.03,
            'etf_bond': -0.16,
            'cef': -0.20,
        },
        'benchmark_spy_return': -0.175,
        'benchmark_qqq_return': -0.320,
        'benchmark_vdy_return': -0.042,
    },
    {
        'name': '2020_covid_crash',
        'display_name': '2020 COVID Crash (Feb 19 – Apr 6, 2020)',
        'start_date': '2020-02-19',
        'end_date': '2020-04-06',
        'description': (
            'Fastest bear market in history: S&P 500 fell 34% in 33 days. '
            'Liquidity crisis: nearly all assets sold off simultaneously including gold and bonds. '
            'VIX hit 82. Recovery was equally fast — full rebound by August 2020. '
            'This scenario tests rapid drawdown protection and stop-loss trigger behaviour. '
            'VDY fell approximately 35%; QQQ approximately 28%; individual stocks more volatile.'
        ),
        'asset_class_returns_json': {
            'etf_equity': -0.34,
            'etf_equity_ca': -0.37,
            'common_stock_tech': -0.28,
            'common_stock_semi': -0.32,
            'common_stock_financial': -0.45,
            'crypto_etf': -0.50,
            'cash_equiv': 0.01,
            'etf_bond': -0.05,
            'cef': -0.38,
        },
        'benchmark_spy_return': -0.340,
        'benchmark_qqq_return': -0.280,
        'benchmark_vdy_return': -0.350,
    },
    {
        'name': '2008_gfc',
        'display_name': '2008 Global Financial Crisis (Sep 1 – Mar 9, 2009)',
        'start_date': '2008-09-01',
        'end_date': '2009-03-09',
        'description': (
            'Systemic financial crisis: S&P 500 fell 57% peak to trough. '
            'Credit markets froze; correlations between all risk assets went to 1. '
            'The longest and deepest drawdown in recent history. '
            'Tests whether hedges that rely on low correlation (e.g. diversification) '
            'actually work in a correlation-breakdown event. '
            'Dividend payers (VDY equivalent) fell 40–50% due to dividend cuts.'
        ),
        'asset_class_returns_json': {
            'etf_equity': -0.45,
            'etf_equity_ca': -0.40,
            'common_stock_tech': -0.50,
            'common_stock_semi': -0.55,
            'common_stock_financial': -0.72,
            'crypto_etf': None,  # Crypto did not exist
            'cash_equiv': 0.02,
            'etf_bond': 0.05,  # Treasuries rallied
            'cef': -0.50,
        },
        'benchmark_spy_return': -0.450,
        'benchmark_qqq_return': -0.430,
        'benchmark_vdy_return': None,  # VDY did not exist; use etf_equity_ca proxy
    },
    {
        'name': '2024_tech_correction',
        'display_name': '2024 Semiconductor Correction (Jul 10 – Aug 5, 2024)',
        'start_date': '2024-07-10',
        'end_date': '2024-08-05',
        'description': (
            'Rapid rotation out of AI/semiconductor stocks. NVDA fell 30%+ from peak. '
            'SOXX and SMH fell 25%+. Nasdaq corrected ~15%. '
            'Broader market was more resilient; dividend ETFs held up well. '
            'This scenario tests the semiconductor concentration in SOXX, SMH, NVDA, DRAM. '
            'The speed of the move (weeks, not months) tests trailing stop trigger sensitivity.'
        ),
        'asset_class_returns_json': {
            'etf_equity': -0.08,
            'etf_equity_ca': -0.05,
            'common_stock_tech': -0.15,
            'common_stock_semi': -0.28,
            'common_stock_financial': -0.03,
            'crypto_etf': -0.22,
            'cash_equiv': 0.01,
            'etf_bond': 0.02,
            'cef': -0.08,
        },
        'benchmark_spy_return': -0.082,
        'benchmark_qqq_return': -0.148,
        'benchmark_vdy_return': -0.012,
    },
]
```

Seed command algorithm:
```
For each scenario in HISTORICAL_SCENARIOS:
    MERGE rm_historical_scenario ON name = :name
    WHEN MATCHED: UPDATE all fields
    WHEN NOT MATCHED: INSERT
Print: X scenarios seeded
```

---

### 7. Admin Registration

```python
@admin.register(HistoricalScenario)
class HistoricalScenarioAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'display_name', 'start_date', 'end_date',
        'benchmark_spy_return', 'benchmark_qqq_return', 'is_active'
    ]
    list_filter = ['is_active']
    search_fields = ['name', 'display_name']
    ordering = ['-start_date']
    readonly_fields = ['created_at']


@admin.register(BacktestRun)
class BacktestRunAdmin(admin.ModelAdmin):
    list_display = [
        'symbol', 'account_id', 'strategy_template', 'scenario',
        'start_date', 'end_date', 'run_timestamp'
    ]
    list_filter = ['strategy_template', 'scenario', 'symbol']
    search_fields = ['symbol', 'account_id']
    ordering = ['-run_timestamp']
    readonly_fields = [f.name for f in BacktestRun._meta.fields]


@admin.register(BacktestResult)
class BacktestResultAdmin(admin.ModelAdmin):
    list_display = [
        'run', 'total_return_unhedged_pct', 'total_return_hedged_pct',
        'max_drawdown_unhedged', 'max_drawdown_hedged',
        'hedge_cost_total_usd', 'hedge_effectiveness_ratio',
        'stop_triggered'
    ]
    ordering = ['-calc_timestamp']
    readonly_fields = [f.name for f in BacktestResult._meta.fields]
```

---

### 8. Tests

File: `risk/tests/test_backtesting.py`

```python
import numpy as np
import pandas as pd
from datetime import date, timedelta
from django.test import TestCase
from django.core.management import call_command
from risk.services.backtest_engine import (
    simulate_trailing_stop, simulate_protective_put,
    simulate_collar, simulate_inverse_etf
)
from risk.services.backtest_metrics import compute_max_drawdown_of_series


class TrailingStopTests(TestCase):

    def _make_prices(self, closes: list[float]) -> pd.DataFrame:
        dates = pd.date_range('2022-01-03', periods=len(closes), freq='B')
        return pd.DataFrame({
            'close': closes,
            'low': [c * 0.995 for c in closes],  # intraday low slightly below close
            'high': [c * 1.005 for c in closes],
        }, index=dates)

    def test_stop_triggers_when_price_drops_through(self):
        # Price rises then drops 15% — should trigger a 10% trailing stop
        closes = [100, 105, 110, 115, 120, 102, 95, 88]
        # High water mark = 120; 10% stop = 108; 102 < 108 → trigger
        prices = self._make_prices(closes)
        result = simulate_trailing_stop(prices, trail_pct=0.10, quantity=100,
                                        cost_basis_per_unit_usd=80.0)
        self.assertTrue(result['stop_triggered'])
        self.assertIsNotNone(result['stop_trigger_date'])
        self.assertAlmostEqual(result['stop_trigger_price_usd'], 108.0, delta=1.0)

    def test_stop_not_triggered_on_small_pullback(self):
        # Price drops 5% — should NOT trigger a 10% trailing stop
        closes = [100, 105, 110, 115, 112, 109]
        prices = self._make_prices(closes)
        result = simulate_trailing_stop(prices, trail_pct=0.10, quantity=100,
                                        cost_basis_per_unit_usd=80.0)
        self.assertFalse(result['stop_triggered'])

    def test_stop_trails_upward(self):
        # Price steadily rises — stop should follow and never trigger
        closes = [100, 102, 104, 106, 108, 110, 112]
        prices = self._make_prices(closes)
        result = simulate_trailing_stop(prices, trail_pct=0.10, quantity=100,
                                        cost_basis_per_unit_usd=80.0)
        self.assertFalse(result['stop_triggered'])
        # Exit at last close
        self.assertAlmostEqual(result['exit_price_usd'], 112.0, delta=0.1)

    def test_stop_exit_price_equals_stop_level_at_trigger(self):
        # Stop set 10% below high water mark of 120 = 108
        closes = [100, 120, 107]  # Day 3 low will be below 108
        prices = self._make_prices(closes)
        # Override low for day 3 to be clearly below stop
        prices.loc[prices.index[2], 'low'] = 105.0
        result = simulate_trailing_stop(prices, trail_pct=0.10, quantity=100,
                                        cost_basis_per_unit_usd=80.0)
        self.assertTrue(result['stop_triggered'])
        self.assertAlmostEqual(result['stop_trigger_price_usd'], 108.0, delta=0.5)

    def test_hedge_cost_is_commission_only(self):
        # Trailing stop has no premium cost — only commission on execution
        closes = [100, 90, 80]  # Drop triggers stop
        prices = self._make_prices(closes)
        prices.loc[prices.index[1], 'low'] = 85.0  # Force trigger
        result = simulate_trailing_stop(prices, trail_pct=0.10, quantity=100,
                                        cost_basis_per_unit_usd=80.0,
                                        commission_per_trade=1.00)
        self.assertEqual(result['hedge_cost_total_usd'], 1.00)  # Commission only


class ProtectivePutTests(TestCase):

    def test_put_expires_worthless_when_price_rises(self):
        # Price rises from 100 to 115 — ATM put expires worthless
        dates = pd.date_range('2022-01-03', periods=126, freq='B')  # ~6 months
        closes = [100 + i * 0.12 for i in range(126)]  # Steady rise to ~115
        prices = pd.DataFrame({'close': closes}, index=dates)
        result = simulate_protective_put(
            prices, quantity=100, put_strike_pct=1.0,
            expiry_months=6, vol_annualised=0.20, risk_free_rate=0.045
        )
        self.assertTrue(result['option_expired_worthless'])
        self.assertEqual(result['option_payoff_usd'], 0.0)
        # Hedge cost = full premium paid
        self.assertGreater(result['hedge_cost_total_usd'], 0)

    def test_put_pays_off_when_price_falls_below_strike(self):
        # Price falls from 100 to 80 — ATM put pays off 20 per share
        dates = pd.date_range('2022-01-03', periods=126, freq='B')
        closes = [100 - i * 0.16 for i in range(126)]  # Falls to ~80
        prices = pd.DataFrame({'close': closes}, index=dates)
        result = simulate_protective_put(
            prices, quantity=100, put_strike_pct=1.0,
            expiry_months=6, vol_annualised=0.20, risk_free_rate=0.045
        )
        self.assertFalse(result['option_expired_worthless'])
        # Put payoff ≈ (100 - 80) * 100 = $2000
        self.assertAlmostEqual(result['option_payoff_usd'], 2000, delta=200)

    def test_collar_costs_less_than_standalone_put(self):
        dates = pd.date_range('2022-01-03', periods=126, freq='B')
        closes = [100.0] * 126  # Flat prices for simplicity
        prices = pd.DataFrame({'close': closes}, index=dates)
        put_result = simulate_protective_put(
            prices, quantity=100, put_strike_pct=1.0,
            expiry_months=6, vol_annualised=0.25, risk_free_rate=0.045
        )
        collar_result = simulate_collar(
            prices, quantity=100, put_strike_pct=1.0, call_strike_pct=1.12,
            expiry_months=6, vol_annualised=0.25, risk_free_rate=0.045
        )
        # Collar net premium should be less than standalone put premium
        # (call premium received offsets put premium paid)
        self.assertLess(
            collar_result['net_premium_usd'],
            put_result['put_premium_total_usd']
        )


class InverseETFTests(TestCase):

    def test_inverse_etf_gains_when_underlying_falls(self):
        # Symbol falls 20% — 1x inverse ETF should gain approximately 20%
        dates = pd.date_range('2022-01-03', periods=50, freq='B')
        closes = [100 * (1 - 0.004 * i) for i in range(50)]  # ~20% decline over period
        prices = pd.DataFrame({'close': closes}, index=dates)
        result = simulate_inverse_etf(
            prices, hedge_ratio=0.5, leverage_factor=1.0,
            annual_decay_rate=0.01, quantity=100  # Low decay for 1x
        )
        self.assertGreater(result['total_hedge_payoff_usd'], 0)

    def test_leveraged_inverse_has_higher_decay(self):
        # 3x inverse (SOXS) should show higher decay cost than 1x (PSQ)
        dates = pd.date_range('2022-01-03', periods=30, freq='B')
        closes = [100.0] * 30  # Flat — no directional gain, only decay
        prices = pd.DataFrame({'close': closes}, index=dates)
        one_x = simulate_inverse_etf(prices, hedge_ratio=0.5, leverage_factor=1.0,
                                      annual_decay_rate=0.01, quantity=100)
        three_x = simulate_inverse_etf(prices, hedge_ratio=0.5, leverage_factor=3.0,
                                        annual_decay_rate=0.05, quantity=100)
        self.assertGreater(
            abs(three_x['total_decay_cost_usd']),
            abs(one_x['total_decay_cost_usd'])
        )


class DrawdownTests(TestCase):

    def test_max_drawdown_known_sequence(self):
        # Values: 100, 120, 80, 110. Peak=120, Trough=80. DD=(80-120)/120 = -33.3%
        values = pd.Series([100, 120, 80, 110])
        result = compute_max_drawdown_of_series(values)
        self.assertAlmostEqual(result, -0.3333, places=3)

    def test_all_rising_has_zero_drawdown(self):
        values = pd.Series([100, 110, 120, 130])
        result = compute_max_drawdown_of_series(values)
        self.assertAlmostEqual(result, 0.0, places=6)


class HedgeEffectivenessTests(TestCase):

    def test_effectiveness_ratio_positive_when_hedge_helped(self):
        # Drawdown reduced from 20% to 10%; cost 2% of position
        # Effectiveness = (0.20 - 0.10) / 0.02 = 5.0 — hedge was 5x its cost
        from risk.services.backtest_metrics import compute_backtest_metrics
        # Set up minimal inputs for metric computation
        dates = pd.date_range('2022-01-03', periods=50, freq='B')
        closes = [100 * (1 - 0.004 * i) for i in range(50)]  # Down ~20%
        prices = pd.DataFrame({'close': closes}, index=dates)
        hedge_payoff = pd.Series([50.0] * 50, index=dates)  # Constant hedge gain
        result = compute_backtest_metrics(
            prices=prices,
            hedge_payoff_daily=hedge_payoff,
            hedge_cost_total_usd=1000.0,
            quantity=100,
            start_price_usd=100.0,
            strategy_specific_outcomes={'stop_triggered': False, 'option_expired_worthless': None}
        )
        self.assertGreater(result['hedge_effectiveness_ratio'], 0)

    def test_return_improvement_positive_when_hedge_better(self):
        from risk.services.backtest_metrics import compute_backtest_metrics
        dates = pd.date_range('2022-01-03', periods=20, freq='B')
        closes = [100, 98, 95, 90, 85, 82, 80, 79, 78, 77,
                  76, 75, 74, 73, 72, 71, 70, 69, 68, 67]
        prices = pd.DataFrame({'close': closes}, index=dates)
        hedge_payoff = pd.Series([30.0] * 20, index=dates)  # Hedge gains $30/day
        result = compute_backtest_metrics(prices, hedge_payoff, 200.0, 100, 100.0, {})
        # Hedged return should be better (less negative) than unhedged
        self.assertGreater(
            result['total_return_hedged_pct'],
            result['total_return_unhedged_pct']
        )


class ScenarioSeedTests(TestCase):

    def test_all_four_scenarios_seeded(self):
        call_command('seed_historical_scenarios', verbosity=0)
        from risk.models import HistoricalScenario
        count = HistoricalScenario.objects.filter(is_active=True).count()
        self.assertEqual(count, 4)

    def test_scenarios_have_valid_date_ranges(self):
        call_command('seed_historical_scenarios', verbosity=0)
        from risk.models import HistoricalScenario
        for s in HistoricalScenario.objects.all():
            self.assertLess(s.start_date, s.end_date)

    def test_seed_is_idempotent(self):
        call_command('seed_historical_scenarios', verbosity=0)
        call_command('seed_historical_scenarios', verbosity=0)
        from risk.models import HistoricalScenario
        self.assertEqual(HistoricalScenario.objects.count(), 4)


class IntegrationTests(TestCase):

    def test_run_backtest_vfv_trailing_stop_2022(self):
        """
        Seed VFV price data for 2022, seed a position record.
        Run: python manage.py run_backtest --symbol VFV --account TAXABLE_TOM_SUSAN
             --strategy trailing_stop_etf_taxable --scenario 2022_rate_shock
        Assert BacktestRun and BacktestResult rows created.
        Assert stop_triggered = True (VFV did fall more than 10% in 2022).
        Assert hedge_effectiveness_ratio > 0.
        """
        pass  # Implement with test fixtures seeding actual VFV price history

    def test_run_backtest_aapl_collar_2022(self):
        """
        Seed AAPL price data for 2022.
        Run collar backtest.
        Assert: collar reduced drawdown vs unhedged.
        Assert: net_premium_usd < protective_put_premium (collar is cheaper).
        """
        pass

    def test_stress_test_mode_used_when_no_price_history(self):
        """
        Run backtest for IBIT with 2008_gfc scenario (IBIT didn't exist in 2008).
        Assert: result uses stress test mode (asset_class_returns applied).
        Assert: result dict contains _is_stress_test_mode=True.
        """
        pass
```

Run with: python manage.py test risk.tests.test_backtesting --settings=portfolio_manager.settings_test

---

## Constraints

- DO NOT implement Monte Carlo or forward-looking simulation — that is M5-Spec-2
- DO NOT implement UI views — that is M6-Spec-2
- DO NOT call yFinance directly — all price data via DataSource from M0-Spec-2
- DO NOT push to git (manual step)
- All monetary values in USD; FX conversion via rm_fx_rates when needed for CAD positions
- Option pricing uses Black-Scholes from risk/services/option_pricing.py (created in M4-Spec-1) — do not re-implement
- Backtest results are append-only — no update or delete of historical runs
- PEP 8 throughout; full type hints; docstrings on all public functions

---

## Manual Steps Required (in order)

- [ ] Verify gate: SELECT COUNT(*) FROM rm_strategy_recommendation WHERE calc_date='2026-06-02' — expect rows for all held positions at Medium urgency or above
- [ ] Verify gate: SELECT COUNT(*) FROM rm_market_price_history WHERE symbol='VFV' AND date >= '2022-01-01' — expect 200+ rows
- [ ] python manage.py makemigrations risk
- [ ] python manage.py migrate --settings=portfolio_manager.settings_dev
- [ ] python manage.py seed_historical_scenarios --settings=portfolio_manager.settings_dev
- [ ] Verify in SSMS: SELECT name, start_date, end_date, benchmark_spy_return FROM rm_historical_scenario ORDER BY start_date
- [ ] Run primary backtest: python manage.py run_backtest --symbol VFV --account TAXABLE_TOM_SUSAN --strategy trailing_stop_etf_taxable --scenario 2022_rate_shock --settings=portfolio_manager.settings_dev
- [ ] Run primary backtest: python manage.py run_backtest --symbol QQQ --account TFSA_TOM --strategy trailing_stop_registered --scenario 2022_rate_shock --settings=portfolio_manager.settings_dev
- [ ] Run primary backtest: python manage.py run_backtest --symbol AAPL --account IB_SUSAN --strategy collar_high_gain_taxable_stock --scenario 2022_rate_shock --settings=portfolio_manager.settings_dev
- [ ] Verify results in SSMS: SELECT r.symbol, r.account_id, b.total_return_unhedged_pct, b.total_return_hedged_pct, b.max_drawdown_unhedged, b.max_drawdown_hedged, b.hedge_effectiveness_ratio, b.stop_triggered FROM rm_backtest_run r JOIN rm_backtest_result b ON b.run_id = r.id
- [ ] Sanity checks:
  - VFV 2022 unhedged return: expect approximately -17% to -22%
  - VFV 2022 hedged (trailing stop 10%): if stop triggered, max loss ≈ 10%; effectiveness > 0
  - AAPL collar: drawdown_hedged should be meaningfully less than drawdown_unhedged
- [ ] python manage.py test risk.tests.test_backtesting --settings=portfolio_manager.settings_test
- [ ] git add -A && git commit -m "feat: M5-Spec-1 backtesting engine + historical scenarios"
- [ ] git push origin feature/risk-m5-backtesting

---

## Human Approval Required

Review backtest results for VFV and AAPL before proceeding to M5-Spec-2. Confirm that:
1. VFV unhedged drawdown in 2022 rate shock is plausible (~-18% to -25%)
2. The trailing stop would have triggered and capped the loss around the trail percentage
3. The AAPL collar shows a meaningful drawdown reduction at a reasonable cost

---

M5-Spec-1 | Portfolio Manager Phase B | June 2026 | tdwdev
