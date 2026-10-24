# Phase E — Implementation Plan

**Goal:** Fix symbol resolution for yfinance, implement fixed-price handling, and reconcile CSV transactions with RBC PDF data.

---

## Summary of Items

| # | Item | Spec | Priority | Effort |
|---|------|------|----------|--------|
| E1 | Symbol resolution + fixed prices | 01_symbol_resolution_and_fixed_prices | 🔴 High | 1 session |
| E3-A | Gap analysis report | 02_transaction_reconciliation_and_statement_generation | 🟡 Medium | 0.5 session |
| E3-B | Generate monthly statements from transactions | 02_transaction_reconciliation_and_statement_generation | 🔴 High | 1 session |
| E3-C | Generate annual reports from transactions | 02_transaction_reconciliation_and_statement_generation | 🟡 Medium | 0.5 session |
| E3-D | Reconciliation engine (CSV vs RBC PDF) | 02_transaction_reconciliation_and_statement_generation | 🔴 High | 1.5 sessions |

**Estimated total: ~5 sessions**

---

## Execution Order: E1 → E3-A → E3-B → E3-C → E3-D

---

## Detailed Breakdown

### E1: Symbol Resolution + Fixed Prices

**Spec:** `01_symbol_resolution_and_fixed_prices.md`

**What to build:**
- `resolve_yahoo_symbol(symbol, primary_currency)` — adds `.TO` for CAD symbols
- `FixedPriceConfig` model (in `core/models.py`) — configurable fixed prices, admin editable
- `get_fixed_price(symbol)` — returns fixed price from model, skips yfinance
- Update `fetch_last_prices()`, `load_prices_from_yfinance()`, `get_top_movers()` to use resolution

**Key logic:**
```
CAD symbol (VDY) → VDY.TO (for yfinance)
USD symbol (AAPL) → AAPL (no change)
Already suffixed (VFV.TO) → VFV.TO (no change)
Fixed price (RBF2010) → return Decimal('10.00'), skip yfinance
```

---

### E3-A: Gap Analysis Report

**Spec:** `02_transaction_reconciliation_and_statement_generation.md`

**What to build:**
- View `gap_analysis()` — shows per-account completeness metrics
- Template `gap_analysis.html` — table with earliest dates, gap counts, coverage %
- URL: `/accounts/gap-analysis/`
- **No new models** — reads existing `core_transaction` and `core_rbc_*` tables

**Output columns:**
- account_id, earliest_csv_date, earliest_continuous_start/end, days_missing_continuous, earliest_rbc_dates, csv_coverage_pct

---

### E3-B: Monthly Statement Generation

**Spec:** `02_transaction_reconciliation_and_statement_generation.md`

**What to build:**
- New model `CoreMonthlyStatement` — one row per (account, month, currency)
- Fields: beginning/ending market value, buys, sells, dividends, interest, commission, net flow
- New management command `generate_monthly_statements`
- New view `generate_monthly_statements()` — UI to trigger generation
- New utility function in `core/utils/statements.py`

**Generation logic:**
1. Group transactions by account, month, currency
2. Compute beginning MV from prior month's ending MV
3. Sum buys, sells, income, commission for each month
4. Store in `CoreMonthlyStatement`

---

### E3-C: Annual Report Generation

**Spec:** `02_transaction_reconciliation_and_statement_generation.md`

**What to build:**
- New model `CoreAnnualReport` — one row per (account, year, currency)
- Fields: beginning/ending value, deposits, withdrawals, net return, invested amount
- New management command `generate_annual_reports`
- New view `generate_annual_reports()` — UI to trigger generation
- Aggregates from `CoreMonthlyStatement` (not raw transactions)

---

### E3-D: Reconciliation Engine

**Spec:** `02_transaction_reconciliation_and_statement_generation.md`

**What to build:**
- New model `CoreReconciliation` — tracks matches/discrepancies
- Fields: CSV values vs RBC values for beginning/ending, buys/sells, income, transactions
- Fields: matching (bool), discrepancy_amount, discrepancy_notes
- New management command `reconcile`
- New view `reconcile_account()` — UI to trigger reconciliation
- New utility function in `core/utils/reconciliation.py`
- Gap filling strategy: flag CSV gaps where RBC has data

**Reconciliation logic:**
1. Load CSV-generated monthly/annual statements
2. Load RBC PDF monthly/annual statements
3. Compare values month/quarter
4. Record matches and discrepancies
5. Flag gaps requiring manual review

---

## Testing

| Item | Tests |
|------|-------|
| E1 | ~5 new tests in `core/tests/test_prices.py` |
| E3-A | ~3 new tests (gap analysis edge cases) |
| E3-B | ~3 new tests (single month, multi-month, idempotent) |
| E3-C | ~1 new test (annual aggregation) |
| E3-D | ~5 new tests (match, discrepancy, RBC-only, CSV-only, gap flagging) |

**Total: ~17 new tests**

---

## Migration Impact

| Item | Migration |
|------|-----------|
| E1 | No migration (code-only) |
| E3-A | No migration (report only) |
| E3-B | Yes — `CoreMonthlyStatement` model |
| E3-C | Yes — `CoreAnnualReport` model |
| E3-D | Yes — `CoreReconciliation` model |

---

## Success Criteria

1. `VDY` resolves to `VDY.TO` for yfinance queries
2. `RBF2010` returns CAD 10 from `FixedPriceConfig`, no yfinance call
3. Gap analysis shows earliest dates and missing day counts per account
4. Monthly statements generated from transactions with correct beginning/ending values
5. Annual reports aggregated from monthly statements
6. Reconciliation identifies matches and discrepancies between CSV and RBC data
7. All 122 existing tests pass + 17 new tests pass = 139 total
8. No regressions
