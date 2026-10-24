# Feature Spec: M2-Spec-1 — Regime Detection & Market Condition Technical Indicators

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/risk-m2-regime

Architecture ref: PM_PhaseB_Architecture_Final_v2.0.md
Depends on: M0-Spec-2 fully implemented — rm_economic_indicators populated with vix_close, yield_10y, yield_2y, yield_curve_slope; rm_market_price_history populated with full OHLCV for all held symbols

---

## Context

This spec implements two parallel detection systems that feed the urgency scoring engine in M2-Spec-2:

1. Regime detection: classifies the macroeconomic environment into one of four quadrants using economic indicator data. The regime tells the scoring engine how to adjust risk thresholds — a declining dividend ETF in Q1 (growth) is less concerning than the same ETF in Q3 (deflation/recession).

2. Market condition detection: computes technical indicators per symbol from OHLCV data. This is why M0 needed full OHLCV in rm_market_price_history rather than just close prices from core_historicalprice — RSI, MACD, Bollinger Bands, and OBV all require open/high/low/volume data.

Neither system produces an urgency score directly — they produce structured state that M2-Spec-2 reads as inputs to the scoring formula.

**Current macro context (June 2026) — for verification reference:**
- US yield curve: approximately flat to mildly inverted (10y ~4.4%, 2y ~4.8% as of mid-2026)
- VIX: approximately 15–20 range (moderate risk-off)
- Regime expectation: likely Q2/Q3 boundary (slowing growth, moderating inflation)
- The detected regime on first run should be plausible against this context

**Current technical context — for verification reference:**
- VDY: stable dividend ETF, likely in mild correction from 2025 highs
- QQQ: Nasdaq-100, high momentum — check for Golden/Death Cross status
- IBIT: crypto ETF, high volatility — RSI will be more extreme than equity positions

**Stack:**
- Python 3.12 / Django 5.1
- MSSQL 2025 (dev: PortfolioDEV)
- numpy, pandas for all indicator computation
- Project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. New Database Tables

#### 1.1 rm_regime_state

One row per calculation date. Records the detected macroeconomic regime.

```python
class RegimeState(models.Model):
    """
    Detected macroeconomic regime at each daily calculation.
    Four-quadrant model: growth vs contraction, inflation vs deflation.
    """
    calc_date = models.DateField(unique=True)

    quadrant = models.CharField(max_length=2)
    # Q1: Growth + Low Inflation (risk-on, equities supported)
    # Q2: Growth + High Inflation (reflation; commodities, real assets favoured)
    # Q3: Contraction + Low Inflation (deflation/recession; defensive positioning)
    # Q4: Contraction + High Inflation (stagflation; most adverse for equities and bonds)

    confidence_score = models.DecimalField(max_digits=5, decimal_places=2)
    # 0.0 to 100.0. Probability gap between highest and second-highest quadrant.
    # High confidence (>60): regime is clear. Low (<30): transitional, treat with caution.

    transition_flag = models.BooleanField(default=False)
    # True if probability of quadrant change exceeds transition_threshold_pct from rm_scoring_config
    # Signals to M2-Spec-2 to widen urgency bands and reduce conviction in score

    # Individual indicator scores (each 0–100, where 100 = most inflationary/growth)
    yield_curve_score = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    # Derived from yield_curve_slope. Positive slope = growth (higher score). Inverted = contraction.
    vix_score = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    # VIX < 15 = low fear (high score). VIX > 30 = high fear (low score).
    cpi_score = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    # CPI YoY trend. Not yet available via free API — NULL until FRED integration.
    pmi_score = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    # PMI composite. Not yet available — NULL until FRED integration.
    gdp_score = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    # GDP growth QoQ. Not yet available — NULL until FRED integration.

    # Quadrant probability distribution (must sum to 1.0)
    prob_q1 = models.DecimalField(max_digits=5, decimal_places=4)
    prob_q2 = models.DecimalField(max_digits=5, decimal_places=4)
    prob_q3 = models.DecimalField(max_digits=5, decimal_places=4)
    prob_q4 = models.DecimalField(max_digits=5, decimal_places=4)

    indicator_snapshot_json = models.TextField()
    # JSON blob: raw indicator values used for this calculation.
    # {yield_curve_slope: 0.23, vix_close: 18.4, cpi_yoy: null, ...}
    # Stored for M8 audit trail.

    calc_timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_regime_state'
        indexes = [models.Index(fields=['calc_date'])]
```

#### 1.2 rm_market_condition

One row per symbol per calculation date. Records all technical indicator values.

