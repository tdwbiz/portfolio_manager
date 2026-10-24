# Accounting System Views — Design Document

> **PARKED** (ledger/accounting layer — see docs/architecture.md §11)

**Date:** 2026-08-08  
**Status:** Design Phase (Not Implemented)  
**Scope:** Financial reporting views built on top of the existing Ledger system

---

## 1. Overview

This document designs the financial reporting views for the Portfolio Manager application. These views transform raw double-entry ledger data into the standard financial statements and reports used in investment management.

### Existing Ledger Infrastructure

| Model | Purpose | Records |
|---|---|---|
| `LedgerAccount` | Per-cash/position/income ledger account | Per (account, type, currency, symbol) |
| `LedgerEntry` | Double-entry transaction records | One per Transaction |
| `MonthlyLedgerBalance` | Month-end snapshots | One per (LedgerAccount, month) |
| `LedgerMonthClosure` | Month-end closure tracking | One per (account, month, currency) |

### Target Reports

| # | Report | Dutch Name | Purpose |
|---|---|---|---|
| 1 | **Trial Balance (Proefbalans)** | Proefbalans | Complete list of all ledger account balances at a point in time |
| 2 | **Balance Sheet (Balans)** | Balans | Assets, liabilities, and equity at a point in time |
| 3 | **Income Statement (Winst- en Verliesrekening)** | Winst- en Verliesrekening | Revenue and expenses for a period |
| 4 | **Statement of Returns (Rendementenoverzicht)** | Rendementenoverzicht | Total return: realized gains + income + unrealized gains |
| 5 | **Cash Flow Statement (Kasstromenoverzicht)** | Kasstromenoverzicht | Cash inflows and outflows by category |
| 6 | **Portfolio Holdings Summary (Portefeuilleoverzicht)** | Portefeuilleoverzicht | Current positions with cost basis, market value, and P&L |
| 7 | **Transaction Detail (Transactieoverzicht)** | Transactieoverzicht | Full transaction history with cost basis tracking |

---

## 2. Report Specifications

### 2.1 Trial Balance (Proefbalans)

**Purpose:** Verify that debits equal credits. Shows every ledger account with its current balance.

**URL:** `/ledger/<account_id>/trial-balance/`

**Parameters:** `date` (defaults to today, last day of most recent closed month)

**Data Query:**
```sql
SELECT 
    la.ledger_name,
    la.account_type,
    la.currency,
    la.symbol,
    la.quantity,                    -- Shares for POSITION, always 0 for CASH
    la.cost_basis_total,
    SUM(CASE WHEN le.entry_type = 'DEBIT' THEN le.cash_amount 
             WHEN le.entry_type = 'DEBIT' AND la.account_type = 'POSITION' THEN le.cost_basis_change 
             ELSE 0 END) AS total_debit,
    SUM(CASE WHEN le.entry_type = 'CREDIT' THEN le.cash_amount 
             WHEN le.entry_type = 'CREDIT' AND la.account_type = 'POSITION' THEN le.cost_basis_change 
             ELSE 0 END) AS total_credit
FROM core_ledger_account la
LEFT JOIN core_ledger_entry le ON le.ledger_account_id = la.id
GROUP BY la.id, la.ledger_name, la.account_type, la.currency, la.symbol, la.quantity, la.cost_basis_total
ORDER BY la.account_type, la.currency, la.symbol
```

**Display Format:**

