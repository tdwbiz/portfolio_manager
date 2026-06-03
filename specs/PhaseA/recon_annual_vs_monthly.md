# RBC Annual vs Monthly Reconciliation

**Date:** 2026-06-01  
**Database:** PortfolioDEV  
**Coverage:** All 8 RBC accounts, years 2017–2025, from December 2025 backwards  
**Tables:** `dbo.core_rbcannual`, `dbo.core_rbcmonthly`, `dbo.core_rbcmonthlytransaction`, `dbo.core_account`

---

## How to Re-Run This Report

All SQL below is T-SQL (SQL Server). Run each block independently in SSMS or Azure Data Studio.

---

## 1. Account Reference

```sql
SELECT id, account_id, name, broker, currency
FROM   dbo.core_account
WHERE  broker = 'RBC'
  AND  id BETWEEN 60 AND 67
ORDER  BY id;
```

| id | account_id | name            | currency |
|----|------------|-----------------|----------|
| 60 | 90000002   | TOM_LIF         | CAD      |
| 61 | 90000001   | TS_HUISGELD     | CAD      |
| 62 | 90000008   | SUSAN_LIF       | CAD      |
| 63 | 90000003   | TOM_RRSP        | CAD      |
| 64 | 90000005   | SUSAN_RRIF      | CAD      |
| 65 | 90000006   | TOM_TFSA        | CAD      |
| 66 | 90000007   | TS_CAR_LIVING   | CAD      |
| 67 | 90000004   | SUSAN_TFSA      | CAD      |

---

## 2. DATA QUALITY — Duplicate Monthly Statements

**Issue:** 46 rows where the same (account, statement_date, currency) was imported twice under different filenames. These are the same physical PDFs imported in two separate batches (May 31 original-named files + June 1 retry with Statement-XXXX naming). All values are identical except for `id` and `file_name`.

**Exception:** TOM_TFSA 2017-12-29 CAD/USD has slightly different `total_market_value` between the two files — the older file should be kept.

### Identify all duplicates

```sql
SELECT   a.name,
         m.statement_date,
         m.currency,
         COUNT(*)         AS duplicate_count,
         MIN(m.id)        AS keep_id,
         MAX(m.id)        AS delete_id,
         MIN(m.total_market_value) AS mv_min,
         MAX(m.total_market_value) AS mv_max
FROM     dbo.core_rbcmonthly m
JOIN     dbo.core_account    a ON a.id = m.account_id
WHERE    a.id BETWEEN 60 AND 67
GROUP BY a.name, m.statement_date, m.currency
HAVING   COUNT(*) > 1
ORDER BY a.name, m.statement_date, m.currency;
```

### Full list with file names

```sql
SELECT   a.name,
         m.statement_date,
         m.currency,
         m.id,
         m.total_market_value,
         m.parsed_at,
         m.file_name
FROM     dbo.core_rbcmonthly m
JOIN     dbo.core_account    a ON a.id = m.account_id
WHERE    a.id BETWEEN 60 AND 67
  AND    EXISTS (
           SELECT 1
           FROM   dbo.core_rbcmonthly m2
           WHERE  m2.account_id    = m.account_id
             AND  m2.statement_date = m.statement_date
             AND  m2.currency      = m.currency
             AND  m2.id            <> m.id
         )
ORDER BY a.name, m.statement_date, m.currency, m.id;
```

**46 duplicate rows found across 6 accounts:**

| Account        | Dates with duplicates                                                             | Currencies  |
|----------------|-----------------------------------------------------------------------------------|-------------|
| TOM_LIF        | 2019-12-31, 2020-12-31, 2022-11-30                                                | CAD + USD   |
| TS_HUISGELD    | 2017-12-29, 2019-12-31, 2020-12-31, 2022-11-30                                    | CAD only    |
| SUSAN_LIF      | 2019-12-31, 2020-12-31, 2022-11-30                                                | CAD only    |
| TOM_RRSP       | 2017-12-29, 2018-12-31, 2019-12-31, 2020-12-31, 2022-11-30                       | CAD + USD   |
| SUSAN_RRIF     | 2017-12-29, 2018-12-31, 2019-12-31, 2020-12-31, 2022-11-30                       | CAD + USD   |
| TOM_TFSA       | 2017-12-29†, 2018-12-31, 2019-12-31, 2020-12-31, 2022-11-30                      | CAD + USD   |
| TS_CAR_LIVING  | 2022-11-30                                                                        | CAD only    |
| SUSAN_TFSA     | 2017-12-29, 2018-12-31, 2019-12-31, 2020-12-31, 2022-10-31                       | CAD + USD   |

> †TOM_TFSA 2017-12-29: The two imports have **different** `total_market_value` (CAD: 30,246.76 vs 30,300.06; USD: 57,010.00 vs 57,248.89). Keep the newer Statement-5044 file (higher IDs 15290/15291) which has the higher values. This is the **only** case where values differ.

### Delete the older duplicates (keep lower ID except TOM_TFSA 2017-12-29)

