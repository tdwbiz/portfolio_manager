# Phase E: Symbol Resolution and Fixed Price Handling

**Goal:** Resolve symbol suffixes (e.g., `VDY` → `VDY.TO`) based on `SecurityUniverse.primary_currency`, and handle fixed-price securities (`RBF2010`, `RBF2014`) via a configurable lookup — never hardcoded in price fetching logic.

---

## Problem Statement

When fetching prices from yfinance, symbols are passed as-is from the database:

- **Canadian symbols** (e.g., `VDY`, `VFV.TO`) need `.TO` suffix for yfinance to find them
- **US symbols** (e.g., `AAPL`) work as-is
- **Fixed-price securities** (`RBF2010` = CAD 10, `RBF2014` = USD 10) should NOT be fetched from yfinance at all

Currently, the warning `"No price available for VDY"` appears because yfinance can't find `VDY` — it needs `VDY.TO`.

---

## Requirements

### 1. Symbol Resolution Function

Create a new utility function in `core/utils/prices.py`:

```python
def resolve_yahoo_symbol(symbol: str, primary_currency: str | None = None) -> str:
    """Resolve a database symbol to its yfinance-compatible ticker.
    
    Rules:
    - If primary_currency is 'CAD' and symbol doesn't already have a suffix, append '.TO'
    - If primary_currency is 'USD', return symbol as-is
    - If primary_currency is None, check SecurityUniverse
    - If symbol is in the fixed_prices list, return None (skip yfinance)
    - Symbols that already have a suffix (e.g., 'VFV.TO') are returned as-is
    """
```

### 2. Fixed Price Lookup

Create a configurable list for fixed-price securities. This should NOT be hardcoded in the price fetching code.

**Chosen: Option A** — Use a new model `FixedPriceConfig` with fields:
- `symbol` (CharField, unique)
- `price_cad` (DecimalField, max_digits=12, decimal_places=6, default=0)
- `price_usd` (DecimalField, max_digits=12, decimal_places=6, default=0)
- `currency` (CharField — 'CAD' or 'USD')
- `notes` (TextField, blank)
- `is_active` (BooleanField, default=True)
- `created_at` (DateTimeField, auto_now_add=True)
- `updated_at` (DateTimeField, auto_now=True)

**Rationale:** Model-based approach allows admin users to add/edit/remove fixed-price entries without code changes. The model lives in the `core` app alongside `BrandingConfig` and `DividendSchedule`.

### 3. Update `fetch_last_prices`

Modify `fetch_last_prices()` in `core/utils/prices.py`:

1. Accept an optional `symbols_info` dict: `{symbol: {"primary_currency": "CAD", "fixed_price": True/False}}`
   - Or better: query `SecurityUniverse` internally for the symbol metadata
2. For each symbol:
   - Check if it's a fixed-price symbol → return fixed price, skip yfinance
   - Resolve symbol → `resolve_yahoo_symbol(symbol)`
   - Fetch price from resolved symbol
3. Cache the resolved symbol mapping to avoid repeated DB lookups

### 4. Update `load_prices_from_yfinance`

Modify `load_prices_from_yfinance()` in `core/utils/prices.py`:

1. Before calling `yf.download()`, resolve all symbols
2. Replace original symbols with resolved symbols in the download call
3. Map results back to original symbols after fetching

### 5. Update `get_top_movers`

Modify `get_top_movers()` in `core/utils/portfolio.py`:

1. Resolve symbols before passing to `yf.download()`
2. Map results back to original symbols
3. Skip fixed-price symbols entirely

### 6. Update `compute_portfolio_value_series` and related functions

These functions use `load_prices_from_yfinance()` internally, so they'll benefit from the fix automatically.

---

## Architecture

```
Transaction (symbol='VDY')
    → compute_positions()
        → enrich_positions_with_prices()
            → fetch_last_prices(['VDY'])
                → resolve_yahoo_symbol('VDY', 'CAD')
                → returns 'VDY.TO'
                → yf.Ticker('VDY.TO') → success!
```

```
Transaction (symbol='RBF2010')
    → compute_positions()
        → enrich_positions_with_prices()
            → fetch_last_prices(['RBF2010'])
                → is_fixed_price('RBF2010') → True
                → returns Decimal('10.00') directly
                → NO yfinance call
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `core/utils/prices.py` | Add `resolve_yahoo_symbol()`, add `get_fixed_price()`, update `fetch_last_prices()`, update `load_prices_from_yfinance()` |
| `core/utils/portfolio.py` | Update `get_top_movers()` to resolve symbols |
| `core/models.py` | Add `FixedPriceConfig` model |
| `core/admin.py` | Register `FixedPriceConfig` admin |
| `risk/models.py` | — (read-only, `SecurityUniverse.primary_currency`) |
| `core/tests/test_prices.py` | New test file for symbol resolution and fixed prices |

---

## Test Plan

1. **`test_resolve_yahoo_symbol`** — Test CAD symbols get `.TO`, US symbols don't, already-suffixed symbols are unchanged
2. **`test_fetch_last_prices_with_resolution`** — Test that `VDY` resolves to `VDY.TO` and price is fetched
3. **`test_fixed_price_skip_yfinance`** — Test that `RBF2010` returns fixed price without yfinance call
4. **`test_get_top_movers_resolution`** — Test movers widget works with resolved symbols
5. **`test_portfolio_value_series_resolution`** — Test chart uses resolved symbols

---

## Success Criteria

1. No `"No price available for VDY"` warnings — `VDY` resolves to `VDY.TO`
2. `RBF2010` returns CAD 10 without calling yfinance
3. `RBF2014` returns USD 10 without calling yfinance
4. All existing 122 tests still pass
5. New tests cover symbol resolution and fixed price handling

---

## Implementation Notes

- **Caching:** Cache the `Symbol → resolved symbol` mapping to avoid repeated SecurityUniverse DB queries
- **Backward compatibility:** Symbols that already have suffixes (e.g., `VFV.TO`) should pass through unchanged
- **Error handling:** If symbol resolution fails, fall back to the original symbol (existing behavior)
- **Admin UI:** If using a model for fixed prices, allow admin users to add/edit/remove entries