```
Proefbalans — Account: TS_HUISGELD — As of: 2026-07-31

┌──────────────────────────────┬────────┬──────┬──────────────┬──────────────┐
│ Ledger Account               │ Type   │ Debit│ Credit       │ Balance      │
├──────────────────────────────┼────────┼──────┼──────────────┼──────────────┤
│ TS_HUISGELD — CAD — CASH    │ CASH   │ 1,234│    987,654   │  -986,420.00 │
│ TS_HUISGELD — USD — CASH    │ CASH   │   456│    234,567   │  -234,111.00 │
│ TS_HUISGELD — VDY — CAD     │ POS    │ 45,678│        0    │   45,678.00  │
│ TS_HUISGELD — VUN — CAD     │ POS    │ 23,456│        0    │   23,456.00  │
│ TS_HUISGELD — XDIV — CAD    │ POS    │ 12,345│        0    │   12,345.00  │
│ TS_HUISGELD — AAPL — USD    │ POS    │  5,678│        0    │    5,678.00  │
│ TS_HUISGELD — DIVIDEND — CAD│ INCOME │  2,345│        0    │    2,345.00  │
│ TS_HUISGELD — INTEREST — CAD│ INCOME │    123│        0    │      123.00  │
├──────────────────────────────┴────────┴──────┼──────────────┼──────────────┤
│ TOTALS                                        │ 1,325,315.00 │ 1,325,315.00 │
└──────────────────────────────────────────────┴──────────────┴──────────────┘

Debit Total = Credit Total ✓  (Balanced)
```

**Key Design Decisions:**
- CASH accounts show net cash balance as a single line (debit = cash in, credit = cash out)
- POSITION accounts show share quantity as the "balance" column
- INCOME accounts show cumulative income to date
- **Debits must equal credits** — this is the fundamental accounting equation verification

---

### 2.2 Balance Sheet (Balans)

**Purpose:** Show the financial position at a point in time — what we own (assets), what we owe (liabilities), and net worth (equity).

**URL:** `/ledger/<account_id>/balance-sheet/`

**Parameters:** `date` (defaults to today)

**Data Mapping:**

| LedgerAccount Type | Balance Sheet Line |
|---|---|
| CASH | Cash & Equivalents (Current Assets) |
| POSITION (ETF/Mutual Fund) | Investment Securities (Non-Current Assets) |
| POSITION (Stock) | Investment Securities (Non-Current Assets) |
| INCOME (DIVIDEND) | Not on balance sheet (income statement) |
| INCOME (INTEREST) | Not on balance sheet (income statement) |
| INCOME (TAX) | Not on balance sheet |

**Balance Sheet Structure:**

```
Balans — Account: TS_HUISGELD — As of: 2026-07-31
                                            CAD        USD
┌─────────────────────────────────────────────────────────────┐
│ ASSETS                                                      │
│                                                             │
│ Current Assets:                                             │
│   Cash & Equivalents          $   986,420.00   $ 234,111.00│
│   Receivables                         0.00         0.00    │
│   ─────────────────────────────────────────────────────     │
│   Total Current Assets          $   986,420.00   $ 234,111.00│
│                                                             │
│ Non-Current Assets:                                         │
│   Investment Securities (at cost):                          │
│     VDY                        $   450,000.00         0.00 │
│     VUN                        $   230,000.00         0.00 │
│     XDIV                       $   120,000.00         0.00 │
│     AAPL (USD)                          0.00   $  56,780.00│
│     RBF2010                      $    15,000.00         0.00│
│   ─────────────────────────────────────────────────────     │
│   Total Investment Securities   $   815,000.00   $  56,780.00│
│                                                             │
│   ─────────────────────────────────────────────────────     │
│   TOTAL ASSETS               $ 1,801,420.00   $ 290,891.00 │
├─────────────────────────────────────────────────────────────┤
│ EQUITY                                                      │
│                                                             │
│   Contributed Capital          $ 1,801,420.00   $ 290,891.00│
│   Retained Earnings (YTD)              0.00         0.00   │
│   ─────────────────────────────────────────────────────     │
│   TOTAL EQUITY               $ 1,801,420.00   $ 290,891.00 │
├─────────────────────────────────────────────────────────────┤
│ TOTAL LIABILITIES + EQUITY   $ 1,801,420.00   $ 290,891.00 │
└─────────────────────────────────────────────────────────────┘

Note: This is a pure investment account — no liabilities.
Equity = total contributions (deposits) − total withdrawals.
```

**Key Design Decisions:**
- **Cost basis for investments**, not market value (balance sheet is historical cost)
- **Per-currency presentation** — CAD and USD shown separately
- **Contributed Capital** computed as sum of all DEP/CONTRIB/TRANSFER-IN minus WDRAW/TFR-OUT
- **Retained Earnings** = cumulative net income (dividends + interest + realized gains − fees)
- **No liabilities** for pure investment accounts (unless margin/debt is used)

