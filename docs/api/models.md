# Models API Reference

## Core Models

### `Account`
Represents a brokerage account (RBC, IB, etc.)

**Fields**:
- `account_id` (CharField): Unique account identifier
- `name` (CharField): Human-readable account name
- `broker` (CharField): Broker name (RBC, IB, etc.)
- `is_active` (BooleanField): Whether the account is active

### `Transaction`
Represents a trade or transaction within an account

**Fields**:
- `account` (ForeignKey): Parent account
- `date` (DateField): Transaction date
- `symbol` (CharField): Ticker symbol
- `quantity` (DecimalField): Number of shares
- `price` (DecimalField): Price per share
- `currency` (CharField): Transaction currency (CAD, USD)
- `activity_type` (CharField): Buy, Sell, Dividend, etc.

### `HistoricalPrice`
Daily OHLCV price data for securities

**Fields**:
- `symbol` (CharField): Ticker symbol
- `date` (DateField): Price date
- `open` (DecimalField): Opening price
- `high` (DecimalField): High price
- `low` (DecimalField): Low price
- `close` (DecimalField): Closing price
- `volume` (IntegerField): Trading volume
- `currency` (CharField): Price currency

## Risk Models

### `SecurityUniverse`
Reference data for securities in the risk universe

**Fields**:
- `symbol` (CharField): Ticker symbol
- `asset_class` (CharField): equity_ca, equity_us, crypto, etc.
- `sector` (CharField): Industry sector
- `security_type` (CharField): Stock, ETF, Mutual Fund, etc.
- `use_jump_diffusion` (BooleanField): Whether to use jump-diffusion in simulations

### `SecurityRiskMetrics`
Computed risk metrics for securities

**Fields**:
- `symbol` (CharField): Ticker symbol
- `calc_date` (DateField): Calculation date
- `volatility_30d` (DecimalField): 30-day volatility
- `var_95` (DecimalField): 95% Value at Risk
- `cvar` (DecimalField): Conditional Value at Risk
- `sharpe_ratio` (DecimalField): Sharpe ratio

### `BacktestResult`
Results from backtesting a strategy

**Fields**:
- `symbol` (CharField): Ticker symbol
- `strategy_template` (ForeignKey): Strategy used
- `scenario` (ForeignKey): Historical scenario
- `start_date` (DateField): Backtest start
- `end_date` (DateField): Backtest end
- `final_value` (DecimalField): Final portfolio value
- `max_drawdown` (DecimalField): Maximum drawdown

## Import Models

### `RbcMonthly`
Parsed RBC monthly statement

**Fields**:
- `account` (ForeignKey): Parent account
- `file_name` (CharField): Original PDF filename
- `statement_date` (DateField): Statement date
- `account_number` (CharField): Account number from PDF

### `RbcAnnual`
Parsed RBC annual report

**Fields**:
- `account` (ForeignKey): Parent account
- `file_name` (CharField): Original PDF filename
- `statement_date` (DateField): Report date
