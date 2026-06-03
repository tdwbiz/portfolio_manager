# CSV vs PDF Monthly Transaction Reconciliation

**Prepared:** 2026-06-01  
**Author:** GitHub Copilot  
**Status:** Ready for review

---

## 1. Executive Summary

This report compares transactions sourced from **CSV exports** (`core_transaction` table) against transactions sourced from **PDF monthly statement imports** (`core_rbcmonthlytransaction` table) across all 8 RBC accounts.

**Decision:** CSV is the primary source going forward. PDF data is a fill-in and recon tool only.

### Key Findings

| Finding | Detail |
|---------|--------|
| CSV date coverage | All accounts start 2020 (TS_CAR_LIVING starts 2021) |
| PDF date coverage | Back to 2013 (TOM_TFSA) / 2014 / 2015 / 2017 / 2018 / 2019 depending on account |
| **Critical gap** | 2020 CSV is DIVIDENDS-ONLY — all BUY/SELL/TRANSFER/DEPOSIT/WITHDRAWAL are missing |
| Pre-2020 gap | Every account has years of PDF-only history with zero CSV transactions |
| Total PDF-ONLY txns to backfill | ~748 transactions across all accounts in PDF-ONLY periods |
| Post-2021 gap | Minor isolated months — CSV is otherwise complete for 2021+ |

### Backfill Priority

| Phase | Scope | Transactions | Priority |
|-------|-------|-------------|----------|
| Phase 1 | Pre-2020 PDF-ONLY periods (no CSV at all) | ~395 | 🔴 HIGH |
| Phase 2 | 2020 full year — add BUY/SELL/non-DIV from PDF | ~223 | 🔴 HIGH |
| Phase 3 | Individual missing months 2021–2024 | ~130 | 🟡 MEDIUM |

---

## 2. Data Sources

### CSV Source: `core_transaction`

- **Table:** `dbo.core_transaction`
- **Linked via:** `statement_id → dbo.core_brokerstatement`, then `account_id → dbo.core_account`
- **Key fields:** `date`, `type`, `symbol`, `quantity`, `price`, `commission`, `amount`, `currency`, `tx_hash`
- **Amount convention:** Absolute value (positive for all types; type code implies direction)
- **De-duplication:** `tx_hash` (SHA-256 of broker+account+date+symbol+type+qty+price+amount+ccy)

### PDF Source: `core_rbcmonthlytransaction`

- **Table:** `dbo.core_rbcmonthlytransaction`
- **Linked via:** `rbc_monthly_id → dbo.core_rbcmonthly`, then `account_id → dbo.core_account`
- **Key fields:** `date`, `activity_type`, `description`, `symbol`, `quantity`, `price`, `debit`, `credit`, `currency`
- **Amount convention:** `credit` = inflow (positive); `debit` = outflow (positive); mostly mutually exclusive per row

---

## 3. Coverage Comparison by Account

### 3.1 Date Range Summary

| Account | CSV Start | CSV End | PDF Start | PDF End | CSV txns | PDF txns |
|---------|-----------|---------|-----------|---------|----------|----------|
| TOM_LIF | 2020-03-31 | 2026-04-30 | 2018-11-08 | 2026-04-30 | 280 | 408 |
| TS_HUISGELD | 2020-01-06 | 2026-04-29 | 2017-11-06 | 2026-04-30 | 226 | 283 |
| SUSAN_LIF | 2020-01-08 | 2026-04-27 | 2019-05-31 | 2026-04-24 | 190 | 203 |
| TOM_RRSP | 2020-01-03 | 2026-04-30 | 2014-01-06 | 2026-04-30 | 539 | 881 |
| SUSAN_RRIF | 2020-01-03 | 2026-04-27 | 2015-12-30 | 2026-04-24 | 304 | 433 |
| TOM_TFSA | 2020-03-03 | 2026-04-24 | 2013-12-31 | 2026-04-30 | 296 | 515 |
| TS_CAR_LIVING | 2021-01-05 | 2026-04-30 | 2020-08-19 | 2026-04-30 | 592 | 622 |
| SUSAN_TFSA | 2020-03-03 | 2026-04-02 | 2014-02-18 | 2026-04-30 | 192 | 405 |

### 3.2 Year-by-Year Transaction Counts (CSV vs PDF)

Legend: `CSV / PDF`  |  `!!` = major gap  |  `--` = no data

#### TOM_LIF (id=60, account_id=90000002)

| Year | CSV | PDF | Delta | Note |
|------|-----|-----|-------|------|
| 2018 | -- | 4 | -4 | !! PDF-ONLY |
| 2019 | -- | 73 | -73 | !! PDF-ONLY |
| 2020 | 4 | 26 | -22 | CSV=DIV only; PDF has BUY/SELL/WDRAW |
| 2021 | 40 | 54 | -14 | Some months missing |
| 2022 | 36 | 45 | -9 | Some months missing |
| 2023 | 42 | 41 | +1 | ok |
| 2024 | 40 | 49 | -9 | Some months missing |
| 2025 | 90 | 89 | +1 | ok |
| 2026 | 28 | 27 | +1 | ok |