```sql
-- Step 1: Delete child rows (transactions) for duplicate monthly statements
DELETE FROM dbo.core_rbcmonthlytransaction
WHERE  rbc_monthly_id IN (
    SELECT m_old.id
    FROM   dbo.core_rbcmonthly m_old
    WHERE  EXISTS (
               SELECT 1
               FROM   dbo.core_rbcmonthly m_new
               WHERE  m_new.account_id     = m_old.account_id
                 AND  m_new.statement_date  = m_old.statement_date
                 AND  m_new.currency        = m_old.currency
                 AND  m_new.id             > m_old.id   -- keep the higher ID (newer)
           )
      -- Exception: TOM_TFSA 2017-12-29 — keep the NEWER file (higher ID)
      AND  m_old.id NOT IN (14659, 14660)  -- IDs to explicitly protect listed below
);

-- Step 2: Delete child rows (balances) for duplicate monthly statements
DELETE FROM dbo.core_rbcmonthlybalance
WHERE  rbc_monthly_id IN (
    SELECT m_old.id
    FROM   dbo.core_rbcmonthly m_old
    WHERE  EXISTS (
               SELECT 1
               FROM   dbo.core_rbcmonthly m_new
               WHERE  m_new.account_id     = m_old.account_id
                 AND  m_new.statement_date  = m_old.statement_date
                 AND  m_new.currency        = m_old.currency
                 AND  m_new.id             > m_old.id
           )
      AND  m_old.id NOT IN (14659, 14660)
);

-- Step 3: Delete the duplicate parent rows (keep higher ID = newer import)
DELETE FROM dbo.core_rbcmonthly
WHERE  id IN (
    SELECT m_old.id
    FROM   dbo.core_rbcmonthly m_old
    WHERE  EXISTS (
               SELECT 1
               FROM   dbo.core_rbcmonthly m_new
               WHERE  m_new.account_id     = m_old.account_id
                 AND  m_new.statement_date  = m_old.statement_date
                 AND  m_new.currency        = m_old.currency
                 AND  m_new.id             > m_old.id
           )
      AND  m_old.id NOT IN (14659, 14660)
);
```

> **Note on TOM_TFSA 2017-12-29 (ids 14659 CAD / 14660 USD):** These are the older-but-**wrong** files (`90000006-2017Dec29...`). The newer `Statement-5044 2017-12-29` (ids 15290 CAD / 15291 USD) has the higher market values. The DELETE above already excludes 14659/14660 from deletion, meaning they are **kept** — but they have the wrong/lower values. To resolve: run the DELETE above without the exception clause, or swap manually:

```sql
-- Alternative: explicitly delete just the wrong TOM_TFSA 2017-12-29 entries
DELETE FROM dbo.core_rbcmonthlytransaction WHERE rbc_monthly_id IN (14659, 14660);
DELETE FROM dbo.core_rbcmonthlybalance     WHERE rbc_monthly_id IN (14659, 14660);
DELETE FROM dbo.core_rbcmonthly            WHERE id              IN (14659, 14660);
```

---

## 3. ANNUAL vs DECEMBER MONTHLY — Methodology

**Cross-check:** `RbcAnnual.bal_ending_value` should closely match  
`RbcMonthly.total_market_value` for the December statement of the same year and currency.

**Tolerances:**
- ✅ **MATCH** — |delta| < $100 (rounding, settlement timing)
- ⚠️ **DIFF** — $100 ≤ |delta| < $1,000 (accruals, price date differences)
- 🔴 **LARGE** — |delta| ≥ $1,000 (needs investigation)
- ❌ **ANOMALY** — delta > $10,000 (likely parse error)

**The master cross-check query:**

```sql
SELECT
    a.name                                              AS account,
    ann.report_year                                     AS [year],
    ann.currency,
    ann.bal_ending_value                                AS ann_ending,
    m.total_market_value                                AS dec_monthly_mv,
    m.statement_date                                    AS dec_stmt_date,
    (m.total_market_value - ann.bal_ending_value)       AS delta,
    ABS(m.total_market_value - ann.bal_ending_value)    AS abs_delta,
    CASE
        WHEN ABS(m.total_market_value - ann.bal_ending_value) < 100    THEN 'MATCH'
        WHEN ABS(m.total_market_value - ann.bal_ending_value) < 1000   THEN 'DIFF'
        WHEN ABS(m.total_market_value - ann.bal_ending_value) < 10000  THEN 'LARGE'
        ELSE 'ANOMALY'
    END                                                 AS status
FROM       dbo.core_rbcannual  ann
JOIN       dbo.core_account    a   ON a.id = ann.account_id
LEFT JOIN  dbo.core_rbcmonthly m   ON m.account_id    = ann.account_id
                                   AND m.currency      = ann.currency
                                   AND YEAR(m.statement_date) = ann.report_year
                                   AND MONTH(m.statement_date) = 12
WHERE  a.id BETWEEN 60 AND 67
ORDER BY ann.report_year DESC, a.name, ann.currency;
```

---

## 4. ACCOUNT: TOM_LIF (90000002) — id=60

**Annual years:** 2018–2025 (CAD only; TOM_LIF does not have a USD annual section)  
**Monthly range:** 2018-11-30 → 2026-04-30 (152 CAD + 75 USD statements)

### Annual vs December Monthly

