# RBC Data Load Process

This document describes the complete data loading workflow for RBC (Royal Bank of Canada) brokerage statements, covering both CSV and PDF import paths.

---

## A. Loading from CSV Files

**Entry point:** `imports/auto_import.py` → `auto_import()` or `imports/parsers.py` → `parse_rbc_csv()`

### Step-by-Step

1. **File Hashing** — SHA-256 hash of the raw file bytes is computed. A `FileImportLog` record is checked; if identical content was already imported, the file is **skipped** (deduplication).

2. **Parse CSV** — `parse_rbc_csv()` reads the RBC Activity Export CSV:
   - Scans for the metadata header block at the top of the file
   - Extracts the **USD→CAD exchange rate** from a line like `Exchange Rate: 1 USD = 1.37XX CAD`
   - Finds the data header row (first column = `"Date"`)
   - Iterates data rows, extracting: `Date`, `Activity`, `Symbol`, `ISIN`, `Quantity`, `Price`, `Settlement Date`, `Account ID`, `Value`, `Currency`, `Notes`

3. **Type Normalization** — Raw activity strings (e.g., `"Buy"`, `"Dividends"`, `"Deposits & Contributions"`) are mapped to canonical codes via `_RBC_TYPE_MAP`:

   | Raw Activity | Canonical Code |
   |---|---|
   | Buy | `BUY` |
   | Sell | `SELL` |
   | Dividends | `DIV` |
   | Distribution | `DIST` |
   | Interest | `INT` |
   | Deposits & Contributions | `DEP` |
   | Withdrawals & De-registrations | `WDRAW` |
   | Transfers | `TRANSFER` |
   | Fees | `FEE` |
   | Taxes | `TAX` |
   | Foreign Tax Withholding | `WITHTAX` |
   | Others / Other | `OTHER` |

4. **Commission Calculation** (for BUY/SELL only):
   - `gross = quantity × price`
   - `commission = abs(gross - abs(amount))` — derived from the discrepancy between expected gross and actual settled amount
   - Sanity-capped at $50 (set to 0 if exceeded)

5. **Currency Conversion**:
   - If transaction currency is USD: `amount_cad = abs(amount) × usd_to_cad_rate` (rounded to 2 decimals)
   - CAD transactions: `amount_cad = amount` (no conversion)

6. **Duplicate Detection** — Checks for existing `Transaction` with matching `(source, date, symbol, type, quantity, price, account)`. Duplicates are **skipped**.

7. **Database Writes** (in a single atomic transaction):
   - **`BrokerStatement`** — one row per file (`source='RBC'`, `raw_filename`, `file_date`)
   - **`Account`** — `get_or_create` by `account_id` (e.g., `90000003`)
   - **`Transaction`** — one row per trade, with all fields including `amount_cad`, `exchange_rate_cad`, `tx_hash`
   - **`FileImportLog`** — records `filename`, `broker`, `records_imported`, `records_skipped`, `content_hash`

### Tables Loaded in Order

```
BrokerStatement → Account → Transaction → FileImportLog
```

---

## B. Loading from PDFs

There are **two** PDF import paths: monthly statements and annual reports.

### B1. Monthly Statement PDFs

**Entry points:**
- **Regex parser:** `imports/views.py` → `pdf_import_view()` → `parsers_pdf.py` → `parse_rbc_pdf()`
- **Vision-Language parser:** `imports/views.py` → `pdf_import_view()` → `parsers_monthly_vl.py` → `parse_rbc_monthly_vl()` (uses Qwen2.5-VL)

#### Step-by-Step (Regex Path)

1. **File Upload** — PDF saved to `MEDIA_ROOT/pdf_uploads/`, validated with `%PDF-` magic bytes

2. **Text Extraction** — `pdfplumber` extracts all text from the PDF

3. **Currency Section Splitting** — `_split_pdf_into_sections()` splits the text at currency boundaries:
   - RBC PDFs often contain **both CAD and USD** sections in one file
   - CAD headers: `"RRSP Statement (CDN$) 2026"`, `"Cdn. Dollar Statement"`, `"Your Canadian dollar account"`
   - USD headers: `"RRSP Statement (U.S.$) 2026"`, `"U.S. Dollar Statement"`, `"Your U.S. dollar account"`
   - Returns a list of `(currency, text)` tuples

4. **Per-Currency-Section Parsing** — For each currency section:

   | Field | Extraction Method |
   |---|---|
   | Statement date | Filename (e.g., `2025-09-30`) or text pattern (`"SEPT 30 2025"`) |
   | Account number | Regex: `"Your Account Number: XXXXX-XXXXX"` |
   | Last statement date | Regex: `"Date of Last Statement: ..."` |
   | FX rate | Regex: `"Exchange rate 1 USD = X.XXXX CAD"` |

   **Sections parsed:**

   | Section | Parser Function | Content |
   |---|---|---|
   | Asset Summary | `_parse_asset_summary()` | `AccountType $MarketValue Percentage%` |
   | Cash Balance | `_parse_cash_balance()` | Opening/closing cash by type |
   | Income Summary | `_parse_income_summary()` | Dividends, interest, distributions |
   | Contribution Summary | `_parse_contribution_summary()` | Deposits/withdrawals |
   | Asset Review | `_parse_asset_review()` | Holdings list with quantities/prices |
   | Account Activity | `_parse_account_activity()` | Individual transactions |

