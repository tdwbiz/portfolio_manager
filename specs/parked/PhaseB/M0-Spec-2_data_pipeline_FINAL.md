# Feature Spec: M0-Spec-2 — Data Pipeline

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/risk-m0-pipeline

---


**Architecture ref:** PM_PhaseB_Architecture_Final_v2.0.md
**Depends on:** M0-Spec-1 fully implemented and verified — all `rm_` tables exist, `core_position_history` is populated, seed data is loaded

---

## Context

This spec implements the data acquisition layer for the Risk Management Module. No risk calculations happen here — this spec's only job is to populate the following tables that were created in M0-Spec-1:

- `rm_market_price_history` — full OHLCV for all symbols
- `rm_volatility_summary` — precomputed volatility at 10/21/63/252-day windows
- `rm_fx_rates` — daily USD/CAD exchange rates
- `rm_economic_indicators` — macro indicators for regime detection

All downstream specs (M1 onward) read from these tables. They must never call yFinance or read Amibroker files directly — that is this spec's exclusive responsibility.

**Portfolio reality as of June 2, 2026 (latest export):**
- 28 unique held symbols (was 25 in May 24 export — BANK, CAGE, DRAM added)
- Benchmarks: QQQ, SPY, XIU.TO
- Hedges catalogued: SOXS, SOXL, PSQ, SPXS
- New account detected with blank nickname in the June 2 export — data pipeline must tolerate account structure changes gracefully
- CAD-denominated symbols: VDY, VFV, XDIV, XEQT, BANK, CAGE, RBF2010, RBF2014, ICSH, VOO is USD
- Canadian yFinance tickers often require `.TO` suffix — see §3.4

**Stack:**
- Python 3.12 / Django 5.1
- MSSQL 2025 (dev: PortfolioDEV)
- yFinance: primary external data source
- Amibroker/AmiQuote: primary source for OHLCV when available (CSV exports)
- Project location: `C:\AI-DevTeam\projects\portfolio_manager`
- Management commands run from: `C:\AI-DevTeam\projects\portfolio_manager\`

---

## Requirements

### 1. DataSource Abstraction Layer

Create `risk/services/data_source.py`.

This is the single interface all downstream code uses to request price data. It must be source-agnostic — callers never know or care whether data came from Amibroker or yFinance.

```python
class DataSource:
    """
    Source-agnostic price series provider.
    
    Priority: Amibroker data (data_source='amibroker') → yFinance fallback (data_source='yfinance').
    Returns data already persisted in rm_market_price_history.
    Does NOT fetch — fetch is done by management commands.
    """
    
    def get_price_series(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        field: str = 'adj_close'
    ) -> pd.Series:
        """
        Return a daily price series for symbol between start_date and end_date (inclusive).
        Index: DatetimeIndex. Values: Decimal → float.
        Raises DataNotAvailableError if no data exists for the requested range.
        Prioritises amibroker rows; fills gaps with yfinance rows.
        """
    
    def get_ohlcv(
        self,
        symbol: str,
        start_date: date,
        end_date: date
    ) -> pd.DataFrame:
        """
        Return OHLCV DataFrame for symbol.
        Columns: open, high, low, close, adj_close, volume.
        Index: DatetimeIndex.
        """
    
    def get_latest_price(self, symbol: str) -> Decimal:
        """Return the most recent close price for symbol from rm_market_price_history."""
    
    def get_available_date_range(self, symbol: str) -> tuple[date, date]:
        """Return (earliest_date, latest_date) for symbol in rm_market_price_history."""
    
    def get_fx_rate(self, date: date) -> Decimal:
        """Return USD/CAD rate for given date. Falls back to nearest prior date if exact date missing."""
    
    def get_economic_indicator(self, indicator_name: str, start_date: date, end_date: date) -> pd.Series:
        """Return time series for a named economic indicator from rm_economic_indicators."""


class DataNotAvailableError(Exception):
    """Raised when requested price data does not exist in rm_market_price_history."""
    pass