```python
class MarketCondition(models.Model):
    """
    Technical indicator values per symbol per calculation date.
    Computed from rm_market_price_history OHLCV.
    """
    symbol = models.CharField(max_length=20)
    calc_date = models.DateField()

    # --- Trend: Moving Averages ---
    sma_50 = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    sma_200 = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    price_vs_sma50_pct = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # (current_price - sma_50) / sma_50 * 100. Positive = above MA.
    price_vs_sma200_pct = models.DecimalField(max_digits=8, decimal_places=4, null=True)

    # --- Moving Average Crossover ---
    crossover_type = models.CharField(max_length=10, blank=True)
    # 'golden': 50d crossed above 200d (bullish signal)
    # 'death': 50d crossed below 200d (bearish signal)
    # 'none': no recent crossover
    crossover_date = models.DateField(null=True, blank=True)
    # Date of the most recent crossover event, or NULL if none in last 252 days

    # --- Momentum: RSI ---
    rsi_14 = models.DecimalField(max_digits=6, decimal_places=2, null=True)
    # Wilder's smoothed RSI, 14-period. Range 0–100.
    # >70: overbought. <30: oversold.

    # --- Momentum: MACD ---
    macd_line = models.DecimalField(max_digits=12, decimal_places=6, null=True)
    # 12-period EMA minus 26-period EMA of daily close
    macd_signal = models.DecimalField(max_digits=12, decimal_places=6, null=True)
    # 9-period EMA of macd_line
    macd_histogram = models.DecimalField(max_digits=12, decimal_places=6, null=True)
    # macd_line - macd_signal. Positive = bullish momentum. Negative = bearish.
    macd_crossover = models.CharField(max_length=10, blank=True)
    # 'bullish': histogram crossed above zero today
    # 'bearish': histogram crossed below zero today
    # 'none': no crossover today

    # --- Volatility: Bollinger Bands ---
    bb_upper = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    bb_middle = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    # 20-day SMA
    bb_lower = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    bb_bandwidth = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # (bb_upper - bb_lower) / bb_middle. Measures volatility expansion.
    bb_pct_b = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # (price - bb_lower) / (bb_upper - bb_lower). 0=at lower, 1=at upper, 0.5=at middle.

    # --- Volume: On-Balance Volume ---
    obv = models.BigIntegerField(null=True)
    # Cumulative OBV from earliest available date in rm_market_price_history.
    # Absolute value is meaningless — only trend matters.
    obv_trend = models.CharField(max_length=10, blank=True)
    # 'rising': OBV 20d SMA above OBV 50d SMA (volume confirming price)
    # 'falling': OBV 20d SMA below OBV 50d SMA (volume diverging from price)
    # 'neutral': flat

    # --- Market Phase Classification ---
    market_phase = models.CharField(max_length=15)
    # 'uptrend':    price > sma_200
    # 'correction': price < (52w_high * 0.90) and price > (52w_high * 0.80)
    # 'bear':       price < (52w_high * 0.80)
    # 'overbought': rsi_14 > 70 (can combine with other phases)
    # 'oversold':   rsi_14 < 30 (can combine with other phases)
    # Priority order: bear > correction > overbought > oversold > uptrend

    high_52w = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    # Highest close in last 252 trading days from rm_market_price_history
    low_52w = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    pct_from_52w_high = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # (current_price - high_52w) / high_52w * 100. Negative if below 52w high.

    price_rows_used = models.IntegerField(default=0)
    calc_timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_market_condition'
        unique_together = [('symbol', 'calc_date')]
        indexes = [
            models.Index(fields=['symbol', 'calc_date']),
            models.Index(fields=['calc_date']),
            models.Index(fields=['market_phase', 'calc_date']),
        ]
```

---

### 2. Management Commands

#### 2.1 calc_regime

```
python manage.py calc_regime [--date YYYY-MM-DD]
```

Reads rm_economic_indicators, detects current regime quadrant, writes one row to rm_regime_state.

**Algorithm:**

```
1. Load indicators from rm_economic_indicators for dates up to --date:
   - yield_curve_slope: most recent value
   - vix_close: most recent value
   - cpi_yoy: most recent value (may be NULL if FRED not integrated)
   - pmi_composite: most recent value (may be NULL)
   - gdp_growth_qoq: most recent value (may be NULL)

2. Score each available indicator (see §3.1)

3. Compute quadrant probabilities (see §3.2)

4. Detect transition flag:
   - Load previous rm_regime_state row (prior calc_date)
   - If previous quadrant != current top quadrant: check probability shift
   - If shift > transition_threshold_pct (from rm_scoring_config, default 15): set transition_flag=True

5. Build indicator_snapshot_json with raw values

6. UPSERT into rm_regime_state
```

#### 2.2 calc_market_conditions