5. **Database Writes** (per currency section, in `pdf_import_view()`):
   - **`RbcMonthly`** — one row per currency section (`account`, `file_name`, `statement_date`, `currency`, `fx_rate`, `total_market_value`)
   - **`RbcMonthlyBalance`** — one row per asset category (Cash, Mutual Funds, Common Shares, Foreign Securities) with `opening_balance`, `closing_balance`, `market_value`, `percentage`
   - **`RbcMonthlyTransaction`** — one row per transaction from Account Activity with `date`, `activity_type`, `description`, `symbol`, `quantity`, `price`, `debit`, `credit`, `commission`

### Tables Loaded in Order

```
RbcMonthly → RbcMonthlyBalance → RbcMonthlyTransaction
```

### B2. Annual Report PDFs (VL-Based Only)

**Entry point:** `imports/views.py` → `annual_report_import_view()` → `parsers_annual_vl.py` → `parse_rbc_annual_report_vl()`

1. **File Upload** — same as monthly

2. **VL Extraction** — Qwen2.5-VL renders PDF pages as images and extracts structured JSON containing:
   - CAD and USD balance/flow data
   - Investment performance metrics (1yr, 3yr, 5yr, 10yr returns)
   - Commission/fee breakdowns
   - Compounding data

3. **Account Matching** — tries exact `account_id` match, then partial match on digits, then auto-creates the Account

4. **Database Writes** (per currency):
   - **`RbcAnnual`** — flat row with balance flows (`bal_beginning_value`, `bal_deposits_in`, `bal_withdrawals_out`, `bal_net_return`, `bal_ending_value`, `bal_amount_invested`), performance metrics, and commission data

### Tables Loaded in Order

```
Account (if created) → RbcAnnual
```

---

## Summary of All RBC Tables

| Table | Source | Content |
|---|---|---|
| `BrokerStatement` | CSV only | File metadata per import |
| `Account` | CSV or PDF | Brokerage account (RBC) |
| `Transaction` | CSV only | Individual trades/activities with CAD amounts |
| `FileImportLog` | CSV only | Import audit trail with dedup hash |
| `RbcMonthly` | PDF only | Statement header per currency section |
| `RbcMonthlyBalance` | PDF only | Asset category summaries (opening/closing/market value) |
| `RbcMonthlyTransaction` | PDF only | Individual transactions from Account Activity |
| `RbcAnnual` | PDF only | Annual report summary with balance flows and performance |

---

## C. Data Audit

After importing RBC PDFs, run the audit command to verify data integrity:

```powershell
python manage.py audit_rbc_data
```

### 10 Audit Checks

| # | Check | What It Validates |
|---|-------|-------------------|
| 1 | Statement Continuity | No temporal gaps, overlapping dates, begin/end total mismatches |
| 2 | Balance Sum Reconciliation | Sum of `RbcMonthlyBalance.market_value` matches `RbcMonthly.end_total_market_value` |
| 3 | Annual vs Monthly | Annual `bal_ending_value` matches December monthly `end_total_market_value` |
| 4 | Currency Validation | Currency fields are valid (CAD/USD) and consistent with statement type |
| 5 | Exchange Rate Validation | USD statements have fx_rate ≠ 1.000000 (parser bug detection) |
| 6 | Account Matching | No duplicate accounts, auto-created accounts flagged |
| 7 | Transaction Integrity | No negative quantities, prices, or commissions |
| 8 | Orphaned Records | No orphaned balances or transactions without parent statements |
| 9 | Market Value Trends | No extreme changes >50% between consecutive statements |
| 10 | Data Completeness | Required fields populated (dates, currencies, amounts) |

### Output Options

| Flag | Description |
|------|-------------|
| `--verbose` | Show detailed output with all fields |
| `--json` | Output audit report in JSON format |
| `--md` | Generate markdown report file |
| `--output-dir PATH` | Custom output directory (default: `processed/audit`) |
| `--fix` | Auto-fix reversible issues |

### Markdown Report

When using `--md`, reports are saved as `RBC_pdf_import_audit_YYYYMMDDHHMMSS.md` in `processed/audit/`:

```markdown
# RBC PDF Import Audit Report

## Summary
| Metric | Value |
|--------|-------|
| Checks Run | 10 |
| Errors | 15 |
| Warnings | 42 |
| Passed | 7 |

**Status**: ⚠️ **WARNINGS** — 42 warnings found

## Database Statistics
| Table | Count |
|-------|-------|
| RbcMonthly | 1,203 |
| RbcMonthlyBalance | 6,070 |

## Errors
### 5. Exchange Rate Validation
1. 90000004 USD 2026-04-30: fx_rate=1.000000 (should be ~1.20-1.50 for USD)
   - **account**: `90000004`
   - **date**: `2026-04-30`
   - **fx_rate**: `1.000000`

## Recommendations
### Immediate Actions Required
- **Exchange Rates**: USD statements with fx_rate=1.000000 need correction.
- **Balance Sums**: Reconcile balance discrepancies.
```

### Best Practices

1. **Run after every import** — Catch issues before they corrupt reports
2. **Review errors first** — Fix parser issues, then re-import
3. **Check exchange rates** — USD statements with fx_rate=1.000000 indicate parser bugs
4. **Monitor trends** — Large market value changes may be legitimate (LIRA distributions) but should be reviewed
5. **Maintain continuity** — Missing statements create gaps in reporting
| `RbcMonthlyTransaction` | PDF only | Individual transactions from Account Activity |
| `RbcAnnual` | PDF only | Annual report balances, performance, commissions |

---

## Key Distinction

**CSV import** feeds the **`Transaction`** table — normalized canonical types, CAD-converted amounts. This is the main transaction ledger used for portfolio calculations.

**PDF import** feeds the **`RbcMonthly*`** and **`RbcAnnual`** tables — statement-native structure, preserved per-currency. This data is for statement archival and verification.

These are **separate data tracks** — they serve different purposes and are not merged.