```

**Rules:**
- `DataSource` reads only from `rm_market_price_history`, `rm_fx_rates`, `rm_economic_indicators`. It never calls yFinance.
- All M1, M2, M3 code imports `DataSource` from this module. Direct yFinance calls anywhere outside this file are a bug.
- Thread-safe: multiple management commands may call it concurrently.

---

### 2. Amibroker CSV Import

Create `risk/management/commands/import_amibroker_prices.py`.

Amibroker/AmiQuote exports CSVs per symbol in a configured watch folder. Column format varies by export setting — the command must handle both common formats.

**Expected CSV formats from AmiQuote:**

Format A (standard AmiQuote export):
```
Date,Open,High,Low,Close,Volume
20240103,185.85,186.33,183.88,185.64,50123456
```

Format B (with Adj Close):
```
Date,Open,High,Low,Close,Adj Close,Volume
20240103,185.85,186.33,183.88,185.64,184.21,50123456
```

Date format: `YYYYMMDD` (no separators).

**Command signature:**
```
python manage.py import_amibroker_prices [--watch-folder PATH] [--symbol SYM] [--dry-run]
```

**Algorithm:**
```
1. Resolve watch folder from --watch-folder argument or settings.AMIBROKER_WATCH_FOLDER
2. If --symbol: process only that symbol's CSV file (e.g. VDY.csv or VDY.TO.csv)
   Else: process all CSV files in the watch folder
3. For each CSV file:
   a. Derive symbol from filename (strip .csv, strip .TO suffix for storage)
   b. Verify symbol exists in rm_security_universe (warn and skip if not)
   c. Parse CSV — detect format A or B by column count
   d. Detect currency from rm_security_universe.primary_currency for this symbol
   e. For adj_close: if Format B, use Adj Close column; if Format A, use Close (no adjustment data)
   f. For each row: INSERT or SKIP if (symbol, date) already exists with data_source='amibroker'
      Use MSSQL MERGE or INSERT ... WHERE NOT EXISTS — no upsert
   g. After insert, compute and upsert rm_volatility_summary for this symbol (see §5)
   h. Log: rows inserted, rows skipped, symbol processed
4. Print summary: total files processed, total rows inserted, total skipped, any symbols not in universe
```

**Settings required** (add to `settings_dev.py`):
```python
AMIBROKER_WATCH_FOLDER = r'C:\AI-Workspace\amibroker_exports'
```

**MSSQL insert pattern (no upsert — only insert new rows):**
```sql
INSERT INTO rm_market_price_history (symbol, date, open, high, low, close, adj_close, volume, currency, data_source)
SELECT :symbol, :date, :open, :high, :low, :close, :adj_close, :volume, :currency, 'amibroker'
WHERE NOT EXISTS (
    SELECT 1 FROM rm_market_price_history
    WHERE symbol = :symbol AND date = :date AND data_source = 'amibroker'
);
```

**Canadian symbol handling:**
- Amibroker may export VDY as `VDY.csv` or `VDY.TO.csv`
- Strip `.TO` suffix when matching to `rm_security_universe.symbol` (stored without `.TO`)
- Store in `rm_market_price_history` without `.TO` suffix (consistent with universe)
- Log a warning if `.TO` stripping was applied so it's visible in output

---

### 3. yFinance Price Fetcher

Create `risk/management/commands/fetch_yfinance_prices.py` and supporting service `risk/services/yfinance_client.py`.

**Command signature:**
```
python manage.py fetch_yfinance_prices [--symbol SYM] [--start YYYY-MM-DD] [--force-full]
```

Default behaviour (no `--start`): incremental — detect last fetched date per symbol and fetch only from that date forward.

With `--force-full`: ignore existing data, fetch maximum available history (up to 10 years).

**Algorithm:**
```
1. Build fetch list:
   - If --symbol: fetch that symbol only
   - Else: fetch all active symbols from rm_security_universe (is_active=True)
   - Include benchmarks (XIU.TO, SPY, QQQ) and any is_held=True symbols
2. For each symbol:
   a. Resolve yFinance ticker (see §3.4 ticker mapping)
   b. Determine fetch start date:
      - If --force-full: go back 10 years from today
      - If data exists (any data_source): fetch from last_date + 1 business day
      - If no data exists: fetch from 10 years ago (full history on first run)
   c. Call yfinance.download(ticker, start=start_date, end=today+1, auto_adjust=True)
      Set timeout=30s; retry up to 3 times with 5s backoff on failure
   d. For each row returned:
      - Skip if (symbol, date) already exists with data_source='amibroker' (amibroker wins)
      - Insert if (symbol, date) does not exist at all
      - Insert if (symbol, date) exists only as 'yfinance' (skip duplicate)
   e. On any yFinance error: log warning, continue to next symbol — do not abort run