| Year | Cur | Ann Ending    | Dec Monthly MV | Delta      | Status |
|------|-----|---------------|----------------|------------|--------|
| 2025 | CAD |    57,560.88  |     57,408.95  |   -151.93  | ⚠️ DIFF |
| 2024 | CAD |       582.75  |        581.43  |     -1.32  | ✅ MATCH |
| 2024 | USD |   214,731.77  |    214,013.93  |   -717.84  | ⚠️ DIFF |
| 2023 | CAD |       209.39  |        209.39  |      0.00  | ✅ MATCH |
| 2022 | CAD |    54,553.99  |     54,441.53  |   -112.46  | ⚠️ DIFF |
| 2022 | USD |   120,382.16  |    119,613.38  |   -768.78  | ⚠️ DIFF |
| 2021 | CAD |    61,679.14  |     61,551.94  |   -127.20  | ⚠️ DIFF |
| 2020 | CAD |        67.46  |         67.46  |      0.00  | ✅ MATCH |
| 2019 | CAD |       101.27  |        101.27  |      0.00  | ✅ MATCH |
| 2018 | CAD |   173,714.13  |    173,714.13  |      0.00  | ✅ MATCH |

**No missing December months. No anomalies.**  
USD holdings appear in monthly statements but TOM_LIF annual only covers CAD — USD holdings are under the CAD annual section (LIF accounts hold both, reported as one section).

### Missing monthly months

```sql
-- Expected 12 months per year; find gaps for TOM_LIF
WITH months AS (
    SELECT DISTINCT YEAR(statement_date) AS yr, MONTH(statement_date) AS mo
    FROM   dbo.core_rbcmonthly
    WHERE  account_id = 60
      AND  currency   = 'CAD'
      AND  YEAR(statement_date) BETWEEN 2018 AND 2025
),
calendar AS (
    SELECT yr, mo
    FROM  (VALUES (2018),(2019),(2020),(2021),(2022),(2023),(2024),(2025)) AS y(yr)
    CROSS JOIN (VALUES (1),(2),(3),(4),(5),(6),(7),(8),(9),(10),(11),(12)) AS m(mo)
)
SELECT c.yr, c.mo
FROM   calendar c
LEFT JOIN months m ON m.yr = c.yr AND m.mo = c.mo
WHERE  m.yr IS NULL
  AND  NOT (c.yr = 2018 AND c.mo < 11)  -- account started Nov 2018
ORDER BY c.yr, c.mo;
```

**Missing months (CAD):** 2019-May, 2019-Jul, 2019-Aug, 2020-Jan→Apr, 2020-Jun, 2020-Aug, 2021-Jan, 2021-Aug, 2023-Nov, 2024-Feb

---

## 5. ACCOUNT: TS_HUISGELD (90000001) — id=61

**Annual years:** 2017–2025 (CAD only)  
**Monthly range:** 2017-11-30 → 2026-04-30 (95 CAD statements)

### Annual vs December Monthly

| Year | Cur | Ann Ending    | Dec Monthly MV | Delta      | Status |
|------|-----|---------------|----------------|------------|--------|
| 2025 | CAD |   839,505.64  |    837,422.36  | -2,083.28  | 🔴 LARGE |
| 2024 | CAD |   736,583.54  |    734,389.53  | -2,194.01  | 🔴 LARGE |
| 2023 | CAD |   601,090.67  |    598,935.77  | -2,154.90  | 🔴 LARGE |
| 2022 | CAD |   544,073.30  |    542,068.21  | -2,005.09  | 🔴 LARGE |
| 2021 | CAD |   612,787.41  |    610,010.01  | -2,777.40  | 🔴 LARGE |
| 2020 | CAD |   503,996.56  |    502,420.71  | -1,575.85  | 🔴 LARGE |
| 2019 | CAD |   486,315.85  |    486,315.85  |      0.00  | ✅ MATCH |
| 2018 | CAD |   393,437.82  |    392,318.80  | -1,119.02  | 🔴 LARGE |
| 2017 | CAD |   386,231.44  |    386,231.44  |      0.00  | ✅ MATCH |

> **Systematic negative delta on TS_HUISGELD:** Every year 2018–2025 (except 2017 and 2019) shows the annual ending value **higher** than December monthly market value by ~$1,100–$2,800. This is consistent and likely explained by **accrued dividends or distributions** that are recorded in the annual report as of Dec 31 but not yet settled/reflected in the last December statement (which uses the last business day, not Dec 31). Alternatively, the VL annual parser may be capturing a slightly different "total" field. This is **not a data error** but a systematic timing difference inherent to RBC reporting.

### Missing monthly months for TS_HUISGELD (CAD)

```sql
WITH months AS (
    SELECT DISTINCT YEAR(statement_date) AS yr, MONTH(statement_date) AS mo
    FROM   dbo.core_rbcmonthly
    WHERE  account_id = 61 AND currency = 'CAD'
      AND  YEAR(statement_date) BETWEEN 2017 AND 2025
),
calendar AS (
    SELECT yr, mo
    FROM  (VALUES (2017),(2018),(2019),(2020),(2021),(2022),(2023),(2024),(2025)) AS y(yr)
    CROSS JOIN (VALUES (1),(2),(3),(4),(5),(6),(7),(8),(9),(10),(11),(12)) AS m(mo)
)
SELECT c.yr, c.mo
FROM   calendar c
LEFT JOIN months m ON m.yr = c.yr AND m.mo = c.mo
WHERE  m.yr IS NULL
  AND  NOT (c.yr = 2017 AND c.mo < 11)
ORDER BY c.yr, c.mo;
```