```
python manage.py calc_market_conditions [--date YYYY-MM-DD] [--symbol SYM]
```

Reads rm_market_price_history OHLCV, computes all technical indicators for each held symbol.

**Algorithm:**

```
1. Resolve symbols: all is_held=True from rm_security_universe, or --symbol
2. For each symbol:
   a. Load all available OHLCV from rm_market_price_history via DataSource
   b. Minimum required: 200 rows for SMA-200. If fewer available: compute what is possible,
      set remaining fields to NULL, log a warning.
   c. Compute all indicators (see §3.3 through §3.8)
   d. Classify market_phase (see §3.9)
   e. UPSERT into rm_market_condition
3. Print summary: symbols processed, any skipped
```

**Mutual funds and cash equivalents (RBF2010, RBF2014, SGOV, ICSH):**
- No OHLCV available
- Set all indicator columns to NULL
- Set market_phase = 'uptrend' (cash equivalents do not have meaningful phases)
- Log INFO: "Symbol RBF2010: no price data for technical indicators — setting NULL"

---

### 3. Indicator Computation Details

All functions in risk/services/regime_engine.py and risk/services/technical_indicators.py.
Pure Python — no ORM calls inside functions.

#### 3.1 Economic Indicator Scoring

Each indicator is scored 0–100, where higher = more inflationary/growth-oriented.

```python
def score_yield_curve(slope: float | None) -> float | None:
    """
    Score the yield curve slope (10y minus 2y, in percentage points).
    Positive slope = growth/normal = higher score.
    Negative slope (inversion) = contraction signal = lower score.

    Breakpoints:
    slope >= +1.50: score = 85  (strongly normal, growth signal)
    slope >= +0.50: score = 65
    slope >= +0.00: score = 50  (flat)
    slope >= -0.50: score = 35  (mildly inverted)
    slope  < -0.50: score = 15  (deeply inverted, recession signal)

    Returns None if slope is None.
    """

def score_vix(vix: float | None) -> float | None:
    """
    Score the VIX level. Lower VIX = calmer markets = higher growth score.
    Note: this is a growth score, not a fear score.

    VIX < 12:  score = 85  (complacency, risk-on)
    VIX < 18:  score = 65
    VIX < 25:  score = 45
    VIX < 35:  score = 25  (elevated fear)
    VIX >= 35: score = 10  (crisis-level fear)

    Returns None if vix is None.
    """

def score_cpi(cpi_yoy: float | None) -> float | None:
    """
    Score CPI YoY. Higher CPI = higher inflation = lower score (bad for Q1/Q3).
    Score is the INFLATION dimension, inverted for quadrant mapping.

    cpi_yoy < 1.0:  score = 20  (deflation risk)
    cpi_yoy < 2.5:  score = 40  (target range)
    cpi_yoy < 4.0:  score = 60  (moderate)
    cpi_yoy < 6.0:  score = 80  (elevated)
    cpi_yoy >= 6.0: score = 95  (high inflation)

    Returns None if cpi_yoy is None.
    """

def score_pmi(pmi: float | None) -> float | None:
    """
    Score PMI composite. >50 = expansion = higher growth score.

    pmi >= 57: score = 85
    pmi >= 52: score = 65
    pmi >= 50: score = 50  (neutral)
    pmi >= 47: score = 35
    pmi  < 47: score = 15  (contraction)

    Returns None if pmi is None.
    """

def score_gdp(gdp_qoq: float | None) -> float | None:
    """
    Score GDP QoQ growth rate (annualised %).

    gdp_qoq >= 3.0: score = 80
    gdp_qoq >= 1.5: score = 60
    gdp_qoq >= 0.0: score = 45
    gdp_qoq  < 0.0: score = 20  (contraction)

    Returns None if gdp_qoq is None.
    """
```

#### 3.2 Quadrant Probability Calculation