3. After all symbols: call compute_volatility_summaries() for all symbols updated
4. Print summary: symbols fetched, date ranges, rows inserted, symbols with errors
```

**yfinance call pattern:**
```python
import yfinance as yf

def fetch_symbol(ticker: str, start: date, end: date) -> pd.DataFrame:
    """
    Returns DataFrame with columns: Open, High, Low, Close, Volume (adj_close = Close when auto_adjust=True).
    Index: DatetimeIndex. May return empty DataFrame if no data available.
    """
    df = yf.download(ticker, start=start.isoformat(), end=end.isoformat(),
                     auto_adjust=True, progress=False)
    return df
```

#### 3.4 yFinance Ticker Mapping

Canadian ETFs listed on TSX require the `.TO` suffix in yFinance but are stored in the database without it. Create a mapping table or use `rm_security_universe.amibroker_symbol` field for this purpose.

**yFinance ticker resolution rules:**
1. If `rm_security_universe.amibroker_symbol` is populated, use that as the yFinance ticker
2. Else if `rm_security_universe.primary_currency = 'CAD'`, append `.TO` to symbol
3. Else use symbol as-is

**Known mappings for this portfolio (seed into `rm_security_universe.amibroker_symbol`):**

| Symbol | yFinance Ticker | Notes |
|---|---|---|
| VDY | VDY.TO | Vanguard FTSE Canadian High Div |
| VFV | VFV.TO | Vanguard S&P 500 CAD |
| XDIV | XDIV.TO | iShares Canadian Dividend |
| XEQT | XEQT.TO | iShares Core Equity Portfolio |
| BANK | BANK.TO | BMO Equal Weight Banks ETF |
| CAGE | CAGE.TO | CI Global Asset Allocation ETF |
| XIU.TO | XIU.TO | Benchmark only — already has suffix |
| RBF2010 | — | Mutual fund — yFinance unavailable; skip gracefully |
| RBF2014 | — | Mutual fund — yFinance unavailable; skip gracefully |
| ICSH | ICSH | US-listed; no suffix |
| ADX | ADX | US-listed CEF; no suffix |
| DRAM | DRAM | US-listed semiconductor ETF |

**Mutual fund handling:** RBF2010 and RBF2014 are RBC money market funds not available via yFinance. Skip them with a `INFO` log: "Symbol RBF2010: mutual fund not available via yFinance — skipping". Do not raise an error.

---

### 4. FX Rate Fetcher

Create `risk/management/commands/fetch_fx_rates.py`.

**Command signature:**
```
python manage.py fetch_fx_rates [--start YYYY-MM-DD] [--force-full]
```

**Algorithm:**
```
1. Determine start date:
   - If --force-full: go back 10 years
   - If data exists in rm_fx_rates: fetch from last date + 1
   - If no data: fetch from 10 years ago
2. Fetch USD/CAD via yfinance.download('CAD=X', start=start, end=today+1, auto_adjust=True)
   The Close column of CAD=X is the USD per 1 CAD rate.
   Convert: usd_cad_rate = 1 / close  (we store how many CAD per 1 USD)
3. For each date returned:
   INSERT INTO rm_fx_rates (date, usd_cad_rate, data_source)
   WHERE NOT EXISTS (SELECT 1 FROM rm_fx_rates WHERE date = :date)