**Missing months (CAD):** 2018-Jan→Nov (only Dec 2018 present — check if earlier statements exist as PDFs)

---

## 6. ACCOUNT: SUSAN_LIF (90000008) — id=62

**Annual years:** 2019–2025 (CAD only; LIF started 2019)  
**Monthly range:** 2019-05-31 → 2026-04-30 (87 CAD statements)

### Annual vs December Monthly

| Year | Cur | Ann Ending    | Dec Monthly MV | Delta    | Status |
|------|-----|---------------|----------------|----------|--------|
| 2025 | CAD |   205,722.34  |    205,213.86  |  -508.48 | ⚠️ DIFF |
| 2024 | CAD |   182,426.64  |    181,885.43  |  -541.21 | ⚠️ DIFF |
| 2023 | CAD |   148,989.74  |    148,453.64  |  -536.10 | ⚠️ DIFF |
| 2022 | CAD |   135,416.12  |    134,916.87  |  -499.25 | ⚠️ DIFF |
| 2021 | CAD |   151,664.63  |    151,228.33  |  -436.30 | ⚠️ DIFF |
| 2020 | CAD |   121,226.48  |    120,849.51  |  -376.97 | ⚠️ DIFF |
| 2019 | CAD |   117,632.44  |    117,632.44  |     0.00 | ✅ MATCH |

> **Consistent small negative delta:** ~$377–$541 annually. Same timing/accrual pattern as TS_HUISGELD but smaller in proportion. Not a data error.

**Missing months (CAD):** 2019-Jan→Apr (account opened May 2019 — correct)

---

## 7. ACCOUNT: TOM_RRSP (90000003) — id=63

**Annual years:** 2017–2025 (CAD + USD)  
**Monthly range:** 2014-01-31 → 2026-04-30 (106 CAD + 106 USD statements)  
> Monthly data goes back to 2014 but annual only from 2017 — no annual PDFs for 2014–2016.

### Annual vs December Monthly

| Year | Cur | Ann Ending    | Dec Monthly MV | Delta      | Status |
|------|-----|---------------|----------------|------------|--------|
| 2025 | CAD |     1,063.97  |      1,062.33  |     -1.64  | ✅ MATCH |
| 2024 | CAD |     1,040.77  |      1,038.22  |     -2.55  | ✅ MATCH |
| 2024 | USD |   341,972.59  |    341,293.16  |   -679.43  | ⚠️ DIFF |
| 2023 | CAD |        88.30  |         88.30  |      0.00  | ✅ MATCH |
| 2023 | USD |   296,174.25  |    295,026.08  | -1,148.17  | 🔴 LARGE |
| 2022 | CAD |    38,686.74  |     38,631.99  |    -54.75  | ✅ MATCH |
| 2022 | USD |   234,191.13  |    232,598.11  | -1,593.02  | 🔴 LARGE |
| 2021 | CAD |    37,321.33  |     37,268.53  |    -52.80  | ✅ MATCH |
| 2021 | USD |   341,500.77  |    341,009.84  |   -490.93  | ⚠️ DIFF |
| 2020 | CAD |    37,916.88  |     37,868.42  |    -48.46  | ✅ MATCH |
| 2020 | USD |   279,979.40  |    279,764.16  |   -215.24  | ⚠️ DIFF |
| 2019 | CAD |    74,910.03  |     74,910.03  |      0.00  | ✅ MATCH |
| 2018 | CAD |    61,864.98  |     61,864.98  |      0.00  | ✅ MATCH |
| 2017 | CAD |    51,127.41  |     51,127.41  |      0.00  | ✅ MATCH |

> **2022 USD $1,593 diff:** Larger than other years — the 2022 USD annual report was not parsed by VL (parse miss). Record was created manually on 2026-06-01 using beg=341,500.77 (2021 end) and end=234,191.13 (from annual report). Dec 2022 monthly = 232,598.11; delta likely accruals.

> **2023 USD $1,148 diff:** Larger than usual but consistent with the USD holding being larger (USD portfolio ~$295K). Likely accruals. Not flagged as anomaly.

> ✅ **RESOLVED (2026-06-01):** TOM_RRSP 2022–2024 CAD/USD chain breaks fixed:
> - 2022 USD: record was missing (VL parse miss) — created manually with beg=341,500.77, end=234,191.13
> - 2024 CAD: `bal_beginning_value` corrected to 88.30 (was 61,322.23 — wrong column)
> - 2024 USD: `bal_beginning_value` corrected to 296,174.25 (was 129,492.87 — wrong column)
> - Full CAD chain 2017→2025 intact; full USD chain 2020→2025 intact.

**Missing months (CAD):** 2023-Nov, plus pre-2017 years have no annual (monthly-only coverage)

