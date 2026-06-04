# Feature Spec: M0-Spec-1 — Data Models, position_history, Views & Seed Data

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/risk-m0-data-models

---

**Architecture ref:** PM_PhaseB_Architecture_Final_v2.0.md

---

## Context

This is Spec 1 of 14 for the Risk Management Module (Phase B). It is the data foundation — no business logic, no calculations, no fetching. Every subsequent spec depends on the tables and views created here.

**Phase A schema confirmed from live database (PortfolioDEV, 2026-05-24):**

| Table | Relevant Columns | Notes |
|---|---|---|
| `core_account` | id (bigint PK), account_id (nvarchar 50, UNIQUE), name, broker, currency, starting_balance, category, owner | No `exclude_from_risk` column yet — add via migration |
| `core_transaction` | id, date, symbol, type, quantity, price, commission, amount, currency, account_id (FK→core_account.id), amount_cad, exchange_rate_cad, notes, tx_hash | Position history must be derived from these |
| `core_historicalprice` | id, symbol, date, close ONLY | No OHLCV — M0 adds full price history separately in `rm_market_price_history` |
| `core_brokerstatement` | source, file_date, raw_filename | — |
| `core_pdfstatement` | file_name, statement_date, account_number, fx_rate, account_id (FK) | — |

**Live portfolio summary (2026-05-24):**
- Two owners: **TOM**, **SUSAN**
- 11 accounts: RRSP_TOM, LIF_TOM, IB_TOM, TFSA_TOM, RRIF_SUSAN, LIF_SUSAN, IB_SUSAN, TFSA_SUSAN, IB_JOINT, TAXABLE_TOM_SUSAN, TAXABLE_CAR
- 25 unique symbols: AAPL, ADX, AIS, CGDV, ETHA, GOOG, IBIT, ICSH, IGM, IWY, MA, NVDA, QQQ, RBF2010, RBF2014, SGOV, SMH, SOXX, SPY, V, VDY, VFV, VOO, XDIV, XEQT
- Total portfolio ~$4.5M CAD; unrealised gains ~$1.65M CAD
- TAXABLE_CAR: living expenses reserve account — flag `exclude_from_risk = 1` but do not hard-code the name

**Stack:**
- Python 3.12 / Django 5.1
- MSSQL 2025 (dev: PortfolioDEV) / MSSQL 2022 (prod: TaxPortfolioPRD)
- Project location: `C:\AI-DevTeam\projects\portfolio_manager`

---

## Requirements

### 1. Phase A Extension — `core_account` — Add `exclude_from_risk` Flag

Create a migration in the `core` app that adds one column to `core_account`:

```sql
ALTER TABLE core_account ADD exclude_from_risk BIT NOT NULL DEFAULT 0;
```

- Django migration: `core/migrations/XXXX_add_exclude_from_risk_to_account.py`
- Django model: add `exclude_from_risk = models.BooleanField(default=False)` to `core_account` model
- After migration, update TAXABLE_CAR's flag to `True` via a data migration or seed script — **not hard-coded in application logic**
- Admin: register `exclude_from_risk` as editable in the existing `core_account` admin

---

### 2. Phase A Extension — `position_history` Table

Add to the `core` app (not the `risk` app). This is Phase A infrastructure consumed by Phase B.

**Table: `core_position_history`**

```sql
CREATE TABLE core_position_history (
    id                  BIGINT IDENTITY(1,1) PRIMARY KEY,
    owner               NVARCHAR(50)     NOT NULL,
    account_id          BIGINT           NOT NULL,  -- FK → core_account.id
    symbol              NVARCHAR(50)     NOT NULL,
    quantity            NUMERIC(12, 4)   NOT NULL,
    cost_basis_per_unit NUMERIC(12, 4)   NOT NULL,
    total_cost_basis    NUMERIC(15, 2)   NOT NULL,
    currency            NVARCHAR(3)      NOT NULL,
    date_from           DATE             NOT NULL,
    date_to             DATE             NULL,       -- NULL = current open record
    as_of_timestamp     DATETIMEOFFSET   NOT NULL DEFAULT SYSDATETIMEOFFSET()
);
```

**Indexes:**
```sql
CREATE INDEX ix_poshistory_account_symbol ON core_position_history (account_id, symbol, date_from, date_to);
CREATE INDEX ix_poshistory_owner ON core_position_history (owner, date_from, date_to);
CREATE INDEX ix_poshistory_symbol ON core_position_history (symbol, date_from, date_to);
```

