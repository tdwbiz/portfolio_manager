# Services API Reference

## Risk Services

### Volatility (`risk/services/volatility.py`)

#### `compute_volatility_summaries(symbols=None, windows=None)`
Compute volatility summaries for price data.

**Parameters**:
- `symbols` (list[str], optional): Symbols to compute. Fetches from DB if None.
- `windows` (list[int], optional): Lookback windows (e.g., [30, 60, 120, 252]).

**Returns**: `list[dict]` — Volatility summaries per symbol per window.

### Technical Indicators (`risk/services/technical_indicators.py`)

#### `sma(prices: pd.Series, window: int) -> pd.Series`
Compute Simple Moving Average.

#### `rsi(prices: pd.Series, window: int = 14) -> pd.Series`
Compute Relative Strength Index.

#### `macd(prices: pd.Series, fast: int = 12, slow: int = 26) -> tuple[pd.Series, pd.Series, pd.Series]`
Compute MACD line, signal line, and histogram.

#### `bollinger_bands(prices: pd.Series, window: int = 20, num_std: int = 2) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]`
Compute Bollinger Bands. Returns (upper, middle, lower, bandwidth, pct_b).

#### `classify_market_phase(current_price: float, sma_200: float, pct_from_52w_high: float, rsi: float) -> str`
Classify market as 'bull', 'bear', 'correction', 'overbought', 'oversold', or 'uptrend'.

### Option Pricing (`risk/services/option_pricing.py`)

#### `black_scholes_call(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, dict]`
Compute Black-Scholes call price and Greeks.

#### `black_scholes_put(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, dict]`
Compute Black-Scholes put price and Greeks.

#### `estimate_option_cost(strike: float, quantity: int, volatility: float, risk_free: float, bid_ask_spread: float) -> dict`
Estimate total option cost including premium, commission, and bid-ask spread.

### Stress Testing (`risk/services/stress_test.py`)

#### `run_portfolio_stress_test(portfolio: list[dict], scenario: dict, shocks: dict) -> dict`
Run portfolio stress test with named or custom scenarios.

**Returns**: `dict` with `total_loss`, `per_position`, `strategy_overlay`, `var_reduction`.

### Gap Detection (`risk/services/gap_detection.py`)

#### `detect_gaps(symbol: str, max_gap_days: int = 10) -> list[GapReport]`
Detect trading gaps in price history.

**Returns**: `list[GapReport]` — Each report has `symbol`, `start_date`, `end_date`, `gap_days`, `severity`.

### VaR Reduction (`risk/services/var_reduction.py`)

#### `compute_var_reduction(strategy_code: str, var: float, market_value: float, **kwargs) -> dict`
Compute VaR reduction for a given strategy.

**Returns**: `dict` with `reduction`, `var_after`, `var_reduction_pct_of_mv`.

## Data Source (`risk/services/data_source.py`)

#### `load_price_series(symbol: str, start_date: date, end_date: date) -> pd.DataFrame`
Load price series from database or yFinance.

#### `fetch_last_prices(symbols: list[str]) -> dict[str, float]`
Fetch latest prices for multiple symbols.