```python
def compute_quadrant_probabilities(
    indicator_scores: dict[str, float | None],
    weights: dict[str, float]
) -> tuple[dict[str, float], float, str]:
    """
    Map indicator scores to quadrant probabilities.

    indicator_scores: {indicator_name: score_0_to_100}
    weights: from rm_scoring_config keys:
        regime_weight_yield_curve (default 0.35)
        regime_weight_vix         (default 0.25)
        regime_weight_cpi         (default 0.20)
        regime_weight_pmi         (default 0.10)
        regime_weight_gdp         (default 0.10)

    If an indicator is NULL: redistribute its weight proportionally to available indicators.
    This handles the current state where CPI/PMI/GDP are not yet available from FRED.

    Quadrant mapping logic:
    Each quadrant is characterised by two dimensions:
        Growth dimension:    yield_curve_score, vix_score, pmi_score, gdp_score
        Inflation dimension: cpi_score

    growth_score  = weighted average of growth-dimension indicators
    inflate_score = cpi_score (or 50 if NULL — neutral assumption)

    Quadrant probabilities use a softmax-like distribution:
    Q1 probability ∝ growth_score  * (100 - inflate_score)  [high growth, low inflation]
    Q2 probability ∝ growth_score  * inflate_score           [high growth, high inflation]
    Q3 probability ∝ (100 - growth_score) * (100 - inflate_score)  [low growth, low inflation]
    Q4 probability ∝ (100 - growth_score) * inflate_score    [low growth, high inflation]

    Normalise to sum to 1.0.

    Returns:
        probs: {'Q1': float, 'Q2': float, 'Q3': float, 'Q4': float}
        confidence: float (0–100) — difference between top and second probability * 100
        quadrant: str — the highest-probability quadrant ('Q1', 'Q2', 'Q3', or 'Q4')
    """
```

#### 3.3 Simple Moving Averages

```python
def sma(prices: pd.Series, window: int) -> pd.Series:
    """
    Simple moving average. prices.rolling(window).mean().
    Returns series of same length with NaN for first (window-1) values.
    """

def sma_crossover(sma_fast: pd.Series, sma_slow: pd.Series) -> tuple[str, date | None]:
    """
    Detect most recent SMA crossover in the last 252 days.

    Returns (crossover_type, crossover_date).
    crossover_type: 'golden' | 'death' | 'none'
    crossover_date: date of the crossover event, or None.

    Algorithm:
    1. Compute sign(sma_fast - sma_slow) each day
    2. Find most recent sign change in last 252 days
    3. If sign went negative to positive: 'golden'
    4. If sign went positive to negative: 'death'
    5. If no sign change in last 252 days: 'none'
    """
```

#### 3.4 RSI (Wilder's Smoothed Method)

```python
def rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """
    Wilder's RSI. Uses exponential smoothing with alpha = 1/period.
    Standard formula — NOT simple moving average of gains/losses.

    Algorithm:
    1. Compute daily price changes: delta = prices.diff()
    2. Separate gains (delta > 0) and losses (abs(delta) where delta < 0)
    3. Initial avg_gain = mean(gains[:period]), initial avg_loss = mean(losses[:period])
    4. For each subsequent day:
       avg_gain = (avg_gain * (period-1) + gain) / period
       avg_loss = (avg_loss * (period-1) + loss) / period
    5. RS = avg_gain / avg_loss
    6. RSI = 100 - (100 / (1 + RS))

    Requires minimum period + 1 data points.
    Returns pd.Series. First (period) values are NaN.
    """
```

#### 3.5 MACD

```python
def macd(prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD indicator using exponential moving averages.

    ema_fast   = prices.ewm(span=fast, adjust=False).mean()
    ema_slow   = prices.ewm(span=slow, adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    macd_signal = macd_line.ewm(span=signal, adjust=False).mean()
    histogram  = macd_line - macd_signal

    Returns (macd_line, macd_signal, histogram) — all pd.Series.
    """

def macd_crossover_today(histogram: pd.Series) -> str:
    """
    Detect if a MACD crossover occurred on the most recent day.

    'bullish': histogram[-2] < 0 and histogram[-1] >= 0 (crossed above zero)
    'bearish': histogram[-2] > 0 and histogram[-1] <= 0 (crossed below zero)
    'none': no crossover today
    """
```

#### 3.6 Bollinger Bands

```python
def bollinger_bands(prices: pd.Series, window: int = 20, num_std: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    """
    Bollinger Bands with standard deviation bands.

    middle = prices.rolling(window).mean()  (20-day SMA)
    std    = prices.rolling(window).std()
    upper  = middle + num_std * std
    lower  = middle - num_std * std
    bandwidth = (upper - lower) / middle
    pct_b  = (prices - lower) / (upper - lower)

    Returns (upper, middle, lower, bandwidth, pct_b) — all pd.Series.
    """
```

#### 3.7 On-Balance Volume

```python
def on_balance_volume(closes: pd.Series, volumes: pd.Series) -> pd.Series:
    """
    OBV = cumulative sum of signed volume.

    If close[t] > close[t-1]: OBV[t] = OBV[t-1] + volume[t]
    If close[t] < close[t-1]: OBV[t] = OBV[t-1] - volume[t]
    If close[t] == close[t-1]: OBV[t] = OBV[t-1]

    Returns pd.Series. Starts at volume[0] on day 0.
    Absolute value is arbitrary — only direction matters.
    """

def obv_trend(obv: pd.Series, fast_window: int = 20, slow_window: int = 50) -> str:
    """
    Classify OBV trend using two SMA crossover.

    obv_sma_fast = obv.rolling(fast_window).mean()
    obv_sma_slow = obv.rolling(slow_window).mean()

    'rising':  obv_sma_fast[-1] > obv_sma_slow[-1]
    'falling': obv_sma_fast[-1] < obv_sma_slow[-1]
    'neutral': abs difference < 0.5% of abs(obv_sma_slow[-1])
    """
```