---

### 2.3 Income Statement (Winst- en Verliesrekening)

**Purpose:** Show income and expenses for a period (month or year).

**URL:** `/ledger/<account_id>/income-statement/`

**Parameters:** `period` (monthly/yearly), `year`, `month` (for monthly), `start_date`/`end_date` (custom)

**Data Mapping:**

| LedgerAccount Type | Income Statement Line |
|---|---|
| INCOME (DIVIDEND) | Dividend Income |
| INCOME (DIST) | Distribution Income |
| INCOME (INTEREST) | Interest Income |
| CASH (from SELL) | Realized Gains/Losses |
| CASH (from fees) | Management Fees / Expenses |
| INCOME (TAX) | Tax Withheld |

**Income Statement Structure:**

```
Winst- en Verliesrekening — Account: TS_HUISGELD — Period: 2026-07
                                                                    CAD
┌──────────────────────────────────────────────────────────────────────┐
│ REVENUE / INKOMSTEN                                                  │
│                                                                      │
│ Dividend Income:                                                     │
│   VDY Dividends                                   $     1,234.00   │
│   VUN Dividends                                   $       456.00   │
│   XDIV Dividends                                  $       789.00   │
│   ───────────────────────────────────────────────────────────        │
│   Total Dividend Income                         $     2,479.00   │
│                                                                      │
│ Distribution Income:                                                 │
│   RBF2010 Distributions                         $       123.00   │
│   ───────────────────────────────────────────────────────────        │
│   Total Distribution Income                     $       123.00   │
│                                                                      │
│ Interest Income:                                                     │
│   RBF2010 Interest                              $        45.00   │
│   ───────────────────────────────────────────────────────────        │
│   Total Interest Income                         $        45.00   │
│                                                                      │
│ Realized Gains (Losses):                                             │
│   VTS Sale (FIFO)                               $    -2,345.00   │
│   ───────────────────────────────────────────────────────────        │
│   Total Realized Gains (Losses)                 $    -2,345.00   │
│                                                                      │
│ ───────────────────────────────────────────────────────────          │
│ NET INCOME                                                          $     302.00   │
├──────────────────────────────────────────────────────────────────────┤
│ EXPENSES / KOSTEN                                                    │
│                                                                      │
│ Management Fees:                                                     │
│   RBC Advisory Fee                          $        12.50       │
│   ───────────────────────────────────────────────────────────        │
│   Total Expenses                              $        12.50       │
│                                                                      │
│ ───────────────────────────────────────────────────────────          │
│ NET INCOME AFTER EXPENSES                                           $     289.50   │
└──────────────────────────────────────────────────────────────────────┘
```