**FK constraint:**
```sql
ALTER TABLE core_position_history ADD CONSTRAINT fk_poshistory_account
    FOREIGN KEY (account_id) REFERENCES core_account(id);
```

**Business rules:**
- Each record represents a position in one account for one symbol, valid from `date_from` to `date_to` (exclusive)
- Open/current records have `date_to = NULL`
- One open record per (account_id, symbol) at any point in time
- `owner` is denormalised from `core_account.owner` for query efficiency
- `quantity` is the net position (buys minus sells); zero-quantity records should not exist — close by setting `date_to`

**Django model:** `core/models.py` — class `PositionHistory`
- Use `Meta.managed = False` if using raw SQL migration, OR use Django migration — discuss with developer
- FK: `account = models.ForeignKey('Account', on_delete=models.PROTECT, related_name='position_history')`

**Management command: `rebuild_position_history`**

Create `core/management/commands/rebuild_position_history.py`:

```
Algorithm:
1. Truncate core_position_history (with confirmation prompt in interactive mode; --force flag for scripted use)
2. Load all core_transaction records ordered by (account_id, symbol, date, id)
3. For each (account_id, symbol) group:
   a. Replay transactions chronologically
   b. Maintain running quantity and weighted average cost basis
   c. On quantity change: close current record (set date_to = transaction.date - 1 day), open new record
   d. On final transaction: leave date_to = NULL (current open record)
   e. If final quantity rounds to zero (< 0.0001): set date_to = last_transaction_date (closed position)
4. Populate owner from core_account.owner for each record
5. Print summary: records created, symbols processed, accounts processed
```

Transaction types that affect positions (consult `core_transactiontype.affects_position` flag).

---

### 3. Phase A Database Views

Create via a **raw SQL migration** (not a Django model). The migration runs the `CREATE OR ALTER VIEW` statements directly using `migrations.RunSQL`.

**View 1: `vw_current_portfolio`**

Returns current open positions, optionally filtered by `exclude_from_risk`.

```sql
CREATE OR ALTER VIEW vw_current_portfolio AS
SELECT
    ph.id,
    ph.owner,
    ph.account_id,
    ca.account_id        AS account_code,
    ca.name              AS account_name,
    ca.category          AS account_category,
    ca.broker,
    ca.exclude_from_risk,
    ph.symbol,
    ph.quantity,
    ph.cost_basis_per_unit,
    ph.total_cost_basis,
    ph.currency,
    ph.date_from
FROM core_position_history ph
INNER JOIN core_account ca ON ca.id = ph.account_id
WHERE ph.date_to IS NULL
  AND ph.quantity > 0.0001
  AND ca.exclude_from_risk = 0;
-- Note: consumers who want to include excluded accounts
-- use the base table with a join, not this view.
```

**View 2: `vw_portfolio_as_of`**

MSSQL does not support parameterised views natively. Implement as a **stored procedure** or **table-valued function**:

```sql
CREATE OR ALTER FUNCTION vw_portfolio_as_of (@as_of_date DATE)
RETURNS TABLE AS RETURN
(
    SELECT
        ph.id,
        ph.owner,
        ph.account_id,
        ca.account_id        AS account_code,
        ca.name              AS account_name,
        ca.category          AS account_category,
        ca.exclude_from_risk,
        ph.symbol,
        ph.quantity,
        ph.cost_basis_per_unit,
        ph.total_cost_basis,
        ph.currency,
        ph.date_from,
        ph.date_to
    FROM core_position_history ph
    INNER JOIN core_account ca ON ca.id = ph.account_id
    WHERE ph.date_from <= @as_of_date
      AND (ph.date_to IS NULL OR ph.date_to > @as_of_date)
      AND ph.quantity > 0.0001
);
```

Call from Python as: `SELECT * FROM vw_portfolio_as_of('2025-01-01')` via raw SQL.

**View 3: `vw_portfolio_diff`**