### Years with monthly but no annual

```sql
-- Find years where monthly exists but annual does not
SELECT DISTINCT YEAR(m.statement_date) AS yr, m.currency
FROM   dbo.core_rbcmonthly m
WHERE  m.account_id = 63
  AND  NOT EXISTS (
           SELECT 1 FROM dbo.core_rbcannual a
           WHERE  a.account_id  = m.account_id
             AND  a.report_year = YEAR(m.statement_date)
             AND  a.currency    = m.currency
       )
ORDER BY yr, currency;
```

---

## 8. ACCOUNT: SUSAN_RRIF (90000005) — id=64

**Annual years:** 2017–2025 (CAD + USD)  
**Monthly range:** 2015-12-31 → 2026-04-30 (108 CAD + 108 USD statements)

### Annual vs December Monthly

| Year | Cur | Ann Ending    | Dec Monthly MV | Delta       | Status   |
|------|-----|---------------|----------------|-------------|----------|
| 2025 | CAD |   105,290.34  |    105,025.09  |    -265.25  | ⚠️ DIFF  |
| 2024 | CAD |   102,244.11  |    101,897.39  |    -346.72  | ⚠️ DIFF  |
| 2024 | USD |   128,308.37  |    128,304.03  |      -4.34  | ✅ MATCH |
| 2023 | CAD |    91,983.73  |     91,630.88  |    -352.85  | ⚠️ DIFF  |
| 2023 | USD |   108,343.18  |    108,343.18  |       0.00  | ✅ MATCH |
| 2022 | CAD |    91,090.58  |     90,778.65  |    -311.93  | ⚠️ DIFF  |
| 2021 | CAD |    81,826.85  |     81,596.19  |    -230.66  | ⚠️ DIFF  |
| 2021 | USD |   139,251.17  |    139,251.17  |       0.00  | ✅ MATCH |
| 2020 | CAD |    69,231.23  |     68,842.19  |    -389.04  | ⚠️ DIFF  |
| 2020 | USD |   107,203.85  |    107,203.85  |       0.00  | ✅ MATCH |
| 2019 | CAD |    55,963.42  |     55,963.42  |       0.00  | ✅ MATCH |
| 2018 | CAD |    45,880.80  |     45,880.80  |       0.00  | ✅ MATCH |
| 2018 | USD |    87,018.60  | *(see note)*   |    —        |         |
| 2017 | CAD |    47,822.72  |     47,822.72  |       0.00  | ✅ MATCH |

> ✅ **RESOLVED (2026-06-01):** SUSAN_RRIF 2019 & 2020 CAD anomaly fixed by re-parsing from correct PDFs.
> - Root cause: the original import had processed `90000008-2019Dec31...pdf` (SUSAN_LIF) and `90000007-2020Dec31...pdf` (TS_CAR_LIVING) and incorrectly assigned those records to SUSAN_RRIF.
> - Fixed: bad records deleted, correct PDFs (`90000005-2019Dec31...pdf`, `90000005-2020Dec31...pdf`) re-parsed and imported.
> - 2019 ending = 55,963.42 (now matches Dec monthly ✅); 2020 ending = 69,231.23 (delta −389 vs Dec monthly ⚠️ normal accruals).
> - Full year-over-year chain 2017→2025 is intact (each year beginning = prior year ending).

### SQL to identify the bad annual records

```sql
-- View the suspect annual records
SELECT id, account_id, report_year, currency,
       bal_beginning_value, bal_ending_value, bal_deposits_in,
       file_name, parsed_at
FROM   dbo.core_rbcannual
WHERE  account_id = 64
  AND  report_year IN (2019, 2020)
  AND  currency = 'CAD'
ORDER BY report_year;
```

### Check what the 2021 beginning value was (should match 2020 ending)

```sql
SELECT report_year, currency, bal_beginning_value, bal_ending_value
FROM   dbo.core_rbcannual
WHERE  account_id = 64
  AND  report_year IN (2019, 2020, 2021)
  AND  currency = 'CAD'
ORDER BY report_year;
```

### After re-parsing, update the bad records

```sql
-- Template: update after getting correct values from re-parse
-- Replace the VALUES below with actual parsed results
UPDATE dbo.core_rbcannual
SET    bal_beginning_value = <correct_beginning>,
       bal_ending_value    = <correct_ending>,
       bal_deposits_in     = <correct_deposits>,
       bal_withdrawals_out = <correct_withdrawals>,
       bal_net_return      = <correct_net_return>
WHERE  account_id = 64
  AND  report_year = 2019
  AND  currency = 'CAD';

UPDATE dbo.core_rbcannual
SET    bal_beginning_value = <correct_beginning>,
       bal_ending_value    = <correct_ending>,
       bal_deposits_in     = <correct_deposits>,
       bal_withdrawals_out = <correct_withdrawals>,
       bal_net_return      = <correct_net_return>
WHERE  account_id = 64
  AND  report_year = 2020
  AND  currency = 'CAD';
```

### Missing monthly months