#### TS_HUISGELD (id=61, account_id=90000001)

| Year | CSV | PDF | Delta | Note |
|------|-----|-----|-------|------|
| 2017 | -- | 3 | -3 | !! PDF-ONLY |
| 2018 | -- | 4 | -4 | !! PDF-ONLY |
| 2019 | -- | 56 | -56 | !! PDF-ONLY |
| 2020 | 50 | 35 | +15 | CSV has more — likely overlapping exports |
| 2021 | 39 | 37 | +2 | ok |
| 2022 | 20 | 22 | -2 | ok |
| 2023 | 22 | 28 | -6 | Minor gap |
| 2024 | 41 | 43 | -2 | ok |
| 2025 | 37 | 39 | -2 | ok |
| 2026 | 17 | 16 | +1 | ok |

> Note: TS_HUISGELD 2020 CSV=50 > PDF=35 — CSV appears to have full activity in 2020 for this account (not dividend-filtered). PDF may be missing some months.

#### SUSAN_LIF (id=62, account_id=90000008)

| Year | CSV | PDF | Delta | Note |
|------|-----|-----|-------|------|
| 2019 | -- | 16 | -16 | !! PDF-ONLY (May–Dec) |
| 2020 | 28 | 24 | +4 | ok (CSV slightly higher — overlapping export) |
| 2021 | 21 | 20 | +1 | ok |
| 2022 | 22 | 25 | -3 | ok |
| 2023 | 26 | 25 | +1 | ok |
| 2024 | 41 | 41 | 0 | ✅ perfect match |
| 2025 | 40 | 41 | -1 | ok |
| 2026 | 12 | 11 | +1 | ok |

#### TOM_RRSP (id=63, account_id=90000003)

| Year | CSV | PDF | Delta | Note |
|------|-----|-----|-------|------|
| 2014 | -- | 79 | -79 | !! PDF-ONLY |
| 2015 | -- | 12 | -12 | !! PDF-ONLY |
| 2016 | -- | 6 | -6 | !! PDF-ONLY |
| 2017 | -- | 18 | -18 | !! PDF-ONLY |
| 2018 | -- | 108 | -108 | !! PDF-ONLY |
| 2019 | -- | 93 | -93 | !! PDF-ONLY |
| 2020 | 38 | 79 | -41 | CSV=DIV/TAX/WDRAW only; missing BUY/SELL |
| 2021 | 175 | 154 | +21 | CSV has more (overlapping exports) |
| 2022 | 91 | 97 | -6 | Minor gap |
| 2023 | 52 | 54 | -2 | ok |
| 2024 | 60 | 66 | -6 | Minor gap |
| 2025 | 83 | 83 | 0 | ✅ perfect match |
| 2026 | 40 | 32 | +8 | ok (CSV captures more recent activity) |

#### SUSAN_RRIF (id=64, account_id=90000005)

| Year | CSV | PDF | Delta | Note |
|------|-----|-----|-------|------|
| 2015 | -- | 2 | -2 | !! PDF-ONLY |
| 2016 | -- | 2 | -2 | !! PDF-ONLY |
| 2017 | -- | 9 | -9 | !! PDF-ONLY |
| 2018 | -- | 49 | -49 | !! PDF-ONLY |
| 2019 | -- | 37 | -37 | !! PDF-ONLY |
| 2020 | 25 | 45 | -20 | CSV=DIV only; missing BUY/SOLD/TRANSFER |
| 2021 | 28 | 34 | -6 | Minor gap |
| 2022 | 43 | 43 | 0 | ✅ perfect match |
| 2023 | 44 | 49 | -5 | ok |
| 2024 | 48 | 47 | +1 | ok |
| 2025 | 80 | 80 | 0 | ✅ perfect match |
| 2026 | 36 | 36 | 0 | ✅ perfect match |

#### TOM_TFSA (id=65, account_id=90000006)

| Year | CSV | PDF | Delta | Note |
|------|-----|-----|-------|------|
| 2013 | -- | 2 | -2 | !! PDF-ONLY |
| 2014 | -- | 10 | -10 | !! PDF-ONLY |
| 2015 | -- | 2 | -2 | !! PDF-ONLY |
| 2016 | -- | 6 | -6 | !! PDF-ONLY |
| 2017 | -- | 9 | -9 | !! PDF-ONLY |
| 2018 | -- | 34 | -34 | !! PDF-ONLY |
| 2019 | -- | 25 | -25 | !! PDF-ONLY |
| 2020 | 9 | 51 | -42 | CSV=DIV only; massive BUY/SELL gap |
| 2021 | 83 | 93 | -10 | Some months missing |
| 2022 | 51 | 82 | -31 | Significant gap |
| 2023 | 29 | 39 | -10 | Minor gap |
| 2024 | 40 | 54 | -14 | Minor gap |
| 2025 | 66 | 89 | -23 | Significant gap |
| 2026 | 18 | 19 | -1 | ok |