4. Verify: most recent rate should be within 3 business days of today; warn if not
```

**Storage convention:** `usd_cad_rate` = number of CAD per 1 USD. Example: if 1 USD = 1.3842 CAD, store 1.384200.

This matches the live export convention (June 2, 2026: USD/CAD = 1.38415).

---

### 5. Economic Indicators Fetcher

Create `risk/management/commands/fetch_economic_indicators.py`.

These are needed for M2 regime detection. Fetch and store in `rm_economic_indicators`.

**Command signature:**
```
python manage.py fetch_economic_indicators [--start YYYY-MM-DD] [--force-full]
```

**Indicators to fetch and their yFinance tickers:**

| indicator_name | yFinance ticker | Notes |
|---|---|---|
| `vix_close` | `^VIX` | CBOE Volatility Index daily close |
| `yield_10y` | `^TNX` | US 10-year Treasury yield (values in %, e.g. 4.23) |
| `yield_2y` | `^IRX` | US 2-year Treasury yield (values in %; note: ^IRX is 13-week bill, use as proxy or document limitation) |
| `yield_curve_slope` | computed | `yield_10y` minus `yield_2y` — compute after fetch, store as separate row |

**Note on yield_2y:** `^IRX` is the 13-week T-bill rate, not the 2-year. This is a known limitation. Document in code comments that FRED's `DGS2` is the correct 2-year series but requires a FRED API key. Use `^IRX` as a proxy for now; upgrade path noted in `rm_scoring_config` as a future config item.

**Indicators NOT available via free yFinance (document as future upgrade):**
- `cpi_yoy`: Bureau of Labor Statistics; FRED series `CPIAUCSL`. Skip for now; store placeholder.
- `pmi_composite`: ISM/Markit; no free API. Skip for now.
- `gdp_growth_qoq`: BEA; FRED series `A191RL1Q225SBEA`. Skip for now.
- `unemployment_rate`: FRED series `UNRATE`. Skip for now.

**Algorithm:**
```
1. For each fetchable indicator (vix_close, yield_10y, yield_2y):
   a. Determine start date (incremental or force-full, same pattern as price fetcher)
   b. Download via yfinance.download(ticker, ...)
   c. Use Close column as the indicator value
   d. For yield_10y and yield_2y: values are already in % (e.g. 4.23 means 4.23%)
   e. INSERT rows into rm_economic_indicators WHERE NOT EXISTS
2. After yield_10y and yield_2y are stored:
   Compute yield_curve_slope for each date where both values exist:
   slope = yield_10y - yield_2y
   INSERT into rm_economic_indicators (date, 'yield_curve_slope', slope, 'computed')
   Skip if row already exists
3. Log: rows inserted per indicator, date ranges covered, any gaps detected
```

---

### 6. Volatility Summary Computation

Create `risk/services/volatility.py` with function `compute_volatility_summaries`.

This is called by both the Amibroker importer and the yFinance fetcher after new price data is loaded. It is not a standalone management command (it is called internally), but expose it as one too for manual re-runs:

```
python manage.py compute_volatility [--symbol SYM] [--date YYYY-MM-DD]
```

**Algorithm:**
```
For each symbol in rm_security_universe (or just the specified symbol):
    For each lookback_window in [10, 21, 63, 252]:
        1. Load the last (lookback_window + 1) closing prices from rm_market_price_history
           (ordered by date DESC, limit lookback_window + 1)
        2. Compute daily log returns: ln(price_t / price_t-1)
        3. Compute std deviation of log returns over the window
        4. Annualise: volatility_annualised = std_dev * sqrt(252)
        5. UPSERT into rm_volatility_summary:
           (symbol, calc_date=today, lookback_days=window, volatility_annualised, data_source)
           If row exists: UPDATE volatility_annualised and calc_timestamp
           If not: INSERT
    
    Skip symbol if fewer than (lookback_window + 2) price rows available — log a warning
```

**MSSQL UPSERT pattern:**
```sql
MERGE rm_volatility_summary AS target
USING (VALUES (:symbol, :calc_date, :lookback_days, :vol, :source)) 
    AS source (symbol, calc_date, lookback_days, volatility_annualised, data_source)
ON target.symbol = source.symbol 
   AND target.calc_date = source.calc_date 
   AND target.lookback_days = source.lookback_days
WHEN MATCHED THEN
    UPDATE SET volatility_annualised = source.volatility_annualised,
               data_source = source.data_source
WHEN NOT MATCHED THEN
    INSERT (symbol, calc_date, lookback_days, volatility_annualised, data_source)
    VALUES (source.symbol, source.calc_date, source.lookback_days, 
            source.volatility_annualised, source.data_source);
```

---

### 7. Gap Detection

Create `risk/services/gap_detection.py` with function `detect_price_gaps`.

**Gap definition:** A symbol has a gap if there are more than 5 consecutive business days with no price data in `rm_market_price_history`, within the last 252 trading days.

Business days: Monday–Friday excluding US market holidays. Use a simple approach: any day that is not Saturday/Sunday, and for which at least one other held symbol has a price record (i.e. the market was open). This avoids needing a holiday calendar.

```python
def detect_price_gaps(symbols: list[str] | None = None) -> list[GapReport]:
    """
    For each symbol, detect runs of missing business days in recent price history.
    Returns list of GapReport(symbol, gap_start, gap_end, gap_days, severity).
    severity: 'warning' (5 days) or 'critical' (>10 days).
    """