```sql
CREATE OR ALTER FUNCTION vw_portfolio_diff (@date1 DATE, @date2 DATE)
RETURNS TABLE AS RETURN
(
    -- Positions at date2 not in date1 (new positions)
    SELECT 'd2_only' AS change_type, d2.owner, d2.account_id, d2.symbol,
           0 AS qty_date1, d2.quantity AS qty_date2,
           d2.quantity AS qty_change
    FROM vw_portfolio_as_of(@date2) d2
    WHERE NOT EXISTS (
        SELECT 1 FROM vw_portfolio_as_of(@date1) d1
        WHERE d1.account_id = d2.account_id AND d1.symbol = d2.symbol
    )
    UNION ALL
    -- Positions at date1 not in date2 (closed positions)
    SELECT 'd1_only', d1.owner, d1.account_id, d1.symbol,
           d1.quantity, 0, -d1.quantity
    FROM vw_portfolio_as_of(@date1) d1
    WHERE NOT EXISTS (
        SELECT 1 FROM vw_portfolio_as_of(@date2) d2
        WHERE d2.account_id = d1.account_id AND d2.symbol = d1.symbol
    )
    UNION ALL
    -- Positions in both with changed quantity
    SELECT 'changed', d1.owner, d1.account_id, d1.symbol,
           d1.quantity, d2.quantity, d2.quantity - d1.quantity
    FROM vw_portfolio_as_of(@date1) d1
    INNER JOIN vw_portfolio_as_of(@date2) d2
        ON d1.account_id = d2.account_id AND d1.symbol = d2.symbol
    WHERE ABS(d2.quantity - d1.quantity) > 0.0001
);
```

---

### 4. New Django App — `risk`

Create a new Django app: `python manage.py startapp risk`

Add to `INSTALLED_APPS` in settings.

All `rm_` prefixed models live in `risk/models.py`. All table names use `db_table = 'rm_<name>'` in Meta.

---

### 5. Risk Module Tables — `rm_` prefix

**5.1 `rm_security_universe`**

Master list of all symbols the system knows about.

```python
class SecurityUniverse(models.Model):
    symbol = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200, blank=True)
    security_type = models.CharField(max_length=20)
    # Values: 'held', 'benchmark', 'hedge', 'alternative', 'watchlist'
    # A symbol can be both held and benchmark (e.g. QQQ, SPY) — use comma-separated or flags
    asset_class = models.CharField(max_length=50)
    # Values: 'equity', 'etf_equity', 'etf_bond', 'etf_commodity', 'etf_crypto',
    #         'common_stock', 'mutual_fund', 'cef', 'cash_equiv', 'crypto_etf'
    sector = models.CharField(max_length=100, blank=True)
    primary_currency = models.CharField(max_length=3, default='USD')
    is_held = models.BooleanField(default=False)
    is_benchmark = models.BooleanField(default=False)
    is_hedge = models.BooleanField(default=False)
    is_cef = models.BooleanField(default=False)  # Closed-end fund flag (ADX)
    use_jump_diffusion = models.BooleanField(default=False)  # For Monte Carlo M5
    options_available = models.BooleanField(default=True)  # False for ADX, RBF*, ICSH
    amibroker_symbol = models.CharField(max_length=30, blank=True)  # If different from yFinance symbol
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rm_security_universe'
        ordering = ['symbol']
```

**5.2 `rm_market_price_history`**

Full OHLCV — separate from `core_historicalprice` which has only `close`.

```python
class MarketPriceHistory(models.Model):
    symbol = models.CharField(max_length=20)
    date = models.DateField()
    open = models.DecimalField(max_digits=18, decimal_places=6)
    high = models.DecimalField(max_digits=18, decimal_places=6)
    low = models.DecimalField(max_digits=18, decimal_places=6)
    close = models.DecimalField(max_digits=18, decimal_places=6)
    adj_close = models.DecimalField(max_digits=18, decimal_places=6)
    volume = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, default='USD')
    data_source = models.CharField(max_length=20)  # 'amibroker' or 'yfinance'
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_market_price_history'
        unique_together = [('symbol', 'date')]
        indexes = [
            models.Index(fields=['symbol', 'date']),
            models.Index(fields=['date']),
        ]
```

**5.3 `rm_volatility_summary`**

Precomputed volatility at standard windows. Populated by M0-Spec-2 and updated daily.

```python
class VolatilitySummary(models.Model):
    symbol = models.CharField(max_length=20)
    calc_date = models.DateField()
    lookback_days = models.IntegerField()  # 10, 21, 63, 252
    volatility_annualised = models.DecimalField(max_digits=10, decimal_places=6)
    # Annualised: std(daily log returns) × sqrt(252)
    data_source = models.CharField(max_length=20)
    calc_timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_volatility_summary'
        unique_together = [('symbol', 'calc_date', 'lookback_days')]
        indexes = [models.Index(fields=['symbol', 'calc_date'])]
```