**Key Design Decisions:**
- **Realized gains/losses** are included in income (they're gains/losses from SELL transactions)
- **FIFO cost basis** for computing realized gains: oldest shares sold first
- **Per-security breakdown** of dividend/distribution income
- **Annual totals** also available for tax reporting

---

### 2.4 Statement of Returns (Rendementenoverzicht)

**Purpose:** Total return for a period — the most important report for investment performance. Shows realized gains, income, unrealized gains, and total return percentage.

**URL:** `/ledger/<account_id>/statement-of-returns/`

**Parameters:** `period` (monthly/yearly/lifetime), `year`, `month`

**Structure:**

```
Rendementenoverzicht — Account: TS_HUISGELD — Period: 2026-07 (Monthly)

┌──────────────────────────────────────────────────────────────────────────┐
│ PERFORMANCE OVERVIEW                                                     │
│                                                                          │
│ Opening Market Value (July 1)                                   $1,750,000│
│ Closing Market Value (July 31)                                  $1,785,000│
│ ─────────────────────────────────────────────────────────────────────    │
│ Total Change in Value                                            $   35,000│
│                                                                          │
│ ─────────────────────────────────────────────────────────────────────    │
│ RETURN BREAKDOWN:                                                          │
│                                                                          │
│   1. Realized Gains/Losses                                  $   -2,345│
│   2. Dividend Income                                        $    2,479│
│   3. Distribution Income                                    $      123│
│   4. Interest Income                                        $       45│
│   5. Net Contributions (Deposits − Withdrawals)            $   10,000│
│   ─────────────────────────────────────────────────────────────────────    │
│   Subtotal (Return Components)                             $   10,302│
│                                                                          │
│   6. Unrealized Gains/Losses                                 $   24,698│
│                                                                          │
│ ─────────────────────────────────────────────────────────────────────    │
│ TOTAL RETURN (Realized + Income + Unrealized)                    $   34,997│
│ TOTAL RETURN PERCENTAGE                                         **    2.00%**│
│                                                                          │
│ Annualized Return (Monthly × 12)                                    24.00%│
├──────────────────────────────────────────────────────────────────────────┤
│ BY SECURITY                                                              │
│                                                                          │
│ ┌────────┬────────────┬────────────┬───────────┬──────────┬─────────┐  │
│ │Symbol  │ Open MV    │ Close MV │ Realized  │ Dividend │ Total%  │  │
│ ├────────┼────────────┼────────────┼───────────┼──────────┼─────────┤  │
│ │VDY     │$500,000    │$520,000    │$  5,000   │$ 1,234   │  4.25%  │  │
│ │VUN     │$250,000    │$255,000    │$  2,000   │$   456   │  2.18%  │  │
│ │XDIV    │$130,000    │$135,000    │$(1,500)   │$   789   │  1.99%  │  │
│ │AAPL    │ $60,000    │ $65,000    │$(5,845)   │$     0   │  8.33%  │  │
│ │RBF2010 │ $15,000    │ $15,500    │$    0     │$   168   │  3.80%  │  │
│ └────────┴────────────┴────────────┴───────────┴──────────┴─────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

**Key Design Decisions:**
- **Unrealized gains** computed as: (closing MV − opening MV) − (realized gains + income + net contributions)
- **Return %** = total return / opening market value
- **Annualized** = (1 + monthly_return)^12 − 1
- **Per-security breakdown** for granular performance analysis
- **Lifetime mode** available for full account history

---

### 2.5 Cash Flow Statement (Kasstromenoverzicht)

**Purpose:** Track cash inflows and outflows by category. Important for understanding deposit/withdrawal patterns.

**URL:** `/ledger/<account_id>/cash-flow/`

**Parameters:** `period` (monthly/yearly), `year`, `month`

**Structure:**

```
Kasstromenoverzicht — Account: TS_HUISGELD — Period: 2026-07

┌──────────────────────────────────────────────────────────────────────┐
│ CASH FLOW FROM OPERATIONS                                            │
│                                                                      │
│ Dividend Income                                     $     2,479    │
│ Distribution Income                                 $       123    │
│ Interest Income                                     $        45    │
│ Realized Gains                                      $    -2,345    │
│ ───────────────────────────────────────────────────────────          │
│ Net Cash from Operations                                        $     302    │
│                                                                      │
│ CASH FLOW FROM FINANCING                                             │
│                                                                      │
│ Deposits / Contributions                            $    15,000    │
│ Withdrawals                                         $   -5,000     │
│ ───────────────────────────────────────────────────────────          │
│ Net Cash from Financing                                         $    10,000    │
│                                                                      │
│ ───────────────────────────────────────────────────────────          │
│ NET CHANGE IN CASH                                            $    10,302    │
│ Opening Cash Balance                                      $   976,118    │
│ Closing Cash Balance                                        $ 986,420    │
└──────────────────────────────────────────────────────────────────────┘
```

**Key Design Decisions:**
- **Operations** = investment income (dividends, interest, realized gains/losses)
- **Financing** = deposits, withdrawals, transfers
- **Investing activities** are implicit (buying/selling securities affects cash but is netted against realized gains)
- Matches standard cash flow statement structure but simplified for investment accounts

---

### 2.6 Portfolio Holdings Summary (Portefeuilleoverzicht)

**Purpose:** Current snapshot of all positions with cost basis, market value, and unrealized P&L.

**URL:** `/ledger/<account_id>/holdings/`

**Parameters:** `date` (defaults to latest available)

**Structure:**

```
Portefeuilleoverzicht — Account: TS_HUISGELD — As of: 2026-07-31

┌────────────────────────────────────────────────────────────────────────────────────────┐
│ CAD HOLDINGS                                                                            │
│                                                                                         │
│ ┌──────┬────────────────┬──────────┬──────────────┬──────────────┬──────────┬────────┐
│ │Type  │Symbol          │Shares    │Avg Cost      │Total Cost    │Mkt Price │Mkt Val │
│ ├──────┼────────────────┼──────────┼──────────────┼──────────────┼──────────┼────────┤
│ │ETF   │VDY             │2,500.00  │$180.50      │$451,250.00   │$208.00   │$520,000│
│ │ETF   │VUN             │1,200.00  │$192.00      │$230,400.00   │$212.50   │$255,000│
│ │ETF   │XDIV            │1,000.00  │$120.00      │$120,000.00   │$135.00   │$135,000│
│ │FUND  │RBF2010         │1,500.00  │$10.00       │$15,000.00    │$10.33    │$15,500 │
│ └──────┴────────────────┴──────────┴──────────────┴──────────────┴──────────┴────────┘
│                                                                                         │
│ Total CAD Holdings: 6,200.00 shares | Cost: $816,650.00 | MV: $925,500.00              │
│ Unrealized Gain: $108,850.00 (13.33%)                                                   │
│                                                                                         │
│ USD HOLDINGS                                                                            │
│                                                                                         │
│ ┌──────┬────────────────┬──────────┬──────────────┬──────────────┬──────────┬────────┐
│ │Type  │Symbol          │Shares    │Avg Cost      │Total Cost    │Mkt Price │Mkt Val │
│ ├──────┼────────────────┼──────────┼──────────────┼──────────────┼──────────┼────────┤
│ │STOCK │AAPL            │   50.00  |$950.00      │$47,500.00    │$1,135.60 │$56,780 │
│ └──────┴────────────────┴──────────┴──────────────┴──────────────┴──────────┴────────┘
│                                                                                         │
│ Total USD Holdings: 50.00 shares | Cost: $47,500.00 | MV: $56,780.00                   │
│ Unrealized Gain: $9,280.00 (19.54%)                                                    │
│                                                                                         │
│ ALLOCATION (CAD + USD converted at 1.36 USD/CAD)                                       │
│                                                                                         │
│   VDY  ████████████████████████████████ 40.5%                                          │
│   VUN  █████████████████ 14.0%                                                        │
│   XDIV ██████████ 7.3%                                                                │
│   AAPL ████████ 6.2%                                                                  │
│   RBF2010 █ 1.4%                                                                      │
│   Cash ████████████████████████████████████ 30.6%                                      │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

**Key Design Decisions:**
- **Separate CAD and USD holdings** tables
- **Market price sourced from:** Holdings CSV → RBC PDF → yfinance (fallback)
- **Asset allocation** shown as a visual bar chart
- **Total MV includes cash** in allocation percentage

---

### 2.7 Transaction Detail (Transactieoverzicht)

**Purpose:** Complete transaction history with cost basis, proceeds, and realized P&L per trade.

**URL:** `/ledger/<account_id>/transactions/`

**Parameters:** `year`, `symbol` (filter), `type` (filter), `start_date`/`end_date`

**Structure:**

```
Transactieoverzicht — Account: TS_HUISGELD — Year: 2026

┌────────┬────────┬──────────┬────────┬──────────┬──────────────┬──────────────┬──────────┐
│ Date   │Type    │Symbol    │Shares  │Price     │Amount CAD   │Cost Basis    │P&L       │
├────────┼────────┼──────────┼────────┼──────────┼──────────────┼──────────────┼──────────┤
│2026-01-05│BUY   │VDY       │  100.00 │$178.50  │$-17,850.00   │$17,850.00    │$0.00     │
│2026-01-15│DIV    │VDY       │   0.00  │$0.85    │$   85.00    │$0.00         │$0.00     │
│2026-03-01│SELL   │VTS       │  -50.00 │$45.00   │$ 2,250.00   │$2,150.00     │$100.00   │
│2026-06-15│DIV    │VDY       │   0.00  │$0.90    │$  90.00    │$0.00         │$0.00     │
└────────┴────────┴──────────┴────────┴──────────┴──────────────┴──────────────┴──────────┘

Year 2026 Summary:
  Total Buys:    $ 17,850.00
  Total Sells:   $  2,250.00
  Realized Gains: $    100.00
  Dividends:     $    175.00
  Net Cash Flow: $(15,325.00)
```

**Key Design Decisions:**
- **FIFO cost basis** for SELL transactions
- **P&L per trade** = proceeds − cost_basis (for SELL)
- **DIV/DIST** show $0 shares and $0 cost basis (pure income)
- **Filterable** by year, symbol, and transaction type

---

## 3. Data Model Extensions Required

### 3.1 Add INCOME subtypes to LedgerAccount

Current model has `ACCOUNT_TYPES = [('CASH',...), ('POSITION',...), ('INCOME',...)]`.

Need to add subtypes:

```python
INCOME_TYPES = [
    ('DIVIDEND', 'Dividend Income'),
    ('DISTRIBUTION', 'Distribution Income'),
    ('INTEREST', 'Interest Income'),
    ('TAX', 'Tax Withheld'),
    ('FEE', 'Management Fees'),
    ('REALIZED_GAIN', 'Realized Gains'),
]
```

### 3.2 Add RealizedGain model (optional but recommended)

```python
class RealizedGain(models.Model):
    """Tracks realized gains/losses per sell transaction."""
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE)
    ledger_account = models.ForeignKey(LedgerAccount, ...)
    symbol = models.CharField(max_length=50)
    shares_sold = models.DecimalField(max_digits=15, decimal_places=4)
    proceeds = models.DecimalField(max_digits=15, decimal_places=2)
    cost_basis = models.DecimalField(max_digits=15, decimal_places=2)
    realized_gain = models.DecimalField(max_digits=15, decimal_places=2)
    holding_period_days = models.IntegerField()  # FIFO: days from purchase to sale
    is_long_term = models.BooleanField()  # > 365 days (Canadian tax rule)
    currency = models.CharField(max_length=3)
    
    class Meta:
        db_table = "core_realized_gain"
```

### 3.3 Add TaxLot model (for future tax planning)

```python
class TaxLot(models.Model):
    """Individual purchase lot for FIFO/average cost tracking."""
    symbol = models.CharField(max_length=50)
    account = models.ForeignKey(Account, ...)
    quantity = models.DecimalField(max_digits=15, decimal_places=4)
    cost_per_unit = models.DecimalField(max_digits=10, decimal_places=4)
    total_cost = models.DecimalField(max_digits=15, decimal_places=2)
    purchase_date = models.DateField()
    closed = models.BooleanField(default=False)
    closed_date = models.DateField(null=True, blank=True)
    closed_by_transaction = models.ForeignKey(Transaction, null=True, blank=True)
    
    class Meta:
        db_table = "core_tax_lot"
        unique_together = [('account', 'symbol', 'purchase_date', 'total_cost')]
```

### 3.4 Add FX rate table (for multi-currency)

```python
class FXRate(models.Model):
    """USD/CAD exchange rates for portfolio valuation."""
    date = models.DateField()
    usd_to_cad = models.DecimalField(max_digits=10, decimal_places=6)
    source = models.CharField(max_length=50, default="Bank of Canada")
    
    class Meta:
        db_table = "core_fx_rate"
        unique_together = [('date', 'source')]
```

---

## 4. View Implementation Plan

### Phase 1: Core Views (Highest Value)

| View | Complexity | Priority | Estimated Effort |
|---|---|---|---|
| Trial Balance (Proefbalans) | Low | P0 | 2 hours |
| Holdings Summary (Portefeuilleoverzicht) | Medium | P0 | 4 hours |
| Transaction Detail (Transactieoverzicht) | Low | P0 | 2 hours |
| Income Statement (Winst- en Verliesrekening) | Medium | P1 | 4 hours |

### Phase 2: Advanced Views

| View | Complexity | Priority | Estimated Effort |
|---|---|---|---|
| Balance Sheet (Balans) | Medium | P1 | 3 hours |
| Statement of Returns (Rendementenoverzicht) | High | P1 | 6 hours |
| Cash Flow Statement (Kasstromenoverzicht) | Low | P2 | 2 hours |

### Phase 3: Tax & Planning

| View | Complexity | Priority | Estimated Effort |
|---|---|---|---|
| Tax Lot Tracker | High | P2 | 8 hours |
| Tax Loss Harvesting Analysis | Medium | P3 | 4 hours |
| Asset Allocation Chart | Medium | P3 | 3 hours |

**Total estimated effort:** ~36 hours

---

## 5. URL Structure

All views follow the pattern `/ledger/<account_id>/<report>/`:

| URL | View Name | Description |
|---|---|---|
| `/ledger/<id>/trial-balance/` | Proefbalans | Trial balance at point in time |
| `/ledger/<id>/balance-sheet/` | Balans | Balance sheet at point in time |
| `/ledger/<id>/income-statement/` | Winst- en Verliesrekening | Income statement for period |
| `/ledger/<id>/statement-of-returns/` | Rendementenoverzicht | Total return for period |
| `/ledger/<id>/cash-flow/` | Kasstromenoverzicht | Cash flows for period |
| `/ledger/<id>/holdings/` | Portefeuilleoverzicht | Current positions |
| `/ledger/<id>/transactions/` | Transactieoverzicht | Full transaction history |
| `/ledger/<id>/tax-lots/` | Belastingloten | Tax lot tracking (future) |

Navigation: All views accessible from the account detail page's "Accounting" tab, and from the existing `/ledger/<id>/<year>/<month>/` page via a "Financial Reports" dropdown.

---

## 6. Template Strategy

### Shared Template Components

All reports share common elements:

1. **Header:** Account name, currency, period/date
2. **Navigation:** Month/period selector, currency toggle
3. **Export:** PDF/CSV download button
4. **Summary Cards:** Key metrics at the top (total assets, total return %, income YTD)

### Template File Structure

```
core/templates/core/ledger/
├── base_ledger.html          # Shared layout with header/nav
├── trial_balance.html        # Proefbalans
├── balance_sheet.html        # Balans
├── income_statement.html     # Winst- en Verliesrekening
├── statement_of_returns.html # Rendementenoverzicht
├── cash_flow.html            # Kasstromenoverzicht
├── holdings.html             # Portefeuilleoverzicht
└── transactions.html         # Transactieoverzicht
```

### CSS Considerations

- Use existing `tsmoney.css` variables for theming
- Financial tables should use monospaced numbers for alignment
- Green for gains, red for losses (standard financial coloring)
- Responsive design for mobile viewing

---

## 7. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Ledger data accuracy | HIGH | HIGH | Fix DIST classification first; validate with PDF before deploying views |
| FX rate availability | MEDIUM | MEDIUM | Use Bank of Canada rates; allow manual override |
| FIFO computation performance | LOW | MEDIUM | Cache tax lot computations; paginate results |
| Multi-currency complexity | HIGH | MEDIUM | Build per-currency first, then add consolidated view |
| Tax law changes | LOW | HIGH | Design TaxLot model to be extensible for different tax rules |

---

## 8. Open Questions

1. **Cost basis method:** FIFO vs. average cost — which to implement first? (Recommend FIFO for Canadian tax compliance)
2. **Consolidated views:** Should we show a single report across ALL accounts, or per-account only? (Recommend per-account first, consolidated later)
3. **Tax reporting format:** Canadian T3/T5 slips vs. internal reporting — does the user need slip-level detail?
4. **Real-time vs. batch:** Should market values update in real-time via yfinance, or use Holdings CSV snapshots? (Recommend: Holdings CSV for reported values, yfinance for supplementary info)
5. **Historical cost basis:** For old positions pre-dating CSV imports, cost basis may be unknown. How to handle? (Recommend: Show "Unknown" and flag for manual review)

---

*This is a design document only. No implementation has been started.*