```

**Surface gap reports:**
- Log to Django logger at WARNING level for each gap detected
- Store in `rm_data_quality_log` table (see §8) with gap details
- Expose in Django admin so gaps are visible without running a command

---

### 8. Data Quality Log Table

Add one additional table to the `risk` app. This was not in M0-Spec-1 but is needed by this spec — add the model and migration here.

```python
class DataQualityLog(models.Model):
    """Records data quality events: gaps, fetch failures, symbol mismatches."""
    
    logged_at = models.DateTimeField(auto_now_add=True)
    symbol = models.CharField(max_length=20, blank=True)
    event_type = models.CharField(max_length=30)
    # Values: 'gap_detected', 'fetch_failed', 'symbol_not_in_universe',
    #         'mutual_fund_skipped', 'ticker_mapping_applied', 'insufficient_history'
    severity = models.CharField(max_length=10)  # 'info', 'warning', 'critical'
    description = models.TextField()
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    gap_days = models.IntegerField(null=True, blank=True)
    resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'rm_data_quality_log'
        indexes = [
            models.Index(fields=['symbol', 'logged_at']),
            models.Index(fields=['event_type', 'severity']),
            models.Index(fields=['resolved']),
        ]
```

Register in `risk/admin.py` with list_display, filters on severity and resolved, and a "Mark resolved" action.

---

### 9. Master Refresh Command

Create `risk/management/commands/refresh_market_data.py`.

This is the command called daily by cron or Windows Task Scheduler. It orchestrates all fetch commands in the correct order.

**Command signature:**
```
python manage.py refresh_market_data [--full] [--dry-run] [--symbol SYM]
```

**Execution order:**
```
1. fetch_fx_rates               (needed first; prices may be CAD-denominated)
2. import_amibroker_prices      (primary source; runs if AMIBROKER_WATCH_FOLDER has new files)
3. fetch_yfinance_prices        (fills gaps; skips symbols already covered by Amibroker for that date)
4. fetch_economic_indicators    (VIX, yields)
5. compute_volatility           (after prices are current)
6. detect_price_gaps and log    (after all fetches complete)
7. Print final summary report
```

**Scheduling on saturn (Windows):**
Add to Windows Task Scheduler:
```
Program: C:\Python312\python.exe
Arguments: manage.py refresh_market_data
Start in: C:\AI-DevTeam\projects\portfolio_manager
Trigger: Daily at 6:00 PM ET (after US market close)
```

**Scheduling on mercury (production, Linux):**
Add to cron:
```
0 18 * * 1-5 cd /path/to/portfolio_manager && /path/to/venv/bin/python manage.py refresh_market_data >> /var/log/portfolio/refresh.log 2>&1
```

---

### 10. Updated `rm_security_universe` Seed Additions

The June 2, 2026 portfolio export shows three new symbols not in the May 24 seed. Add these to the `seed_rm_universe` command from M0-Spec-1, or create a separate `seed_rm_universe_update` command.

**New symbols to add:**

| Symbol | Name | Asset Class | primary_currency | is_held | sector | Notes |
|---|---|---|---|---|---|---|
| BANK | BMO Equal Weight Banks Index ETF | etf_equity | CAD | True | Financial | Canadian banks ETF; listed on TSX as BANK.TO |
| CAGE | CI Global Asset Allocation ETF | etf_equity | CAD | True | Multi-asset | Listed on TSX; verify full name against yFinance |
| DRAM | VanEck Semiconductor ETF (Memory) | etf_equity | USD | True | Technology | US-listed; semiconductor/memory focus |

**Note to developer:** Confirm CAGE's full name and exact ETF category via yFinance before seeding — the ticker CAGE on TSX may be CI Galaxy Ethereum ETF (crypto) rather than asset allocation. If crypto: set `use_jump_diffusion=True` and `asset_class='crypto_etf'`. If multi-asset: `asset_class='etf_equity'`. This distinction matters for M2 regime sensitivity.

Also update `rm_security_index_exposure` seed for new symbols:
- BANK → DIVIDEND_CA (0.90), TSX (1.00)
- DRAM → SEMICONDUCTOR (0.80), TECHNOLOGY (0.20)
- CAGE → verify composition before seeding; placeholder: BROAD_MARKET (1.00)

---

### 11. Settings

Add to `settings_dev.py` and `settings_test.py`:

```python
# Amibroker export watch folder
AMIBROKER_WATCH_FOLDER = r'C:\AI-Workspace\amibroker_exports'