**5.4 `rm_fx_rates`**

Daily USD/CAD exchange rates.

```python
class FxRate(models.Model):
    date = models.DateField(unique=True)
    usd_cad_rate = models.DecimalField(max_digits=10, decimal_places=6)
    data_source = models.CharField(max_length=20)
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_fx_rates'
        indexes = [models.Index(fields=['date'])]
```

**5.5 `rm_economic_indicators`**

Macro data for M2 regime detection.

```python
class EconomicIndicator(models.Model):
    date = models.DateField()
    indicator_name = models.CharField(max_length=50)
    # Valid values: 'yield_10y', 'yield_2y', 'yield_curve_slope',
    #               'cpi_yoy', 'pmi_composite', 'gdp_growth_qoq',
    #               'vix_close', 'vix_3m_close', 'unemployment_rate'
    value = models.DecimalField(max_digits=12, decimal_places=4)
    data_source = models.CharField(max_length=30)
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_economic_indicators'
        unique_together = [('date', 'indicator_name')]
        indexes = [
            models.Index(fields=['indicator_name', 'date']),
            models.Index(fields=['date']),
        ]
```

**5.6 `rm_alternative_portfolios`**

User-defined watchlists for what-if analysis. Per account only. No cross-account or version history.

```python
class AlternativePortfolio(models.Model):
    owner = models.CharField(max_length=50)  # TOM or SUSAN
    account = models.ForeignKey('core.Account', on_delete=models.CASCADE,
                                 related_name='alternative_portfolios')
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rm_alternative_portfolios'
        unique_together = [('account', 'name')]
```

**5.7 `rm_alternative_holdings`**

Securities in an alternative portfolio. Weights must sum to 1.0 per portfolio.

```python
class AlternativeHolding(models.Model):
    portfolio = models.ForeignKey(AlternativePortfolio, on_delete=models.CASCADE,
                                   related_name='holdings')
    symbol = models.CharField(max_length=20)
    target_weight = models.DecimalField(max_digits=6, decimal_places=4)
    # Stored as 0.0 – 1.0. E.g. 0.3500 = 35%

    class Meta:
        db_table = 'rm_alternative_holdings'
        unique_together = [('portfolio', 'symbol')]
```

**Weight validation:** Enforce in the model's `clean()` method and in the view layer:
```python
def clean(self):
    total = self.portfolio.holdings.exclude(pk=self.pk).aggregate(
        total=models.Sum('target_weight'))['total'] or 0
    if total + self.target_weight > Decimal('1.00001'):
        raise ValidationError("Holdings weights exceed 100% for this portfolio.")
```

**5.8 `rm_scoring_config`**

Configuration table for M2 scoring weights and thresholds. Created here, populated in M2-Spec-2.

```python
class ScoringConfig(models.Model):
    config_key = models.CharField(max_length=100, unique=True)
    config_value = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rm_scoring_config'
```

Initial seed keys (with placeholder values — M2-Spec-2 will populate proper values):
```
weight_m1_metrics       = 0.35
weight_security_driver  = 0.20
weight_regime           = 0.25
weight_market_condition = 0.20
threshold_low           = 25
threshold_medium        = 50
threshold_high          = 75
var_portfolio_alert_pct = 5.0
concentration_alert_pct = 20.0
```

**5.9 `rm_security_index_exposure`**

Seed table mapping ETFs to their underlying index exposures. Used for effective concentration calculation in M1-Spec-2.

```python
class SecurityIndexExposure(models.Model):
    symbol = models.CharField(max_length=20)
    index_name = models.CharField(max_length=50)
    # Values: 'SP500', 'NASDAQ100', 'TSX', 'SEMICONDUCTOR', 'TECHNOLOGY',
    #         'DIVIDEND_US', 'DIVIDEND_CA', 'BROAD_MARKET', 'OTHER'
    exposure_weight = models.DecimalField(max_digits=6, decimal_places=4)
    # Approximate; e.g. VFV → SP500 = 1.0, QQQ → NASDAQ100 = 1.0,
    # IGM → NASDAQ100 ≈ 0.85, TECHNOLOGY ≈ 0.15 (approximate)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = 'rm_security_index_exposure'
        unique_together = [('symbol', 'index_name')]
```

---

### 6. Seed Data

