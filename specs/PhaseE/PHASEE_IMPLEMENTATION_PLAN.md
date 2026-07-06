# Phase E — Implementation Plan

**Goal:** Fix symbol resolution for yfinance and implement configurable fixed-price handling.

---

## Summary of Items

| # | Item | Spec | Priority | Effort |
|---|------|------|----------|--------|
| E1 | Symbol resolution function + SecurityUniverse integration | 01_symbol_resolution_and_fixed_prices | 🔴 High | 1 session |
| E2 | FixedPriceConfig model + admin UI | 01_symbol_resolution_and_fixed_prices | 🟡 Medium | 0.5 session |

---

## Execution Order: E1 → E2

E1 depends on SecurityUniverse being readable. E2 is independent but builds on E1's pattern.

---

## Detailed Breakdown

### E1: Symbol Resolution

**What to build:**
- `resolve_yahoo_symbol(symbol, primary_currency)` in `core/utils/prices.py`
- `get_fixed_price(symbol)` in `core/utils/prices.py` — queries SecurityUniverse or FixedPriceConfig
- Update `fetch_last_prices()` to resolve symbols before yfinance calls
- Update `load_prices_from_yfinance()` to resolve symbols before yf.download()
- Update `get_top_movers()` in `core/utils/portfolio.py`

**Key logic:**
```python
def resolve_yahoo_symbol(symbol, primary_currency=None):
    # If primary_currency is CAD and no suffix → append .TO
    # If primary_currency is USD → return as-is
    # If already has suffix (e.g., .TO, .X) → return as-is
    # If symbol is fixed-price → return None
```

### E2: FixedPriceConfig Model

**Model:**
```python
class FixedPriceConfig(models.Model):
    symbol = models.CharField(max_length=50, unique=True)
    price_cad = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    price_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    currency = models.CharField(max_length=3, default='CAD')
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
```

**Admin:** Register in `core/admin.py` with symbol, currency, price fields.

---

## Testing

- **D1:** ~5 new tests in `core/tests/test_prices.py`
- **D2:** ~2 new tests (model admin)

**Total: ~7 new tests**

---

## Migration Impact

- **E1:** No migration needed (code-only)
- **E2:** Yes — new `FixedPriceConfig` model requires migration

---

## Success Criteria

1. `VDY` resolves to `VDY.TO` when `primary_currency='CAD'`
2. `AAPL` stays `AAPL` when `primary_currency='USD'`
3. `VFV.TO` stays `VFV.TO` (already suffixed)
4. `RBF2010` returns fixed CAD 10 without yfinance
5. `RBF2014` returns fixed USD 10 without yfinance
6. All 122 existing tests pass
7. 7+ new tests pass
8. No more "No price available for VDY" warnings
9. Admin can add/edit/remove fixed-price entries