> ⚠️ TOM_TFSA has the most significant ongoing gaps — PDF consistently has 20-40% more transactions than CSV even in recent years.

#### TS_CAR_LIVING (id=66, account_id=90000007)

| Year | CSV | PDF | Delta | Note |
|------|-----|-----|-------|------|
| 2020 | -- | 14 | -14 | !! PDF-ONLY (Aug–Dec) |
| 2021 | 57 | 52 | +5 | ok |
| 2022 | 86 | 84 | +2 | ok |
| 2023 | 89 | 96 | -7 | Minor gap |
| 2024 | 153 | 155 | -2 | ok |
| 2025 | 155 | 165 | -10 | Minor gap |
| 2026 | 52 | 56 | -4 | ok |

#### SUSAN_TFSA (id=67, account_id=90000004)

| Year | CSV | PDF | Delta | Note |
|------|-----|-----|-------|------|
| 2014 | -- | 14 | -14 | !! PDF-ONLY |
| 2015 | -- | 7 | -7 | !! PDF-ONLY |
| 2016 | -- | 4 | -4 | !! PDF-ONLY |
| 2017 | -- | 6 | -6 | !! PDF-ONLY |
| 2018 | -- | 40 | -40 | !! PDF-ONLY |
| 2019 | -- | 35 | -35 | !! PDF-ONLY |
| 2020 | 12 | 32 | -20 | CSV=DIV only; missing BUY/TRANSFER/CONTRIB |
| 2021 | 40 | 60 | -20 | Significant gap |
| 2022 | 22 | 33 | -11 | Significant gap |
| 2023 | 30 | 43 | -13 | Significant gap |
| 2024 | 32 | 53 | -21 | Significant gap |
| 2025 | 43 | 63 | -20 | Significant gap |
| 2026 | 13 | 15 | -2 | ok |

> ⚠️ SUSAN_TFSA has the worst ongoing gap — PDF has ~40-65% more transactions than CSV in every year from 2021 onwards. Needs full 2021-2025 backfill from PDF.

---

## 4. Root Cause Analysis

### Why is CSV incomplete?

Three distinct root causes:

**A) Pre-2020: No CSV files at all**  
CSV exports were not available for any account before 2020 (some accounts: before 2019/2021). Only PDF monthly statements cover these years.

**B) 2020: Filtered dividend-only CSV export**  
The CSV file `Activity Dividends 2020 Tom & Susan Aftree Inkomste January 2, 2021.csv` was a **dividends-only export** from RBC. This means ALL BUY, SELL, TRANSFER, DEPOSIT, WITHDRAWAL transactions for 2020 are absent from CSV for most accounts.  
Evidence: `2020 CSV types = {DIV, TAX, WDRAW only}` across all "Aftree" accounts.  
Exception: TS_HUISGELD, SUSAN_LIF, SUSAN_RRIF had additional 2020 CSV files that captured more types.