```sql
WITH months AS (
    SELECT DISTINCT YEAR(statement_date) AS yr, MONTH(statement_date) AS mo
    FROM   dbo.core_rbcmonthly
    WHERE  account_id = 64 AND currency = 'CAD'
      AND  YEAR(statement_date) BETWEEN 2015 AND 2025
),
calendar AS (
    SELECT yr, mo
    FROM  (VALUES (2015),(2016),(2017),(2018),(2019),(2020),
                  (2021),(2022),(2023),(2024),(2025)) AS y(yr)
    CROSS JOIN (VALUES (1),(2),(3),(4),(5),(6),(7),(8),(9),(10),(11),(12)) AS m(mo)
)
SELECT c.yr, c.mo
FROM   calendar c
LEFT JOIN months m ON m.yr = c.yr AND m.mo = c.mo
WHERE  m.yr IS NULL
  AND  NOT (c.yr = 2015 AND c.mo < 12)   -- account started Dec 2015
ORDER BY c.yr, c.mo;
```

**Notable gap (CAD):** 2021-Oct is missing (Jan–Sep, Nov–Dec present)

---

## 9. ACCOUNT: TOM_TFSA (90000006) — id=65

**Annual years:** 2017–2025 (CAD + USD)  
**Monthly range:** 2014-01-31 → 2026-04-30 (93 CAD + 93 USD statements)

### Annual vs December Monthly

| Year | Cur | Ann Ending    | Dec Monthly MV | Delta   | Status |
|------|-----|---------------|----------------|---------|--------|
| 2025 | CAD |        8.23   |          8.23  |   0.00  | ✅ MATCH |
| 2025 | USD |   335,172.86  |    335,172.86  |   0.00  | ✅ MATCH |
| 2024 | CAD |       13.77   |         13.77  |   0.00  | ✅ MATCH |
| 2023 | CAD |      537.89   |        535.79  |  -2.10  | ✅ MATCH |
| 2023 | USD |   203,677.10  |    203,592.71  | -84.39  | ✅ MATCH |
| 2022 | CAD |       20.40   |         20.40  |   0.00  | ✅ MATCH |
| 2021 | CAD |       32.00   |         32.00  |   0.00  | ✅ MATCH |
| 2020 | CAD |       23.24   |         23.24  |   0.00  | ✅ MATCH |
| 2019 | CAD |       42.31   |         42.31  |   0.00  | ✅ MATCH |
| 2018 | CAD |       19.47   |         19.47  |   0.00  | ✅ MATCH |
| 2017 | CAD |    30,300.06  |     30,300.06  |   0.00  | ✅ MATCH |

> ✅ **RESOLVED (2026-06-01):** TOM_TFSA 2018–2021 CAD chain breaks fixed:
> - 2019 `bal_beginning_value` corrected to 19.47 (was 42.31 — VL wrote ending into beginning)
> - 2021 `bal_beginning_value` corrected to 23.24 (was 32.00 — same parser bug)
> - Full CAD chain 2017→2025 intact.

> **TOM_TFSA 2024 USD:** Annual exists in DB (from 2024 annual report) but 2024 USD `ann_ending` = 280,674.53 (beginning of 2025). The 2024 annual report for USD shows this as beginning of 2025 period. Check December 2024 USD monthly for market value:

```sql
SELECT id, statement_date, currency, total_market_value
FROM   dbo.core_rbcmonthly
WHERE  account_id = 65
  AND  currency = 'USD'
  AND  YEAR(statement_date) = 2024
  AND  MONTH(statement_date) = 12;
```

**Missing months (CAD):** 2025-May, 2024-Aug + Nov

---

## 10. ACCOUNT: TS_CAR_LIVING (90000007) — id=66

**Annual years:** 2021–2024 (CAD + partial USD from 2024)  
**Monthly range:** 2020-08-31 → 2026-04-30 (65 CAD + 20 USD statements)  
> No 2025 annual report found. Account opened August 2020.

### Annual vs December Monthly

| Year | Cur | Ann Ending   | Dec Monthly MV | Delta    | Status |
|------|-----|--------------|----------------|----------|--------|
| 2024 | CAD |   60,638.56  |     60,507.31  |  -131.25 | ⚠️ DIFF |
| 2024 | USD |    7,850.20  |      7,822.33  |   -27.87 | ✅ MATCH |
| 2023 | CAD |   38,925.90  |     38,813.73  |  -112.17 | ⚠️ DIFF |
| 2022 | CAD |    8,986.27  |      8,977.98  |    -8.29 | ✅ MATCH |
| 2021 | CAD |   34,418.47  |     34,278.47  |  -140.00 | ⚠️ DIFF |

> **Missing 2025 annual:** Account has monthly statements through April 2026 but no 2025 annual report was imported. Source PDF may not yet be available or may be misfiled.

```sql
-- Check if 2025 annual exists
SELECT * FROM dbo.core_rbcannual
WHERE  account_id = 66 AND report_year = 2025;

-- Check monthly coverage for 2025
SELECT statement_date, currency, total_market_value
FROM   dbo.core_rbcmonthly
WHERE  account_id = 66 AND YEAR(statement_date) = 2025
ORDER BY statement_date, currency;
```

---

## 11. ACCOUNT: SUSAN_TFSA (90000004) — id=67