# yFinance fetch settings
YFINANCE_DEFAULT_HISTORY_YEARS = 10   # Full history on first fetch
YFINANCE_RETRY_ATTEMPTS = 3
YFINANCE_RETRY_DELAY_SECONDS = 5
YFINANCE_REQUEST_TIMEOUT = 30

# Gap detection threshold
PRICE_GAP_WARNING_DAYS = 5    # Warn if gap > 5 business days
PRICE_GAP_CRITICAL_DAYS = 10  # Critical if gap > 10 business days
```

---

### 12. Tests

File: `risk/tests/test_data_pipeline.py`

```python
class DataSourceTests(TestCase):
    """Tests for DataSource abstraction layer."""
    
    def test_get_price_series_returns_correct_dates(self):
        # Seed 5 rows into rm_market_price_history for AAPL
        # Call get_price_series(AAPL, start, end)
        # Assert series has correct index and values
    
    def test_amibroker_data_takes_priority_over_yfinance(self):
        # Seed same (symbol, date) with both amibroker and yfinance values
        # get_price_series should return amibroker value
    
    def test_get_price_series_raises_when_no_data(self):
        # Request price series for symbol with no data
        # Assert raises DataNotAvailableError
    
    def test_get_fx_rate_falls_back_to_nearest_prior_date(self):
        # Seed rate for 2026-01-13 (Tuesday)
        # Request rate for 2026-01-14 (Wednesday, no data)
        # Should return 2026-01-13 rate
    
    def test_get_fx_rate_raises_when_no_prior_date(self):
        # Request rate for date before any seeded data
        # Should raise DataNotAvailableError


class VolatilityComputationTests(TestCase):
    """Tests for volatility computation logic."""
    
    def test_annualised_volatility_formula(self):
        # Seed 253 days of synthetic prices for TEST_SYM with known std dev
        # E.g. prices = 100 * (1 + 0.01) ** range(253) with small noise
        # Compute 252-day volatility
        # Assert result is within 1% of expected annualised vol
    
    def test_volatility_skipped_when_insufficient_history(self):
        # Seed only 15 rows for a symbol
        # compute_volatility for 21-day window should log warning and skip
        # rm_volatility_summary should have no row for that symbol/window
    
    def test_volatility_upsert_updates_existing_row(self):
        # Seed volatility row for (SYM, today, 21)
        # Run compute_volatility again with updated prices
        # Assert the row is updated (not duplicated)
    
    def test_all_four_windows_computed(self):
        # Seed 300 rows for a symbol
        # Run compute_volatility
        # Assert rm_volatility_summary has 4 rows (10, 21, 63, 252) for that symbol


class IncrementalFetchTests(TestCase):
    """Tests for incremental fetch logic (not live yFinance — use mocked responses)."""
    
    def setUp(self):
        # Mock yfinance.download to return a fixed DataFrame
        pass
    
    def test_incremental_fetch_only_adds_missing_dates(self):
        # Seed rm_market_price_history with dates up to 2026-05-01
        # Run fetch with mocked yFinance returning 2026-05-01 to 2026-05-31
        # Assert only rows after 2026-05-01 are inserted
    
    def test_yfinance_does_not_overwrite_amibroker_rows(self):
        # Seed (AAPL, 2026-01-15) with data_source='amibroker'
        # Run yFinance fetch which returns data for 2026-01-15
        # Assert the amibroker row is unchanged; no yfinance row inserted for that date
    
    def test_force_full_refetches_entire_history(self):
        # Seed some rows
        # Run with --force-full
        # Assert fetch range starts from 10 years ago regardless of existing data


class GapDetectionTests(TestCase):
    
    def test_gap_detected_when_more_than_5_business_days_missing(self):
        # Seed prices with a 7-day gap (e.g. 2026-03-01, then skip to 2026-03-12)
        # Run detect_price_gaps
        # Assert GapReport returned for that symbol with gap_days=7
    
    def test_no_gap_reported_for_weekends(self):
        # Seed Friday price and Monday price (2 calendar days = 0 business days gap)
        # Assert no gap reported
    
    def test_gap_written_to_data_quality_log(self):
        # After detect_price_gaps, assert DataQualityLog row created