#### 3.8 52-Week High/Low and Phase Classification

```python
def fifty_two_week_range(prices: pd.Series) -> tuple[float, float, float]:
    """
    Compute 52-week high, low, and current distance from high.

    window = last 252 prices
    high_52w = max(window)
    low_52w  = min(window)
    pct_from_high = (prices[-1] - high_52w) / high_52w * 100  (negative if below high)

    Returns (high_52w, low_52w, pct_from_high).
    """
```

#### 3.9 Market Phase Classification

```python
def classify_market_phase(
    current_price: float,
    sma_200: float | None,
    pct_from_52w_high: float,
    rsi: float | None
) -> str:
    """
    Classify current market phase. Priority order matters.

    Priority (highest to lowest):
    1. 'bear':       pct_from_52w_high < -20.0  (>20% below 52-week high)
    2. 'correction': pct_from_52w_high < -10.0  (10–20% below 52-week high)
    3. 'overbought': rsi is not None and rsi > 70
    4. 'oversold':   rsi is not None and rsi < 30
    5. 'uptrend':    sma_200 is not None and current_price > sma_200
    6. 'uptrend':    fallback if no other condition (no SMA-200 data)

    Note: overbought/oversold can coexist with uptrend — use highest priority.
    A bear market security can also be oversold — 'bear' takes priority.
    """
```

---

### 4. Regime Sensitivity Seed Data

Create a data migration (or seed management command) that populates `rm_scoring_config` with regime-to-asset-class sensitivity multipliers. These are read by M2-Spec-2 to adjust urgency scores.

Create a new table for this in the migration:

```python
class RegimeSensitivity(models.Model):
    """
    Sensitivity multiplier for each (asset_class, regime_quadrant) combination.
    Multiplier > 1.0: this asset class is more risky in this regime.
    Multiplier < 1.0: this asset class is less risky in this regime.
    Multiplier = 1.0: regime-neutral.
    """
    asset_class = models.CharField(max_length=50)
    regime_quadrant = models.CharField(max_length=2)
    sensitivity_multiplier = models.DecimalField(max_digits=5, decimal_places=2)
    rationale = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = 'rm_regime_sensitivity'
        unique_together = [('asset_class', 'regime_quadrant')]
```

**Seed data for rm_regime_sensitivity:**

| asset_class | Q1 | Q2 | Q3 | Q4 | Notes |
|---|---|---|---|---|---|
| common_stock | 1.00 | 1.10 | 1.30 | 1.40 | Growth stocks worst in Q3/Q4 |
| etf_equity | 1.00 | 1.05 | 1.20 | 1.30 | Broad equity ETFs follow market |
| etf_equity (dividend) | 0.90 | 1.00 | 1.10 | 1.20 | Dividend ETFs more defensive |
| cef | 1.00 | 1.05 | 1.20 | 1.25 | CEFs add liquidity risk in stress |
| crypto_etf | 1.20 | 1.30 | 1.50 | 1.60 | Crypto most volatile in bad regimes |
| cash_equiv | 0.60 | 0.70 | 0.70 | 0.80 | Cash is a haven; lower risk score |
| mutual_fund | 0.60 | 0.65 | 0.70 | 0.75 | Money market funds very stable |

Note: 'etf_equity (dividend)' matches VDY, XDIV, CGDV, AIS. Implement by checking sector = 'Dividend' or name contains 'Dividend' in rm_security_universe. The crew should implement a helper that maps symbol to effective sensitivity given its asset_class and sector.

---

### 5. Admin Registration