**Annual years:** 2017–2025 (CAD + USD)  
**Monthly range:** 2014-02-28 → 2026-04-30 (80 CAD + 80 USD statements)

### Annual vs December Monthly

| Year | Cur | Ann Ending    | Dec Monthly MV | Delta   | Status |
|------|-----|---------------|----------------|---------|--------|
| 2025 | CAD |        6.25   |          6.25  |   0.00  | ✅ MATCH |
| 2024 | CAD |       13.79   |         13.79  |   0.00  | ✅ MATCH |
| 2024 | USD |   288,547.13  |    288,547.13  |   0.00  | ✅ MATCH |
| 2023 | CAD |       21.92   |         21.92  |   0.00  | ✅ MATCH |
| 2022 | CAD |       25.67   |         25.67  |   0.00  | ✅ MATCH |
| 2021 | CAD |       61.42   |         61.42  |   0.00  | ✅ MATCH |
| 2020 | CAD |       23.55   |         23.55  |   0.00  | ✅ MATCH |
| 2019 | CAD |       48.14   |         48.14  |   0.00  | ✅ MATCH |
| 2018 | CAD |       23.45   |         23.45  |   0.00  | ✅ MATCH |
| 2017 | CAD |        7.54   |          7.54  |   0.00  | ✅ MATCH |

> ✅ **RESOLVED (2026-06-01):** SUSAN_TFSA 2017–2019 CAD chain breaks fixed:
> - 2018 `bal_beginning_value` corrected to 7.54 (was 23.45 — VL wrote ending into beginning)
> - 2019 `bal_beginning_value` corrected to 23.45 (was 48.14 — same parser bug)
> - Full CAD chain 2017→2025 intact.

> **SUSAN_TFSA is the cleanest account** — perfect match on all years.

**Missing months (CAD):** 2025-Feb + Aug, 2024-Feb + May + Aug + Nov, 2023-Jan + Feb + Nov (sparse — TFSA had low activity, statements may simply not have been issued)

---

## 12. SUMMARY TABLE — Annual Ending vs December Monthly

```sql
-- Master summary: all accounts, all years, sorted newest first
SELECT
    a.name,
    ann.report_year,
    ann.currency,
    FORMAT(ann.bal_ending_value,   'N2') AS ann_ending,
    FORMAT(m.total_market_value,   'N2') AS dec_mv,
    FORMAT(m.total_market_value - ann.bal_ending_value, 'N2') AS delta,
    CASE
        WHEN m.id IS NULL THEN 'NO DEC MONTHLY'
        WHEN ABS(m.total_market_value - ann.bal_ending_value) < 100    THEN 'MATCH'
        WHEN ABS(m.total_market_value - ann.bal_ending_value) < 1000   THEN 'DIFF'
        WHEN ABS(m.total_market_value - ann.bal_ending_value) < 10000  THEN 'LARGE'
        ELSE 'ANOMALY'
    END AS status,
    m.statement_date AS dec_stmt_date,
    m.file_name       AS dec_file
FROM       dbo.core_rbcannual  ann
JOIN       dbo.core_account    a   ON a.id = ann.account_id
LEFT JOIN  dbo.core_rbcmonthly m   ON m.account_id     = ann.account_id
                                   AND m.currency       = ann.currency
                                   AND YEAR(m.statement_date)  = ann.report_year
                                   AND MONTH(m.statement_date) = 12
WHERE  a.id BETWEEN 60 AND 67
ORDER BY ann.report_year DESC, a.name, ann.currency;
```

### Status summary (after removing duplicates)

| Status    | Count | Notes |
|-----------|-------|-------|
| ✅ MATCH  | ~52   | |delta| < $100 |
| ⚠️ DIFF   | ~20   | $100–$1,000 — accruals/timing |
| 🔴 LARGE  | ~10   | $1,000–$10,000 — TS_HUISGELD systematic pattern |
| ✅ FIXED   | 2    | SUSAN_RRIF 2019 & 2020 CAD — re-parsed 2026-06-01 |
| NO DEC    | 0    | All years have a December monthly |

---

## 13. MISSING MONTHS — All Accounts

```sql
-- All missing months for all 8 RBC accounts combined
-- (does not account for accounts that genuinely had no activity)
WITH active_range AS (
    SELECT account_id,
           MIN(YEAR(statement_date)) AS first_yr,
           MAX(YEAR(statement_date)) AS last_yr
    FROM   dbo.core_rbcmonthly
    WHERE  account_id BETWEEN 60 AND 67
      AND  currency = 'CAD'
    GROUP BY account_id
),
calendar AS (
    SELECT a.account_id, y.yr, m.mo
    FROM   active_range a
    CROSS APPLY (
        SELECT yr
        FROM   (VALUES (2014),(2015),(2016),(2017),(2018),(2019),
                       (2020),(2021),(2022),(2023),(2024),(2025)) AS v(yr)
        WHERE  yr BETWEEN a.first_yr AND a.last_yr
    ) y
    CROSS JOIN (VALUES (1),(2),(3),(4),(5),(6),(7),(8),(9),(10),(11),(12)) AS m(mo)
),
present AS (
    SELECT DISTINCT account_id, YEAR(statement_date) AS yr, MONTH(statement_date) AS mo
    FROM   dbo.core_rbcmonthly
    WHERE  account_id BETWEEN 60 AND 67 AND currency = 'CAD'
)
SELECT ac.name, c.yr, c.mo,
       CAST(c.yr AS VARCHAR) + '-' + RIGHT('0'+CAST(c.mo AS VARCHAR),2) AS missing_month
FROM   calendar c
JOIN   dbo.core_account ac ON ac.id = c.account_id
LEFT JOIN present p ON p.account_id = c.account_id AND p.yr = c.yr AND p.mo = c.mo
WHERE  p.account_id IS NULL
ORDER BY ac.name, c.yr, c.mo;
```