Create a management command `seed_rm_universe` (or a data migration — developer's choice) that inserts the following data.

**6.1 `rm_security_universe` seed:**

| Symbol | Name | Asset Class | is_held | is_benchmark | is_hedge | is_cef | options_available | Notes |
|---|---|---|---|---|---|---|---|---|
| AAPL | Apple Inc | common_stock | ✓ | | | | ✓ | High UGL in IB_SUSAN |
| ADX | Adams Diversified Equity | cef | ✓ | | | ✓ | ✗ | Closed-end fund; limited liquidity |
| AIS | Ares Capital ETF | etf_equity | ✓ | | | | ✓ | |
| CGDV | Capital Group Dividend ETF | etf_equity | ✓ | | | | ✓ | |
| ETHA | iShares Ethereum ETF | crypto_etf | ✓ | | | | ✓ | use_jump_diffusion=True |
| GOOG | Alphabet Inc | common_stock | ✓ | | | | ✓ | |
| IBIT | iShares Bitcoin ETF | crypto_etf | ✓ | | | | ✓ | use_jump_diffusion=True |
| ICSH | iShares Ultra Short Bond ETF | cash_equiv | ✓ | | | | ✗ | Near-cash; low risk |
| IGM | iShares Expanded Tech ETF | etf_equity | ✓ | | | | ✓ | |
| IWY | iShares Russell Top 200 Growth | etf_equity | ✓ | | | | ✓ | |
| MA | Mastercard Inc | common_stock | ✓ | | | | ✓ | |
| NVDA | NVIDIA Corp | common_stock | ✓ | | | | ✓ | use_jump_diffusion=True |
| QQQ | Invesco QQQ Nasdaq-100 | etf_equity | ✓ | ✓ | | | ✓ | Held AND benchmark |
| RBF2010 | RBC CAD Money Market | mutual_fund | ✓ | | | | ✗ | Cash equiv; primary_currency=CAD |
| RBF2014 | RBC USD Money Market | mutual_fund | ✓ | | | | ✗ | Cash equiv |
| SGOV | iShares 0-3 Month Treasury | cash_equiv | ✓ | | | | ✗ | Near-cash risk-free proxy |
| SMH | VanEck Semiconductor ETF | etf_equity | ✓ | | | | ✓ | |
| SOXX | iShares Semiconductor ETF | etf_equity | ✓ | | | | ✓ | |
| SPY | SPDR S&P 500 ETF | etf_equity | ✓ | ✓ | | | ✓ | Held AND benchmark |
| V | Visa Inc | common_stock | ✓ | | | | ✓ | |
| VDY | Vanguard FTSE Canadian High Div | etf_equity | ✓ | | | | ✓ | primary_currency=CAD; largest position |
| VFV | Vanguard S&P 500 Index ETF CAD | etf_equity | ✓ | | | | ✓ | primary_currency=CAD; S&P 500 CAD-hedged |
| VOO | Vanguard S&P 500 ETF | etf_equity | ✓ | | | | ✓ | |
| XDIV | iShares Core MSCI Canadian Div | etf_equity | ✓ | | | | ✓ | primary_currency=CAD |
| XEQT | iShares Core Equity ETF Portfolio | etf_equity | ✓ | | | | ✓ | primary_currency=CAD |
| XIU.TO | iShares S&P/TSX 60 ETF | etf_equity | | ✓ | | | ✓ | Benchmark only; amibroker_symbol='XIU' |
| SOXS | Direxion Semiconductor Bear 3x | etf_equity | | | ✓ | | ✓ | Hedge for SOXX/SMH |
| SOXL | Direxion Semiconductor Bull 3x | etf_equity | | | ✓ | | ✓ | |
| PSQ | ProShares Short QQQ | etf_equity | | | ✓ | | ✓ | Hedge for QQQ |
| SPXS | Direxion S&P 500 Bear 3x | etf_equity | | | ✓ | | ✓ | Hedge for SPY/VOO/VFV |

**6.2 `rm_security_index_exposure` seed (key entries):**

| Symbol | Index | Weight |
|---|---|---|
| VFV | SP500 | 1.00 |
| VOO | SP500 | 1.00 |
| SPY | SP500 | 1.00 |
| QQQ | NASDAQ100 | 1.00 |
| IGM | NASDAQ100 | 0.80 |
| IGM | TECHNOLOGY | 0.20 |
| IWY | SP500 | 0.85 |
| IWY | TECHNOLOGY | 0.15 |
| SOXX | SEMICONDUCTOR | 1.00 |
| SMH | SEMICONDUCTOR | 1.00 |
| VDY | DIVIDEND_CA | 1.00 |
| VDY | TSX | 0.90 |
| XDIV | DIVIDEND_CA | 1.00 |
| XDIV | TSX | 1.00 |
| XEQT | SP500 | 0.45 |
| XEQT | TSX | 0.25 |
| XEQT | BROAD_MARKET | 0.30 |
| CGDV | DIVIDEND_US | 1.00 |
| CGDV | SP500 | 0.70 |
| AIS | DIVIDEND_US | 0.80 |
| IBIT | OTHER | 1.00 |
| ETHA | OTHER | 1.00 |
| ADX | SP500 | 0.85 |
| AAPL | TECHNOLOGY | 1.00 |
| NVDA | SEMICONDUCTOR | 0.70 |
| NVDA | TECHNOLOGY | 0.30 |
| MA | OTHER | 1.00 |
| GOOG | TECHNOLOGY | 0.80 |
| GOOG | NASDAQ100 | 0.20 |
| V | OTHER | 1.00 |

**6.3 Set TAXABLE_CAR `exclude_from_risk = 1`:**

```python
from core.models import Account
Account.objects.filter(account_id='TS_CAR').update(exclude_from_risk=True)
# Note: confirm account_id value against live data — should be 'TS_CAR' or similar
```

---

### 7. Django Admin Registration

Register all new models in `core/admin.py` and `risk/admin.py`:

- `core_account`: add `exclude_from_risk` to `list_display` and `fieldsets`
- `PositionHistory`: read-only in admin (no manual editing); list view with filters by owner, account, symbol; search by symbol
- All `rm_` models: register with appropriate list_display and search_fields
- `AlternativeHolding`: inline under `AlternativePortfolio`
- `ScoringConfig`: editable in admin

---

### 8. Tests

File: `core/tests/test_risk_models.py` and `risk/tests/test_rm_models.py`

**Test cases required:**

```python
# core/tests/test_risk_models.py

class PositionHistoryTemporalTests(TestCase):
    """Test temporal query logic for position_history"""
    
    def test_current_position_has_null_date_to(self):
        # Create position records; verify current record has date_to=None
        
    def test_as_of_query_returns_correct_position(self):
        # Position opens 2025-01-15, closes 2025-06-01, new opens 2025-06-01
        # as_of(2025-03-01) → first position
        # as_of(2025-07-01) → second position
        
    def test_as_of_query_returns_nothing_before_position_opened(self):
        # as_of(2025-01-10) → no position (opened 2025-01-15)
        
    def test_rebuild_position_history_idempotent(self):
        # Run rebuild twice; second run produces identical results
        
    def test_exclude_from_risk_flag_filters_vw_current_portfolio(self):
        # Account with exclude_from_risk=True does not appear in vw_current_portfolio
        
    def test_taxable_car_excluded_by_default(self):
        # After seed, TAXABLE_CAR account has exclude_from_risk=True
        # Verify it does not appear in vw_current_portfolio


class AccountExcludeFromRiskTests(TestCase):
    
    def test_default_exclude_from_risk_is_false(self):
        # New account created without setting flag → exclude_from_risk=False
        
    def test_toggle_exclude_from_risk(self):
        # Set True, verify, set False, verify


# risk/tests/test_rm_models.py

class AlternativePortfolioWeightTests(TestCase):
    
    def test_weights_sum_to_one_valid(self):
        # Create portfolio with 3 holdings summing to exactly 1.0 → valid
        
    def test_weights_exceeding_one_raises_validation_error(self):
        # Add holding that pushes total > 1.0 → ValidationError
        
    def test_single_holding_at_full_weight(self):
        # Single holding at 1.0 → valid
        
    def test_empty_portfolio_is_valid(self):
        # Portfolio with no holdings → valid (zero sum)


class SecurityUniverseSeedTests(TestCase):
    fixtures = ['rm_security_universe_seed.json']
    
    def test_all_25_held_symbols_present(self):
        held = SecurityUniverse.objects.filter(is_held=True)
        symbols = set(held.values_list('symbol', flat=True))
        expected = {'AAPL', 'ADX', 'AIS', 'CGDV', 'ETHA', 'GOOG', 'IBIT', 'ICSH',
                    'IGM', 'IWY', 'MA', 'NVDA', 'QQQ', 'RBF2010', 'RBF2014', 'SGOV',
                    'SMH', 'SOXX', 'SPY', 'V', 'VDY', 'VFV', 'VOO', 'XDIV', 'XEQT'}
        self.assertEqual(symbols, expected)
        
    def test_adx_is_cef_and_options_not_available(self):
        adx = SecurityUniverse.objects.get(symbol='ADX')
        self.assertTrue(adx.is_cef)
        self.assertFalse(adx.options_available)
        
    def test_crypto_etfs_use_jump_diffusion(self):
        for sym in ['IBIT', 'ETHA']:
            s = SecurityUniverse.objects.get(symbol=sym)
            self.assertTrue(s.use_jump_diffusion)
            
    def test_qqq_spy_are_both_held_and_benchmark(self):
        for sym in ['QQQ', 'SPY']:
            s = SecurityUniverse.objects.get(symbol=sym)
            self.assertTrue(s.is_held)
            self.assertTrue(s.is_benchmark)


class FxRateTests(TestCase):
    
    def test_usd_cad_rate_stored_correctly(self):
        FxRate.objects.create(date='2026-01-15', usd_cad_rate=Decimal('1.4423'), data_source='yfinance')
        rate = FxRate.objects.get(date='2026-01-15')
        self.assertEqual(rate.usd_cad_rate, Decimal('1.4423'))
        
    def test_duplicate_date_raises_integrity_error(self):
        FxRate.objects.create(date='2026-01-15', usd_cad_rate=Decimal('1.4423'), data_source='yfinance')
        with self.assertRaises(Exception):
            FxRate.objects.create(date='2026-01-15', usd_cad_rate=Decimal('1.4500'), data_source='yfinance')
```

Run with: `python manage.py test core risk --settings=portfolio_manager.settings_test`

---

### 9. Settings

Ensure `settings_test.py` exists and points to a test MSSQL database or SQLite for CI. Do not run tests against PortfolioDEV.

Ensure the `risk` app is in `INSTALLED_APPS` in `settings_dev.py` and `settings_test.py`.

---

## Constraints

- **DO NOT** implement any data fetching logic — that is M0-Spec-2
- **DO NOT** implement any calculation logic — that is M1 and beyond
- **DO NOT** create `rm_regime_state`, `rm_market_condition`, `rm_urgency_score`, or any M1–M8 tables — those belong to their respective specs
- **DO NOT** push to git (manual step)
- **DO NOT** modify any existing Phase A table except adding `exclude_from_risk` to `core_account`
- Risk module never writes to any Phase A table except via the approved migration
- All `rm_` tables use `rm_` prefix in `db_table` Meta
- PEP 8 throughout; full type hints and docstrings on all classes and methods
- MSSQL-compatible SQL only — no PostgreSQL constructs

---

## Manual Steps Required (in order)

- [ ] `python manage.py makemigrations core` (for exclude_from_risk + position_history)
- [ ] `python manage.py makemigrations risk` (for all rm_ models)
- [ ] Review generated migrations before applying
- [ ] `python manage.py migrate --settings=portfolio_manager.settings_dev`
- [ ] Execute raw SQL for MSSQL views/functions (vw_portfolio_as_of, vw_portfolio_diff) via SSMS on neptune or sqlcmd — include SQL script as `scripts/create_portfolio_views.sql`
- [ ] `python manage.py rebuild_position_history --settings=portfolio_manager.settings_dev`
- [ ] Verify position_history row count and spot-check VDY, AAPL positions manually in SSMS
- [ ] `python manage.py seed_rm_universe --settings=portfolio_manager.settings_dev`
- [ ] Verify rm_security_universe has 30 rows (25 held + 3 benchmarks + 2 additional hedges)
- [ ] `python manage.py test core risk --settings=portfolio_manager.settings_test`
- [ ] Human review: open SSMS, run `SELECT * FROM vw_current_portfolio ORDER BY symbol` — verify all expected holdings appear and TAXABLE_CAR is excluded
- [ ] `git add -A && git commit -m "feat: M0-Spec-1 risk data models + position_history + seed data"`
- [ ] `git push origin feature/risk-m0-data-models`

---

## Human Approval Required

Human approval required before running any migration or database modification. Review generated migration files before `migrate`.

---

*M0-Spec-1 | Portfolio Manager Phase B | May 2026 | tdwdev*