```python
@admin.register(RegimeState)
class RegimeStateAdmin(admin.ModelAdmin):
    list_display = [
        'calc_date', 'quadrant', 'confidence_score', 'transition_flag',
        'prob_q1', 'prob_q2', 'prob_q3', 'prob_q4',
        'yield_curve_score', 'vix_score'
    ]
    list_filter = ['quadrant', 'transition_flag']
    ordering = ['-calc_date']
    readonly_fields = [f.name for f in RegimeState._meta.fields]


@admin.register(MarketCondition)
class MarketConditionAdmin(admin.ModelAdmin):
    list_display = [
        'symbol', 'calc_date', 'market_phase',
        'rsi_14', 'crossover_type', 'macd_histogram',
        'bb_pct_b', 'obv_trend', 'pct_from_52w_high'
    ]
    list_filter = ['calc_date', 'market_phase', 'crossover_type', 'symbol']
    search_fields = ['symbol']
    ordering = ['-calc_date', 'symbol']
    readonly_fields = [f.name for f in MarketCondition._meta.fields]


@admin.register(RegimeSensitivity)
class RegimeSensitivityAdmin(admin.ModelAdmin):
    list_display = ['asset_class', 'regime_quadrant', 'sensitivity_multiplier', 'rationale']
    list_filter = ['regime_quadrant']
    ordering = ['asset_class', 'regime_quadrant']
    # Editable — these are user-configurable weights
```

---

### 6. Tests

File: risk/tests/test_regime_and_technicals.py