class AmibrokerImportTests(TestCase):
    
    def test_standard_format_a_import(self):
        # Create a temp CSV file with Format A data
        # Run import
        # Assert rows in rm_market_price_history with data_source='amibroker'
    
    def test_format_b_with_adj_close(self):
        # Create temp CSV with adj_close column
        # Assert adj_close column populated correctly in rm_market_price_history
    
    def test_canadian_to_suffix_stripped(self):
        # Create VDY.TO.csv
        # Import
        # Assert stored as symbol='VDY', not 'VDY.TO'
    
    def test_symbol_not_in_universe_skipped_with_warning(self):
        # Create UNKNOWN.csv for a symbol not in rm_security_universe
        # Import
        # Assert no rows inserted; DataQualityLog has 'symbol_not_in_universe' event
    
    def test_duplicate_amibroker_row_skipped(self):
        # Seed (VDY, 2026-01-15) with data_source='amibroker'
        # Import CSV that includes 2026-01-15
        # Assert row count unchanged (duplicate skipped)
```

Run with: `python manage.py test risk.tests.test_data_pipeline --settings=portfolio_manager.settings_test`

---

## Constraints

- **DO NOT** implement any risk calculations — that is M1 and beyond
- **DO NOT** modify any Phase A tables or models
- **DO NOT** push to git (manual step)
- All database writes use MSSQL-compatible SQL — no `ON CONFLICT`, no `RETURNING`
- yFinance calls are wrapped in try/except with retry logic — a single symbol failure must not abort the entire run
- Mutual funds (RBF2010, RBF2014) and XIU.TO (benchmark) fetch gracefully even if yFinance returns no data
- `DataSource` class is the exclusive interface for all downstream data access
- All management commands print a human-readable summary at completion
- PEP 8 throughout; full type hints and docstrings on all public functions and classes

---

## Manual Steps Required (in order)

- [ ] Add `AMIBROKER_WATCH_FOLDER` to `settings_dev.py`
- [ ] `python manage.py makemigrations risk` (for DataQualityLog model)
- [ ] `python manage.py migrate --settings=portfolio_manager.settings_dev`
- [ ] `python manage.py seed_rm_universe_update --settings=portfolio_manager.settings_dev` (adds BANK, CAGE, DRAM)
- [ ] `python manage.py fetch_fx_rates --force-full --settings=portfolio_manager.settings_dev`
- [ ] Verify rm_fx_rates has rows from ~2016 onwards, most recent within 3 days of today
- [ ] `python manage.py fetch_economic_indicators --force-full --settings=portfolio_manager.settings_dev`
- [ ] Verify rm_economic_indicators has vix_close, yield_10y, yield_2y, yield_curve_slope
- [ ] Place Amibroker export CSVs in AMIBROKER_WATCH_FOLDER (or skip if no Amibroker data available yet)
- [ ] `python manage.py import_amibroker_prices --settings=portfolio_manager.settings_dev` (if CSVs available)
- [ ] `python manage.py fetch_yfinance_prices --force-full --settings=portfolio_manager.settings_dev`
- [ ] Verify rm_market_price_history has rows for all 28 held symbols + benchmarks
- [ ] `python manage.py compute_volatility --settings=portfolio_manager.settings_dev`
- [ ] Verify rm_volatility_summary has 4 rows per symbol (10, 21, 63, 252 days)
- [ ] `python manage.py test risk.tests.test_data_pipeline --settings=portfolio_manager.settings_test`
- [ ] Open SSMS: spot-check VDY close prices vs known values (74.24 on May 24, 73.74 on Jun 2)
- [ ] Confirm gap detection runs clean (no critical gaps)
- [ ] `git add -A && git commit -m "feat: M0-Spec-2 data pipeline + volatility + gap detection"`
- [ ] `git push origin feature/risk-m0-pipeline`

---

## Human Approval Required

Human approval required before running any migration or bulk data fetch against PortfolioDEV.

---

*M0-Spec-2 | Portfolio Manager Phase B | June 2026 | tdwdev*