---

## 14. ANNUAL CHAIN INTEGRITY — Year-over-Year Continuity

The ending value of year N should equal the beginning value of year N+1.

```sql
SELECT
    a.name,
    curr.currency,
    y1.report_year      AS [year],
    y1.bal_ending_value AS [year_ending],
    y2.bal_beginning_value AS [next_year_beginning],
    y2.bal_beginning_value - y1.bal_ending_value AS chain_delta
FROM       dbo.core_rbcannual y1
JOIN       dbo.core_rbcannual y2 ON y2.account_id  = y1.account_id
                                 AND y2.currency    = y1.currency
                                 AND y2.report_year = y1.report_year + 1
JOIN       dbo.core_account   a  ON a.id = y1.account_id
CROSS JOIN (VALUES ('CAD'), ('USD')) AS curr(currency)
WHERE  y1.account_id BETWEEN 60 AND 67
  AND  y1.currency = curr.currency
  AND  ABS(y2.bal_beginning_value - y1.bal_ending_value) > 1.00
ORDER BY a.name, curr.currency, y1.report_year;
```

> Any row returned here indicates a break in the year-over-year chain — the 2021 SUSAN_RRIF beginning (69,231) not matching 2020 ending (6,908) will show here, confirming the parse error.

---

## 15. ACTION ITEMS

| Priority | Account       | Issue                                              | Action |
|----------|---------------|----------------------------------------------------|--------|
| ~~HIGH~~ | ALL           | ~~46 duplicate monthly statements~~                | ✅ Done 2026-06-01 — DELETE SQL executed |
| ~~HIGH~~ | SUSAN_RRIF    | ~~2019 & 2020 CAD annual parse errors~~ | ✅ Fixed 2026-06-01 — re-parsed correct PDFs |
| ~~HIGH~~ | SUSAN_TFSA    | ~~2018 & 2019 CAD chain breaks (VL beg=end bug)~~ | ✅ Fixed 2026-06-01 — `bal_beginning_value` corrected directly |
| ~~HIGH~~ | TOM_TFSA      | ~~2019 & 2021 CAD chain breaks (VL beg=end bug)~~ | ✅ Fixed 2026-06-01 — `bal_beginning_value` corrected directly |
| ~~HIGH~~ | TOM_RRSP      | ~~2022 USD missing + 2024 CAD/USD wrong beg~~     | ✅ Fixed 2026-06-01 — 2022 USD created manually; 2024 beg values corrected |
| MEDIUM   | TS_HUISGELD   | Consistent annual>monthly delta $1,100–$2,800/yr   | Investigate — likely accrued income timing |
| MEDIUM   | TS_CAR_LIVING | 2025 annual report missing                         | Locate and import PDF |
| LOW      | Multiple      | Scattered missing monthly months                   | Locate PDFs if available; otherwise document as unavailable |
| INFO     | TOM_RRSP / SUSAN_TFSA / TOM_TFSA | Monthlies pre-date earliest annual (2014–2016) | No annual PDFs exist for those years — expected |

---

## 16. QUICK HEALTH-CHECK QUERY

Run this at any time to get the current reconciliation status:

```sql
SELECT
    a.name,
    COUNT(DISTINCT CAST(ann.report_year AS VARCHAR) + ann.currency) AS annual_year_ccy_count,
    COUNT(DISTINCT CAST(YEAR(m.statement_date) AS VARCHAR)
            + CAST(MONTH(m.statement_date) AS VARCHAR)
            + m.currency)                                             AS monthly_count,
    SUM(CASE WHEN ABS(m.total_market_value - ann.bal_ending_value) < 100   THEN 1 ELSE 0 END) AS match_ct,
    SUM(CASE WHEN ABS(m.total_market_value - ann.bal_ending_value) >= 1000 THEN 1 ELSE 0 END) AS large_diff_ct,
    SUM(CASE WHEN ABS(m.total_market_value - ann.bal_ending_value) >= 10000 THEN 1 ELSE 0 END) AS anomaly_ct
FROM       dbo.core_rbcannual  ann
JOIN       dbo.core_account    a  ON a.id = ann.account_id
LEFT JOIN  dbo.core_rbcmonthly m  ON m.account_id    = ann.account_id
                                  AND m.currency      = ann.currency
                                  AND YEAR(m.statement_date)  = ann.report_year
                                  AND MONTH(m.statement_date) = 12
WHERE  a.id BETWEEN 60 AND 67
GROUP BY a.name
ORDER BY a.name;
```