```python
import numpy as np
import pandas as pd
from django.test import TestCase
from risk.services.regime_engine import (
    score_yield_curve, score_vix, compute_quadrant_probabilities
)
from risk.services.technical_indicators import (
    sma, sma_crossover, rsi, macd, bollinger_bands,
    on_balance_volume, classify_market_phase
)


class RegimeDetectionTests(TestCase):

    def test_inverted_yield_curve_scores_low(self):
        score = score_yield_curve(-0.60)
        self.assertLess(score, 30)

    def test_normal_yield_curve_scores_high(self):
        score = score_yield_curve(1.20)
        self.assertGreater(score, 70)

    def test_flat_yield_curve_scores_midpoint(self):
        score = score_yield_curve(0.05)
        self.assertAlmostEqual(score, 50, delta=10)

    def test_high_vix_scores_low(self):
        score = score_vix(40)
        self.assertLess(score, 20)

    def test_low_vix_scores_high(self):
        score = score_vix(10)
        self.assertGreater(score, 75)

    def test_quadrant_probabilities_sum_to_one(self):
        scores = {
            'yield_curve': 65.0,
            'vix': 60.0,
            'cpi': None,
            'pmi': None,
            'gdp': None,
        }
        weights = {
            'regime_weight_yield_curve': 0.35,
            'regime_weight_vix': 0.25,
            'regime_weight_cpi': 0.20,
            'regime_weight_pmi': 0.10,
            'regime_weight_gdp': 0.10,
        }
        probs, confidence, quadrant = compute_quadrant_probabilities(scores, weights)
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=4)

    def test_high_growth_low_inflation_gives_q1(self):
        # High yield curve score, low VIX, low CPI
        scores = {'yield_curve': 80.0, 'vix': 80.0, 'cpi': 20.0, 'pmi': None, 'gdp': None}
        weights = {'regime_weight_yield_curve': 0.35, 'regime_weight_vix': 0.25,
                   'regime_weight_cpi': 0.20, 'regime_weight_pmi': 0.10, 'regime_weight_gdp': 0.10}
        probs, confidence, quadrant = compute_quadrant_probabilities(scores, weights)
        self.assertEqual(quadrant, 'Q1')

    def test_low_growth_high_inflation_gives_q4(self):
        # Inverted yield curve, high VIX, high CPI
        scores = {'yield_curve': 15.0, 'vix': 15.0, 'cpi': 90.0, 'pmi': None, 'gdp': None}
        weights = {'regime_weight_yield_curve': 0.35, 'regime_weight_vix': 0.25,
                   'regime_weight_cpi': 0.20, 'regime_weight_pmi': 0.10, 'regime_weight_gdp': 0.10}
        probs, confidence, quadrant = compute_quadrant_probabilities(scores, weights)
        self.assertEqual(quadrant, 'Q4')

    def test_null_indicators_redistributed(self):
        # With only 2 indicators, weights should redistribute to sum to 1.0
        scores = {'yield_curve': 60.0, 'vix': 50.0, 'cpi': None, 'pmi': None, 'gdp': None}
        weights = {'regime_weight_yield_curve': 0.35, 'regime_weight_vix': 0.25,
                   'regime_weight_cpi': 0.20, 'regime_weight_pmi': 0.10, 'regime_weight_gdp': 0.10}
        probs, confidence, quadrant = compute_quadrant_probabilities(scores, weights)
        # Should not raise; probabilities should still sum to 1.0
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=4)


class RSITests(TestCase):

    def test_rsi_on_known_sequence(self):
        # 14 days of constant +1 gain: RSI should be 100
        prices = pd.Series(list(range(1, 20)))  # 1,2,3...19 (18 days up)
        result = rsi(prices, period=14)
        # After 14+ up days in a row: RSI approaches 100
        self.assertGreater(result.dropna().iloc[-1], 90)

    def test_rsi_on_constant_losses(self):
        # 14 days of constant -1 loss: RSI should be 0
        prices = pd.Series(list(range(20, 1, -1)))  # 20,19,...2
        result = rsi(prices, period=14)
        self.assertLess(result.dropna().iloc[-1], 10)

    def test_rsi_range_is_0_to_100(self):
        np.random.seed(5)
        prices = pd.Series(100 + np.random.randn(252).cumsum())
        result = rsi(prices, period=14)
        valid = result.dropna()
        self.assertTrue((valid >= 0).all())
        self.assertTrue((valid <= 100).all())

    def test_rsi_requires_period_plus_one_rows(self):
        # Only 10 rows, period=14: all NaN
        prices = pd.Series(range(10))
        result = rsi(prices, period=14)
        self.assertTrue(result.isna().all())


class SMATests(TestCase):

    def test_sma_known_values(self):
        prices = pd.Series([10, 20, 30, 40, 50])
        result = sma(prices, 3)
        # SMA-3 of [10,20,30,40,50] = [NaN, NaN, 20.0, 30.0, 40.0]
        self.assertAlmostEqual(result.iloc[2], 20.0)
        self.assertAlmostEqual(result.iloc[4], 40.0)

    def test_golden_cross_detected(self):
        # Build series where 50d SMA crosses above 200d SMA on the last day
        dates = pd.date_range('2025-01-01', periods=300)
        sma_fast = pd.Series(
            [50.0] * 250 + [49.0] + [51.0] * 49,
            index=dates
        )
        sma_slow = pd.Series([50.0] * 300, index=dates)
        crossover_type, crossover_date = sma_crossover(sma_fast, sma_slow)
        self.assertEqual(crossover_type, 'golden')

    def test_no_crossover_when_stable(self):
        dates = pd.date_range('2025-01-01', periods=300)
        sma_fast = pd.Series([55.0] * 300, index=dates)  # Always above
        sma_slow = pd.Series([50.0] * 300, index=dates)
        crossover_type, _ = sma_crossover(sma_fast, sma_slow)
        self.assertEqual(crossover_type, 'none')


class BollingerBandTests(TestCase):

    def test_bandwidth_widens_with_higher_volatility(self):
        np.random.seed(7)
        stable_prices = pd.Series(100 + np.random.randn(50) * 1)
        volatile_prices = pd.Series(100 + np.random.randn(50) * 10)
        _, _, _, bw_stable, _ = bollinger_bands(stable_prices, window=20)
        _, _, _, bw_volatile, _ = bollinger_bands(volatile_prices, window=20)
        self.assertGreater(bw_volatile.iloc[-1], bw_stable.iloc[-1])

    def test_price_at_middle_gives_pct_b_half(self):
        prices = pd.Series([100.0] * 25)
        _, middle, _, _, pct_b = bollinger_bands(prices, window=20)
        # Price = middle = upper = lower when std=0: pct_b undefined (division by zero)
        # In this degenerate case, return 0.5 (price is at the middle)
        # The function must handle std=0 gracefully — return 0.5
        self.assertIsNotNone(pct_b.iloc[-1])


class MarketPhaseTests(TestCase):

    def test_bear_phase_overrides_oversold(self):
        # Price is 25% below 52-week high AND RSI is 25 (oversold)
        # Bear should take priority
        phase = classify_market_phase(75.0, sma_200=90.0, pct_from_52w_high=-25.0, rsi=25.0)
        self.assertEqual(phase, 'bear')

    def test_correction_phase(self):
        phase = classify_market_phase(85.0, sma_200=80.0, pct_from_52w_high=-15.0, rsi=45.0)
        self.assertEqual(phase, 'correction')

    def test_overbought_in_uptrend(self):
        phase = classify_market_phase(105.0, sma_200=90.0, pct_from_52w_high=-2.0, rsi=75.0)
        self.assertEqual(phase, 'overbought')

    def test_uptrend_when_above_200d_sma(self):
        phase = classify_market_phase(110.0, sma_200=90.0, pct_from_52w_high=-5.0, rsi=55.0)
        self.assertEqual(phase, 'uptrend')


class OBVTests(TestCase):

    def test_obv_increases_on_up_days(self):
        closes = pd.Series([100, 102, 101, 103])
        volumes = pd.Series([1000, 2000, 1500, 3000])
        result = on_balance_volume(closes, volumes)
        # Day 0: OBV=1000; Day 1 up: +2000; Day 2 down: -1500; Day 3 up: +3000
        self.assertEqual(result.iloc[1], 3000)
        self.assertEqual(result.iloc[2], 1500)
        self.assertEqual(result.iloc[3], 4500)

    def test_obv_constant_on_unchanged_close(self):
        closes = pd.Series([100, 100, 100])
        volumes = pd.Series([1000, 2000, 3000])
        result = on_balance_volume(closes, volumes)
        # No change in price: OBV stays at initial value
        self.assertEqual(result.iloc[1], result.iloc[0])


class IntegrationTests(TestCase):

    def test_calc_regime_command_produces_one_row(self):
        # Seed rm_economic_indicators with vix_close, yield_10y, yield_2y, yield_curve_slope
        # Run calc_regime --date 2026-06-02
        # Assert rm_regime_state has exactly one row for 2026-06-02
        # Assert prob_q1 + prob_q2 + prob_q3 + prob_q4 ≈ 1.0
        pass

    def test_calc_market_conditions_covers_all_held_symbols(self):
        # Seed 250 days of OHLCV for all 28 held symbols
        # Run calc_market_conditions
        # Assert rm_market_condition has one row per symbol for today
        # Assert no symbol with OHLCV data has all NULL indicators
        pass

    def test_mutual_fund_has_null_indicators(self):
        # Run calc_market_conditions for RBF2010
        # Assert rm_market_condition row has rsi_14=NULL, sma_50=NULL etc.
        # Assert market_phase = 'uptrend' (default for cash equiv)
        pass

    def test_death_cross_detected_for_historical_date(self):
        # Seed known historical price series for QQQ including a Death Cross event
        # (50d SMA crossed below 200d SMA in early 2022)
        # Run calc_market_conditions --date [known death cross date]
        # Assert crossover_type = 'death' and crossover_date is within that period
        pass
```