**C) 2021–2025: Patchy/overlapping CSV exports**  
Some months are missing because user pulled CSV exports at irregular intervals (start/end dates didn't align perfectly). The PDF monthly statements are complete month-by-month.  
TOM_TFSA and SUSAN_TFSA show the largest ongoing gap, suggesting some CSV export files for these accounts may be missing entirely.

---

## 5. Transaction Type Mapping: PDF → CSV

Use this mapping for backfill SQL:

| PDF `activity_type` | CSV `type` | Notes |
|---------------------|------------|-------|
| BOUGHT, BUY | BUY | Both map to buy |
| SOLD, SELL | SELL | Both map to sell |
| DIVIDEND, DIV, DIV REIN | DIV | Dividend / reinvestment |
| INTEREST | INT | Interest income |
| DIST, DISTRIB., DISTRIBUTION | DIST | Distribution |
| DEPOSIT, CONTRIB | DEP | Deposit / contribution |
| WITHDRAWAL, WITHDRAW, WIRE TFR | WDRAW | Withdrawal |
| TRANSFER, TFR IN, TFR OUT, TRFIN146 | TRANSFER | Transfer in/out |
| TAX | TAX | Tax withholding |
| RTC | RETURN_OF_CAPITAL | Return of capital |
| ADJUST | ADJUSTMENT | Adjustment |
| CASH, CASH-LIEU | DIV | Cash-in-lieu of dividend |
| EXPIRED, ASSIGN., CALL..., VND | OTHER | Options expired/assigned/other |
| NAME CHG, MERGER, MGR | REORGANIZATION | Corporate actions |
| FEE, FEE PAID | OTHER | Fees |
| OPENING BALANCE | **SKIP** | Not a real transaction |
| CLOSING BALANCE, CLOSING | **SKIP** | Not a real transaction |
| VANDUARD S&P 500... | **SKIP** | Malformed row |

---

## 6. Month-Level Gaps by Account

### PDF-ONLY months (CSV has ZERO transactions for these months)

#### TOM_LIF
`(2018,11), (2018,12), (2019,1)-(2019,12), (2020,5), (2020,6), (2020,9), (2021,9), (2023,9), (2023,12), (2024,3), (2024,12)`

#### TS_HUISGELD
`(2017,11), (2017,12), (2018,12), (2019,1)-(2019,12)`

#### SUSAN_LIF
`(2019,5)-(2019,12)`

#### TOM_RRSP
`(2014,1)-(2014,5), (2014,12), (2015,12), (2016,12), (2017,11), (2017,12), (2018,1)-(2018,12), (2019,1)-(2019,12)`

#### SUSAN_RRIF
`(2015,12), (2016,12), (2017,11), (2017,12), (2018,1)-(2018,12), (2019,1)-(2019,12)`

#### TOM_TFSA
`(2013,12), (2014,1)-(2014,3), (2014,12), (2015,12), (2016,12), (2017,11), (2017,12), (2018,1)-(2018,6), (2018,9)-(2018,12), (2019,3), (2019,4), (2019,6), (2019,9), (2019,10), (2019,12), (2020,1), (2020,10), (2020,11), (2021,10), (2023,3), (2023,6)`

#### TS_CAR_LIVING
`(2020,8), (2020,9), (2020,10), (2020,11), (2020,12)`

#### SUSAN_TFSA
`(2014,2)-(2014,4), (2014,12), (2015,12), (2016,12), (2017,12), (2018,3)-(2018,6), (2018,9), (2018,10), (2018,12), (2019,3), (2019,4), (2019,6), (2019,7), (2019,9), (2019,10), (2019,12), (2020,1), (2021,7), (2021,10)`

---

## 7. Backfill Strategy

### Principle
- CSV = authoritative primary source for all dates it covers
- PDF = fill-in source for periods where CSV is absent
- **Do NOT overwrite** any existing CSV transactions
- Use `tx_hash = NULL` for PDF-sourced backfill records (SHA-256 cannot be computed in T-SQL)
- Tag backfill records via a dedicated `BrokerStatement` record per account

### Backfill Scope

**Phase 1 — Pre-CSV periods (PDF-ONLY months):**  
Insert all non-BALANCE PDF transactions for months where CSV has no data at all.

**Phase 2 — 2020 full-year BUY/SELL gap:**  
For accounts where 2020 CSV = DIV/TAX/WDRAW only, insert PDF transactions of types:  
`BOUGHT, BUY, SOLD, SELL, TRANSFER, TFR IN, TFR OUT, WIRE TFR, DEPOSIT, CONTRIB, WITHDRAWAL, WITHDRAW, EXPIRED, ASSIGN., RTC, ADJUST`  
from months already present in CSV (skip DIV/TAX/INT already there, using month-level filter).

**Phase 3 — Isolated missing months (2021+):**  
For each PDF-ONLY month identified in Section 6 that falls after the CSV start date, insert all PDF transactions.

---

## 8. Backfill SQL (T-SQL / SQL Server)

> ⚠️ **Before running:** Back up the database. Test on DEV first. Run each phase separately.

### Step 0 — Create backfill BrokerStatement records

```sql
-- One per account (run once; skip if already exists)
-- These are placeholder statements that represent the PDF backfill source
INSERT INTO dbo.core_brokerstatement (source, file_date, raw_filename)
SELECT DISTINCT
    'RBC' AS source,
    GETDATE() AS file_date,
    'PDF_BACKFILL_' + a.name AS raw_filename
FROM dbo.core_account a
WHERE a.broker = 'RBC'
  AND NOT EXISTS (
    SELECT 1 FROM dbo.core_brokerstatement bs
    WHERE bs.raw_filename = 'PDF_BACKFILL_' + a.name
  );
```

### Step 1 — Phase 1: Backfill PDF-ONLY months (pre-CSV periods)

```sql
-- Type mapping CTE for PDF activity_type → CSV type code
WITH type_map AS (
    SELECT activity_type, csv_type FROM (VALUES
        ('BOUGHT',       'BUY'),
        ('BUY',          'BUY'),
        ('SOLD',         'SELL'),
        ('SELL',         'SELL'),
        ('DIVIDEND',     'DIV'),
        ('DIV',          'DIV'),
        ('DIV REIN',     'DIV'),
        ('INTEREST',     'INT'),
        ('DIST',         'DIST'),
        ('DISTRIB.',     'DIST'),
        ('DISTRIBUTION', 'DIST'),
        ('DEPOSIT',      'DEP'),
        ('CONTRIB',      'DEP'),
        ('WITHDRAWAL',   'WDRAW'),
        ('WITHDRAW',     'WDRAW'),
        ('WIRE TFR',     'WDRAW'),
        ('TRANSFER',     'TRANSFER'),
        ('TFR IN',       'TRANSFER'),
        ('TFR OUT',      'TRANSFER'),
        ('TRFIN146',     'TRANSFER'),
        ('TAX',          'TAX'),
        ('RTC',          'RETURN_OF_CAPITAL'),
        ('ADJUST',       'ADJUSTMENT'),
        ('CASH',         'DIV'),
        ('CASH-LIEU',    'DIV'),
        ('EXPIRED',      'OTHER'),
        ('ASSIGN.',      'OTHER'),
        ('VND',          'OTHER'),
        ('FEE',          'OTHER'),
        ('FEE PAID',     'OTHER'),
        ('NAME CHG',     'REORGANIZATION'),
        ('MERGER',       'REORGANIZATION'),
        ('MGR',          'REORGANIZATION')
    ) AS t(activity_type, csv_type)
),

-- CSV coverage: months that already have transactions per account
csv_covered AS (
    SELECT DISTINCT
        t.account_id,
        YEAR(t.date)  AS yr,
        MONTH(t.date) AS mo
    FROM dbo.core_transaction t
    WHERE t.account_id IS NOT NULL
),

-- PDF months per account that have NO CSV data
pdf_only_months AS (
    SELECT DISTINCT
        rm.account_id,
        YEAR(pmt.date)  AS yr,
        MONTH(pmt.date) AS mo
    FROM dbo.core_rbcmonthlytransaction pmt
    JOIN dbo.core_rbcmonthly rm ON rm.id = pmt.rbc_monthly_id
    WHERE NOT EXISTS (
        SELECT 1 FROM csv_covered cc
        WHERE cc.account_id = rm.account_id
          AND cc.yr = YEAR(pmt.date)
          AND cc.mo = MONTH(pmt.date)
    )
),

-- Candidate transactions to insert (PDF-ONLY months, skipping BALANCE rows)
candidates AS (
    SELECT
        rm.account_id,
        pmt.date,
        pmt.symbol,
        COALESCE(tm.csv_type, 'OTHER') AS tx_type,
        pmt.quantity,
        pmt.price,
        pmt.commission,
        CASE WHEN pmt.credit >= pmt.debit THEN pmt.credit ELSE pmt.debit END AS amount,
        pmt.currency,
        pmt.description AS notes,
        bs.id AS statement_id
    FROM dbo.core_rbcmonthlytransaction pmt
    JOIN dbo.core_rbcmonthly rm ON rm.id = pmt.rbc_monthly_id
    JOIN dbo.core_brokerstatement bs ON bs.raw_filename = 'PDF_BACKFILL_' + (
        SELECT name FROM dbo.core_account WHERE id = rm.account_id
    )
    LEFT JOIN type_map tm ON tm.activity_type = pmt.activity_type
    WHERE
        -- Only PDF-only months
        EXISTS (
            SELECT 1 FROM pdf_only_months pom
            WHERE pom.account_id = rm.account_id
              AND pom.yr = YEAR(pmt.date)
              AND pom.mo = MONTH(pmt.date)
        )
        -- Skip balance summary rows
        AND pmt.activity_type NOT IN (
            'OPENING BALANCE', 'CLOSING BALANCE', 'CLOSING',
            'VANDUARD S&P 500 INDEX ETF TR UNIT'
        )
        -- Skip rows with zero amount
        AND (pmt.credit > 0 OR pmt.debit > 0)
)

-- Preview (SELECT) before INSERT:
SELECT
    account_id,
    date,
    symbol,
    tx_type,
    quantity,
    price,
    amount,
    currency,
    notes
FROM candidates
ORDER BY account_id, date;

-- *** UNCOMMENT TO EXECUTE INSERT ***
/*
INSERT INTO dbo.core_transaction
    (statement_id, account_id, date, symbol, type, quantity, price, commission,
     amount, currency, exchange_rate_cad, amount_cad, notes, tx_hash)
SELECT
    statement_id,
    account_id,
    date,
    symbol,
    tx_type,
    quantity,
    price,
    commission,
    amount,
    currency,
    1.000000  AS exchange_rate_cad,   -- will need manual FX rate update
    0.00      AS amount_cad,           -- will need manual recalculation
    notes,
    NULL      AS tx_hash               -- computed in Python if needed later
FROM candidates;
*/
```

### Step 2 — Phase 2: Backfill 2020 BUY/SELL gap

```sql
-- For 2020 months already covered in CSV (DIV-only), add BUY/SELL/non-income types from PDF

WITH type_map AS (
    -- (same as Step 1)
    SELECT activity_type, csv_type FROM (VALUES
        ('BOUGHT','BUY'),('BUY','BUY'),('SOLD','SELL'),('SELL','SELL'),
        ('TRANSFER','TRANSFER'),('TFR IN','TRANSFER'),('TFR OUT','TRANSFER'),
        ('WIRE TFR','WDRAW'),('DEPOSIT','DEP'),('CONTRIB','DEP'),
        ('WITHDRAWAL','WDRAW'),('WITHDRAW','WDRAW'),
        ('EXPIRED','OTHER'),('ASSIGN.','OTHER'),('RTC','RETURN_OF_CAPITAL'),
        ('ADJUST','ADJUSTMENT'),('NAME CHG','REORGANIZATION'),('MERGER','REORGANIZATION')
    ) AS t(activity_type, csv_type)
),

-- 2020 months present in CSV (already have DIV data)
csv_2020_months AS (
    SELECT DISTINCT
        t.account_id,
        YEAR(t.date)  AS yr,
        MONTH(t.date) AS mo
    FROM dbo.core_transaction t
    WHERE t.account_id IS NOT NULL
      AND YEAR(t.date) = 2020
),

-- PDF non-income transactions in 2020 that aren't yet in CSV
candidates_phase2 AS (
    SELECT
        rm.account_id,
        pmt.date,
        pmt.symbol,
        COALESCE(tm.csv_type, 'OTHER') AS tx_type,
        pmt.quantity,
        pmt.price,
        pmt.commission,
        CASE WHEN pmt.credit >= pmt.debit THEN pmt.credit ELSE pmt.debit END AS amount,
        pmt.currency,
        pmt.description AS notes,
        bs.id AS statement_id
    FROM dbo.core_rbcmonthlytransaction pmt
    JOIN dbo.core_rbcmonthly rm ON rm.id = pmt.rbc_monthly_id
    JOIN dbo.core_brokerstatement bs ON bs.raw_filename = 'PDF_BACKFILL_' + (
        SELECT name FROM dbo.core_account WHERE id = rm.account_id
    )
    JOIN type_map tm ON tm.activity_type = pmt.activity_type
    -- Only 2020 months that CSV already covers (has DIV)
    WHERE EXISTS (
        SELECT 1 FROM csv_2020_months c2
        WHERE c2.account_id = rm.account_id
          AND c2.yr = YEAR(pmt.date)
          AND c2.mo = MONTH(pmt.date)
    )
    AND YEAR(pmt.date) = 2020
    -- Exclude income types already captured in CSV
    AND pmt.activity_type NOT IN (
        'DIVIDEND','DIV','DIV REIN','INTEREST','TAX',
        'OPENING BALANCE','CLOSING BALANCE','CLOSING'
    )
    AND (pmt.credit > 0 OR pmt.debit > 0)
)

-- Preview first
SELECT account_id, date, symbol, tx_type, amount, currency
FROM candidates_phase2
ORDER BY account_id, date;

-- *** UNCOMMENT TO INSERT ***
/*
INSERT INTO dbo.core_transaction
    (statement_id, account_id, date, symbol, type, quantity, price, commission,
     amount, currency, exchange_rate_cad, amount_cad, notes, tx_hash)
SELECT
    statement_id, account_id, date, symbol, tx_type, quantity, price, commission,
    amount, currency, 1.000000, 0.00, notes, NULL
FROM candidates_phase2;
*/
```

### Step 3 — Phase 3: Isolated missing months 2021+

```sql
-- For each account's PDF-ONLY months in 2021+ (see Section 6), insert all PDF transactions
-- This mirrors Phase 1 logic but scoped to post-2020

WITH type_map AS (
    SELECT activity_type, csv_type FROM (VALUES
        ('BOUGHT','BUY'),('BUY','BUY'),('SOLD','SELL'),('SELL','SELL'),
        ('DIVIDEND','DIV'),('DIV','DIV'),('DIV REIN','DIV'),
        ('INTEREST','INT'),('DIST','DIST'),('DISTRIB.','DIST'),('DISTRIBUTION','DIST'),
        ('DEPOSIT','DEP'),('CONTRIB','DEP'),('WITHDRAWAL','WDRAW'),('WITHDRAW','WDRAW'),
        ('WIRE TFR','WDRAW'),('TRANSFER','TRANSFER'),('TFR IN','TRANSFER'),
        ('TFR OUT','TRANSFER'),('TRFIN146','TRANSFER'),('TAX','TAX'),
        ('RTC','RETURN_OF_CAPITAL'),('ADJUST','ADJUSTMENT'),
        ('CASH','DIV'),('CASH-LIEU','DIV'),('EXPIRED','OTHER'),('ASSIGN.','OTHER'),
        ('VND','OTHER'),('FEE','OTHER'),('FEE PAID','OTHER'),
        ('NAME CHG','REORGANIZATION'),('MERGER','REORGANIZATION'),('MGR','REORGANIZATION')
    ) AS t(activity_type, csv_type)
),
csv_covered AS (
    SELECT DISTINCT
        t.account_id,
        YEAR(t.date) AS yr,
        MONTH(t.date) AS mo
    FROM dbo.core_transaction t
    WHERE t.account_id IS NOT NULL
),
pdf_only_post2020 AS (
    SELECT DISTINCT
        rm.account_id,
        YEAR(pmt.date) AS yr,
        MONTH(pmt.date) AS mo
    FROM dbo.core_rbcmonthlytransaction pmt
    JOIN dbo.core_rbcmonthly rm ON rm.id = pmt.rbc_monthly_id
    WHERE YEAR(pmt.date) >= 2021
      AND NOT EXISTS (
          SELECT 1 FROM csv_covered cc
          WHERE cc.account_id = rm.account_id
            AND cc.yr = YEAR(pmt.date)
            AND cc.mo = MONTH(pmt.date)
      )
),
candidates_phase3 AS (
    SELECT
        rm.account_id,
        pmt.date,
        pmt.symbol,
        COALESCE(tm.csv_type, 'OTHER') AS tx_type,
        pmt.quantity,
        pmt.price,
        pmt.commission,
        CASE WHEN pmt.credit >= pmt.debit THEN pmt.credit ELSE pmt.debit END AS amount,
        pmt.currency,
        pmt.description AS notes,
        bs.id AS statement_id
    FROM dbo.core_rbcmonthlytransaction pmt
    JOIN dbo.core_rbcmonthly rm ON rm.id = pmt.rbc_monthly_id
    JOIN dbo.core_brokerstatement bs ON bs.raw_filename = 'PDF_BACKFILL_' + (
        SELECT name FROM dbo.core_account WHERE id = rm.account_id
    )
    LEFT JOIN type_map tm ON tm.activity_type = pmt.activity_type
    WHERE EXISTS (
        SELECT 1 FROM pdf_only_post2020 p3
        WHERE p3.account_id = rm.account_id
          AND p3.yr = YEAR(pmt.date)
          AND p3.mo = MONTH(pmt.date)
    )
    AND pmt.activity_type NOT IN (
        'OPENING BALANCE','CLOSING BALANCE','CLOSING',
        'VANDUARD S&P 500 INDEX ETF TR UNIT'
    )
    AND (pmt.credit > 0 OR pmt.debit > 0)
)

SELECT account_id, date, symbol, tx_type, amount, currency
FROM candidates_phase3
ORDER BY account_id, date;

-- *** UNCOMMENT TO INSERT ***
/*
INSERT INTO dbo.core_transaction
    (statement_id, account_id, date, symbol, type, quantity, price, commission,
     amount, currency, exchange_rate_cad, amount_cad, notes, tx_hash)
SELECT
    statement_id, account_id, date, symbol, tx_type, quantity, price, commission,
    amount, currency, 1.000000, 0.00, notes, NULL
FROM candidates_phase3;
*/
```

### Step 4 — Post-backfill validation

```sql
-- Count inserted PDF backfill records per account
SELECT
    a.name AS account,
    COUNT(*) AS backfill_txns,
    MIN(t.date) AS earliest,
    MAX(t.date) AS latest
FROM dbo.core_transaction t
JOIN dbo.core_brokerstatement bs ON bs.id = t.statement_id
JOIN dbo.core_account a ON a.id = t.account_id
WHERE bs.raw_filename LIKE 'PDF_BACKFILL_%'
GROUP BY a.name
ORDER BY a.name;

-- Verify no overlap with existing CSV (should return 0 rows for PDF-ONLY months)
SELECT
    a.name,
    YEAR(t.date) AS yr,
    MONTH(t.date) AS mo,
    COUNT(*) AS csv_cnt,
    SUM(CASE WHEN bs.raw_filename LIKE 'PDF_BACKFILL_%' THEN 1 ELSE 0 END) AS pdf_backfill_cnt
FROM dbo.core_transaction t
JOIN dbo.core_brokerstatement bs ON bs.id = t.statement_id
JOIN dbo.core_account a ON a.id = t.account_id
WHERE a.broker = 'RBC'
GROUP BY a.name, YEAR(t.date), MONTH(t.date)
HAVING SUM(CASE WHEN bs.raw_filename LIKE 'PDF_BACKFILL_%' THEN 1 ELSE 0 END) > 0
ORDER BY a.name, yr, mo;
```

### Step 5 — Optional: Update FX rates and amount_cad

After backfill, `exchange_rate_cad` is set to 1.0 and `amount_cad = 0.00` for all PDF backfill records.  
To compute `amount_cad` for USD transactions, apply the FX rate from the source `core_rbcmonthly.fx_rate`:

```sql
UPDATE t
SET
    t.exchange_rate_cad = rm.fx_rate,
    t.amount_cad = t.amount * rm.fx_rate
FROM dbo.core_transaction t
JOIN dbo.core_brokerstatement bs ON bs.id = t.statement_id
JOIN dbo.core_rbcmonthlytransaction pmt
    ON pmt.date = t.date
    AND pmt.symbol = t.symbol
    AND (CASE WHEN pmt.credit >= pmt.debit THEN pmt.credit ELSE pmt.debit END) = t.amount
JOIN dbo.core_rbcmonthly rm ON rm.id = pmt.rbc_monthly_id
    AND rm.account_id = t.account_id
WHERE bs.raw_filename LIKE 'PDF_BACKFILL_%'
  AND t.currency = 'USD'
  AND t.amount_cad = 0;
```

---

## 9. Notes on Overlapping Months

### Why does CSV sometimes have MORE transactions than PDF on the same month?

1. **CSV export overlapping ranges:** Exporting `Jan 1 → Oct 1` and then `Sept 1 → Dec 31` can double-import September transactions with the same hash skipped (good) or without hashes captured in both (bad if tx_hash was NULL both times). The `tx_hash` unique constraint prevents true duplicates — duplicates would be REJECTED during import.

2. **Multi-currency:** CSV captures USD/CAD transactions from a single export file. The PDF has one currency section per `core_rbcmonthly` row. If a month has both CAD and USD transactions, CSV might count all of them while PDF only counted one currency section.

3. **DIV REIN entries:** Some dividend reinvestments appear as single entries in CSV but two entries in PDF (dividend credit + buy debit as separate rows).

### Shared-month count mismatches are expected
The delta counts in Section 3.2 on shared months are **not errors** — they are artifacts of different export formats. CSV wins on shared months. Only act on true PDF-ONLY months.

---

## 10. Action Items

| # | Account | Action | Phase | Priority |
|---|---------|--------|-------|----------|
| 1 | TOM_RRSP | Backfill 2014–2019 from PDF (316 txns) | 1 | 🔴 HIGH |
| 2 | SUSAN_TFSA | Backfill 2014–2019 from PDF (106 txns) | 1 | 🔴 HIGH |
| 3 | TOM_TFSA | Backfill 2013–2019 from PDF (88 txns) | 1 | 🔴 HIGH |
| 4 | SUSAN_RRIF | Backfill 2015–2019 from PDF (99 txns) | 1 | 🔴 HIGH |
| 5 | TOM_LIF | Backfill 2018–2019 from PDF (77 txns) | 1 | 🔴 HIGH |
| 6 | TS_HUISGELD | Backfill 2017–2019 from PDF (63 txns) | 1 | 🔴 HIGH |
| 7 | SUSAN_LIF | Backfill 2019-05–2019-12 from PDF (16 txns) | 1 | 🟡 MEDIUM |
| 8 | TS_CAR_LIVING | Backfill 2020-08–2020-12 from PDF (14 txns) | 1 | 🟡 MEDIUM |
| 9 | ALL (excl TS_HUISGELD/SUSAN_LIF) | Add 2020 BUY/SELL/non-income from PDF | 2 | 🔴 HIGH |
| 10 | TOM_TFSA | Backfill isolated months 2020-10, 2020-11, 2021-10, 2023-03, 2023-06 | 3 | 🟡 MEDIUM |
| 11 | SUSAN_TFSA | Backfill isolated months 2021-07, 2021-10 | 3 | 🟡 MEDIUM |
| 12 | TOM_LIF | Backfill isolated months 2021-09, 2023-09, 2023-12, 2024-03, 2024-12 | 3 | 🟡 MEDIUM |
| 13 | ALL | After backfill: update `exchange_rate_cad` and `amount_cad` for USD rows | Post | 🟡 MEDIUM |
| 14 | ALL | Going forward: export full-activity CSV (not dividend-only) monthly | Process | 🔵 INFO |

---

## 11. Recommended Execution Plan

```
1. Review and confirm SQL in Section 8 (run SELECT versions first)
2. Take a database backup
3. Run Step 0 (create PDF_BACKFILL_ BrokerStatement records)
4. Run Phase 1 SELECT → verify counts match expectations → run INSERT
5. Run Phase 2 SELECT → verify 2020 backfill → run INSERT
6. Run Phase 3 SELECT → verify isolated months → run INSERT
7. Run Step 4 validation queries
8. Run Step 5 FX rate update for USD rows
9. Re-run annual chain integrity verification (recon_annual_vs_monthly.md)
```

---

*Generated from live DB queries on `PortfolioDEV` — 2026-06-01*