Run with: python manage.py test risk.tests.test_regime_and_technicals --settings=portfolio_manager.settings_test

---

## Constraints

- DO NOT implement urgency scoring — that is M2-Spec-2
- DO NOT produce rm_urgency_score rows — this spec produces only rm_regime_state and rm_market_condition
- DO NOT call yFinance directly — all data via DataSource from M0-Spec-2
- DO NOT push to git (manual step)
- All indicator computation is in pure Python functions — no ORM calls inside computation functions
- Missing economic indicators (CPI, PMI, GDP) must be handled gracefully — NULL score, weight redistributed
- PEP 8 throughout; full type hints on all functions; docstrings on all public functions

---

## Manual Steps Required (in order)

- [ ] Verify gate: SELECT COUNT(*) FROM rm_economic_indicators WHERE indicator_name='vix_close' — expect 2500+ rows
- [ ] Verify gate: SELECT COUNT(*) FROM rm_market_price_history WHERE symbol='VDY' — expect 2000+ rows
- [ ] python manage.py makemigrations risk
- [ ] python manage.py migrate --settings=portfolio_manager.settings_dev
- [ ] python manage.py seed_regime_sensitivity --settings=portfolio_manager.settings_dev
- [ ] python manage.py calc_regime --date 2026-06-02 --settings=portfolio_manager.settings_dev
- [ ] Verify in SSMS: SELECT quadrant, confidence_score, prob_q1, prob_q2, prob_q3, prob_q4, yield_curve_score, vix_score FROM rm_regime_state WHERE calc_date='2026-06-02'
- [ ] Sanity check: with yield curve mildly inverted and VIX ~18, expect Q3 or Q2 as highest probability
- [ ] python manage.py calc_market_conditions --date 2026-06-02 --settings=portfolio_manager.settings_dev
- [ ] Verify in SSMS: SELECT symbol, market_phase, rsi_14, crossover_type, macd_histogram, bb_pct_b, pct_from_52w_high FROM rm_market_condition WHERE calc_date='2026-06-02' ORDER BY symbol
- [ ] Sanity check VDY: RSI should be 40–65 for stable dividend ETF; market_phase likely 'correction' or 'uptrend'
- [ ] Sanity check IBIT: RSI may be extreme (<30 or >70); bb_bandwidth should be wide
- [ ] Sanity check SGOV: all indicators NULL; market_phase = 'uptrend'
- [ ] python manage.py test risk.tests.test_regime_and_technicals --settings=portfolio_manager.settings_test
- [ ] git add -A && git commit -m "feat: M2-Spec-1 regime detection + technical indicators"
- [ ] git push origin feature/risk-m2-regime

---

## Human Approval Required

Review the regime output before proceeding to M2-Spec-2. Confirm the detected quadrant makes sense given current macro environment (mid-2026: moderating inflation, slowing growth). If confidence_score is below 30, the regime is genuinely ambiguous — note this as context for urgency scoring.

---

M2-Spec-1 | Portfolio Manager Phase B | June 2026 | tdwdev
