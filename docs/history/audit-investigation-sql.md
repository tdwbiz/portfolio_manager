# RBC Data Audit - SQL Investigation Reference

This document provides SQL queries for investigating each audit check finding. Run these in your SQL Server Management Studio (SSMS) or Azure Data Studio against the **PortfolioDEV** database.

**Order of Investigation**: Queries are ordered from **2026 (latest) → 2014 (oldest)** so you can fix recent issues first.

---

## Table of Contents

1. [Statement Continuity Gaps](#1-statement-continuity-gaps)
2. [Balance Sum Reconciliation](#2-balance-sum-reconciliation)
3. [Annual vs Monthly Cross-Check](#3-annual-vs-monthly-cross-check)
4. [Currency Validation](#4-currency-validation)
5. [Exchange Rate Validation](#5-exchange-rate-validation)
6. [Account Matching & Duplicates](#6-account-matching--duplicates)
7. [Transaction Integrity](#7-transaction-integrity)
8. [Orphaned Records](#8-orphaned-records)
9. [Market Value Trends](#9-market-value-trends)
10. [Data Completeness](#10-data-completeness)

---

## 1. Statement Continuity Gaps

### 1a. Find temporal gaps > 90 days (ERROR level)

```sql
-- REVERSE CHRONOLOGICAL: Latest gaps first
WITH stmt_pairs AS (
    SELECT 
        rm1.account_id,
        a.account_id AS acc_display,
        rm1.currency,
        rm1.statement_date AS curr_date,
        rm2.statement_date AS prev_date,
        DATEDIFF(DAY, rm2.statement_date, rm1.statement_date) AS gap_days,
        rm1.file_name AS curr_file,
        rm2.file_name AS prev_file,
        rm1.end_total_market_value AS curr_end_total,
        rm1.begin_total_market_value AS curr_begin_total,
        rm2.end_total_market_value AS prev_end_total
    FROM core_rbcmonthly rm1
    JOIN core_rbcmonthly rm2 ON rm1.account_id = rm2.account_id
        AND rm1.currency = rm2.currency
        AND rm1.statement_date > rm2.statement_date
    LEFT JOIN core_account a ON rm1.account_id = a.id
    WHERE NOT EXISTS (
        SELECT 1 FROM core_rbcmonthly rm3
        WHERE rm3.account_id = rm1.account_id
          AND rm3.currency = rm1.currency
          AND rm3.statement_date > rm2.statement_date
          AND rm3.statement_date < rm1.statement_date
    )
)
SELECT 
    acc_display,
    currency,
    prev_date,
    curr_date,
    gap_days,
    prev_file,
    curr_file,
    prev_end_total,
    curr_begin_total
FROM stmt_pairs
WHERE gap_days > 90
ORDER BY curr_date DESC;
```

### 1b. Find overlapping statements (negative gap)

```sql
-- REVERSE CHRONOLOGICAL: Latest overlaps first
WITH stmt_pairs AS (
    SELECT 
        rm1.account_id,
        a.account_id AS acc_display,
        rm1.currency,
        rm1.statement_date AS curr_date,
        rm2.statement_date AS prev_date,
        DATEDIFF(DAY, rm2.statement_date, rm1.statement_date) AS gap_days,
        rm1.file_name AS curr_file,
        rm2.file_name AS prev_file
    FROM core_rbcmonthly rm1
    JOIN core_rbcmonthly rm2 ON rm1.account_id = rm2.account_id
        AND rm1.currency = rm2.currency
        AND rm1.statement_date > rm2.statement_date
    LEFT JOIN core_account a ON rm1.account_id = a.id
    WHERE NOT EXISTS (
        SELECT 1 FROM core_rbcmonthly rm3
        WHERE rm3.account_id = rm1.account_id
          AND rm3.currency = rm1.currency
          AND rm3.statement_date > rm2.statement_date
          AND rm3.statement_date < rm1.statement_date
    )
)
SELECT 
    acc_display,
    currency,
    prev_date,
    curr_date,
    gap_days,
    prev_file,
    curr_file
FROM stmt_pairs
WHERE gap_days < 0
ORDER BY curr_date DESC;
```

### 1c. Find begin/end total mismatches (within 65 days)

```sql
-- REVERSE CHRONOLOGICAL: Latest mismatches first
SELECT 
    rm1.account_id,
    a.account_id AS acc_display,
    rm1.currency,
    rm2.statement_date AS prev_date,
    rm1.statement_date AS curr_date,
    rm2.end_total_market_value AS prev_end_total,
    rm1.begin_total_market_value AS curr_begin_total,
    ABS(rm1.begin_total_market_value - rm2.end_total_market_value) AS discrepancy
FROM core_rbcmonthly rm1
JOIN core_rbcmonthly rm2 ON rm1.account_id = rm2.account_id
    AND rm1.currency = rm2.currency
    AND rm1.statement_date > rm2.statement_date
LEFT JOIN core_account a ON rm1.account_id = a.id
WHERE NOT EXISTS (
    SELECT 1 FROM core_rbcmonthly rm3
    WHERE rm3.account_id = rm1.account_id
      AND rm3.currency = rm1.currency
      AND rm3.statement_date > rm2.statement_date
      AND rm3.statement_date < rm1.statement_date
)
AND DATEDIFF(DAY, rm2.statement_date, rm1.statement_date) <= 65
AND ABS(rm1.begin_total_market_value - rm2.end_total_market_value) > 0.02
ORDER BY curr_date DESC;
```

---

## 2. Balance Sum Reconciliation

### 2a. Find statements where balance sums ≠ end_total_market_value

```sql
-- REVERSE CHRONOLOGICAL: Latest discrepancies first
SELECT 
    rm.id AS rbc_monthly_id,
    a.account_id AS acc_display,
    rm.statement_date,
    rm.currency,
    rm.end_total_market_value,
    SUM(rmb.market_value) AS sum_balances,
    ABS(SUM(rmb.market_value) - rm.end_total_market_value) AS discrepancy
FROM core_rbcmonthly rm
JOIN core_account a ON rm.account_id = a.id
LEFT JOIN core_rbcmonthlybalance rmb ON rm.id = rmb.rbc_monthly_id
GROUP BY rm.id, rm.account_id, a.account_id, rm.statement_date, rm.currency, rm.end_total_market_value
HAVING ABS(SUM(rmb.market_value) - rm.end_total_market_value) > 0.02
ORDER BY rm.statement_date DESC;
```

### 2b. Find statements where end_total=0 but balances exist (import bug)

```sql
-- REVERSE CHRONOLOGICAL: Latest bugs first
SELECT 
    rm.id AS rbc_monthly_id,
    a.account_id AS acc_display,
    rm.statement_date,
    rm.currency,
    rm.end_total_market_value,
    SUM(rmb.market_value) AS sum_balances
FROM core_rbcmonthly rm
JOIN core_account a ON rm.account_id = a.id
JOIN core_rbcmonthlybalance rmb ON rm.id = rmb.rbc_monthly_id
WHERE rm.end_total_market_value = 0.00
  AND SUM(rmb.market_value) > 0
GROUP BY rm.id, rm.account_id, a.account_id, rm.statement_date, rm.currency, rm.end_total_market_value
ORDER BY rm.statement_date DESC;
```

### 2c. View all balances for a specific statement

```sql
-- Replace @rm_id with the ID from the audit error
DECLARE @rm_id INT = 15634;  -- <-- CHANGE THIS

SELECT 
    rmb.id,
    rmb.category,
    rmb.opening_balance,
    rmb.closing_balance,
    rmb.market_value,
    rmb.percentage
FROM core_rbcmonthlybalance rmb
WHERE rmb.rbc_monthly_id = @rm_id
ORDER BY rmb.category;
```

---

## 3. Annual vs Monthly Cross-Check

### 3a. Annual ending value vs December monthly

```sql
-- REVERSE CHRONOLOGICAL: Latest discrepancies first
SELECT 
    ra.id AS annual_id,
    a.account_id AS acc_display,
    ra.report_year,
    ra.currency,
    ra.bal_ending_value AS annual_ending,
    rm.end_total_market_value AS dec_monthly_ending,
    ABS(ra.bal_ending_value - rm.end_total_market_value) AS discrepancy,
    MAX(ABS(ra.bal_ending_value) * 0.01, 10.00) AS tolerance
FROM core_rbcannual ra
JOIN core_account a ON ra.account_id = a.id
LEFT JOIN core_rbcmonthly rm ON ra.account_id = rm.account_id
    AND ra.currency = rm.currency
    AND rm.statement_date YEAR = ra.report_year
    AND rm.statement_date MONTH = 12
WHERE ABS(ra.bal_ending_value - COALESCE(rm.end_total_market_value, 0)) > MAX(ABS(ra.bal_ending_value) * 0.01, 10.00)
ORDER BY ra.report_year DESC;
```

### 3b. Annual beginning value vs January monthly

```sql
-- REVERSE CHRONOLOGICAL: Latest discrepancies first
SELECT 
    ra.id AS annual_id,
    a.account_id AS acc_display,
    ra.report_year,
    ra.currency,
    ra.bal_beginning_value AS annual_beginning,
    rm.begin_total_market_value AS jan_monthly_beginning,
    ABS(ra.bal_beginning_value - rm.begin_total_market_value) AS discrepancy,
    MAX(ABS(ra.bal_beginning_value) * 0.01, 10.00) AS tolerance
FROM core_rbcannual ra
JOIN core_account a ON ra.account_id = a.id
LEFT JOIN core_rbcmonthly rm ON ra.account_id = rm.account_id
    AND ra.currency = rm.currency
    AND rm.statement_date YEAR = ra.report_year
    AND rm.statement_date MONTH = 1
WHERE ABS(ra.bal_beginning_value - COALESCE(rm.begin_total_market_value, 0)) > MAX(ABS(ra.bal_beginning_value) * 0.01, 10.00)
ORDER BY ra.report_year DESC;
```

---

## 4. Currency Validation

### 4a. Invalid currency in RbcMonthly

```sql
SELECT 
    rm.id,
    a.account_id AS acc_display,
    rm.statement_date,
    rm.currency,
    rm.file_name
FROM core_rbcmonthly rm
JOIN core_account a ON rm.account_id = a.id
WHERE rm.currency NOT IN ('CAD', 'USD')
ORDER BY rm.statement_date DESC;
```

### 4b. RbcMonthlyBalance currency mismatch

```sql
SELECT 
    rmb.id,
    rm.id AS parent_monthly_id,
    a.account_id AS acc_display,
    rm.statement_date,
    rm.currency AS monthly_currency,
    rmb.currency AS balance_currency,
    rmb.category
FROM core_rbcmonthlybalance rmb
JOIN core_rbcmonthly rm ON rmb.rbc_monthly_id = rm.id
JOIN core_account a ON rm.account_id = a.id
WHERE rmb.currency != rm.currency
ORDER BY rm.statement_date DESC;
```

### 4c. RbcMonthlyTransaction currency mismatch

```sql
SELECT 
    rmt.id,
    rm.id AS parent_monthly_id,
    a.account_id AS acc_display,
    rm.statement_date,
    rm.currency AS monthly_currency,
    rmt.currency AS tx_currency,
    rmt.date AS tx_date,
    rmt.description
FROM core_rbcmonthlytransaction rmt
JOIN core_rbcmonthly rm ON rmt.rbc_monthly_id = rm.id
JOIN core_account a ON rm.account_id = a.id
WHERE rmt.currency != rm.currency
ORDER BY rm.statement_date DESC;
```

---

## 5. Exchange Rate Validation

### 5a. USD statements with fx_rate=1.000000 (parser bug)

```sql
-- REVERSE CHRONOLOGICAL: Latest bugs first
SELECT 
    rm.id,
    a.account_id AS acc_display,
    rm.statement_date,
    rm.fx_rate,
    rm.file_name,
    'FIX: Update fx_rate to actual USD/CAD rate' AS action
FROM core_rbcmonthly rm
JOIN core_account a ON rm.account_id = a.id
WHERE rm.currency = 'USD'
  AND rm.fx_rate = 1.000000
ORDER BY rm.statement_date DESC;
```

### 5b. Fix USD statements with fx_rate=1.000000

```sql
-- UPDATE fx_rate for USD statements (use actual rates for each date)
-- Replace with actual historical USD/CAD rates
UPDATE core_rbcmonthly
SET fx_rate = 1.3650  -- <-- CHANGE TO ACTUAL RATE FOR THIS DATE
WHERE currency = 'USD'
  AND fx_rate = 1.000000
  AND statement_date = '2026-04-30';  -- <-- CHANGE TO SPECIFIC DATE

-- Verify the fix
SELECT 
    account_id,
    statement_date,
    fx_rate
FROM core_rbcmonthly
WHERE currency = 'USD'
  AND statement_date = '2026-04-30';
```

### 5c. Exchange rates out of reasonable range

```sql
-- REVERSE CHRONOLOGICAL: Latest out-of-range first
SELECT 
    rm.id,
    a.account_id AS acc_display,
    rm.statement_date,
    rm.currency,
    rm.fx_rate,
    CASE 
        WHEN rm.fx_rate < 0.60 THEN 'TOO LOW'
        WHEN rm.fx_rate > 1.50 THEN 'TOO HIGH'
    END AS issue
FROM core_rbcmonthly rm
JOIN core_account a ON rm.account_id = a.id
WHERE rm.fx_rate < 0.60 OR rm.fx_rate > 1.50
ORDER BY rm.statement_date DESC;
```

---

## 6. Account Matching & Duplicates

### 6a. Find duplicate accounts (same normalized 8-digit ID)

```sql
-- REVERSE CHRONOLOGICAL: Latest duplicates first
SELECT 
    LEFT(account_id, CHARINDEX('-', account_id + '-') - 1) + 
    SUBSTRING(account_id, CHARINDEX('-', account_id + '-') + 1, 
              CHARINDEX('-', SUBSTRING(account_id, CHARINDEX('-', account_id + '-') + 1, 100) + '-') - 1) AS normalized_8digit,
    COUNT(*) AS duplicate_count,
    STRING_AGG(account_id, ', ') AS account_ids,
    STRING_AGG(name, ' | ') AS names
FROM (
    SELECT 
        id,
        account_id,
        name,
        broker,
        -- Extract first 8 digits
        REPLACE(REPLACE(REPLACE(account_id, '-', ''), '.', ''), ' ', '') AS digits
    FROM core_account
    WHERE broker = 'RBC'
) AS sub
GROUP BY LEFT(digits, 8)
HAVING COUNT(*) > 1
ORDER BY MAX(id) DESC;
```

### 6b. Alternative: Group by first 8 digits (simpler)

```sql
-- REVERSE CHRONOLOGICAL: Latest duplicates first
SELECT 
    TOP 100
    LEFT(REPLACE(REPLACE(REPLACE(account_id, '-', ''), '.', ''), ' ', ''), 8) AS normalized_id,
    COUNT(*) AS count,
    STRING_AGG(account_id, ', ') AS account_ids,
    STRING_AGG(name, ' | ') AS names,
    MAX(created_at) AS latest_created
FROM core_account
WHERE broker = 'RBC'
GROUP BY LEFT(REPLACE(REPLACE(REPLACE(account_id, '-', ''), '.', ''), ' ', ''), 8)
HAVING COUNT(*) > 1
ORDER BY latest_created DESC;
```

### 6c. Auto-created accounts

```sql
-- REVERSE CHRONOLOGICAL: Latest auto-created first
SELECT 
    id,
    account_id,
    name,
    broker,
    created_at
FROM core_account
WHERE broker = 'RBC'
  AND name LIKE 'Auto-created%'
ORDER BY created_at DESC;
```

---

## 7. Transaction Integrity

### 7a. Negative quantities

```sql
-- REVERSE CHRONOLOGICAL: Latest first
SELECT 
    rmt.id,
    rm.id AS parent_monthly_id,
    a.account_id AS acc_display,
    rm.statement_date AS stmt_date,
    rmt.date AS tx_date,
    rmt.description,
    rmt.symbol,
    rmt.quantity,
    rmt.price,
    rmt.amount
FROM core_rbcmonthlytransaction rmt
JOIN core_rbcmonthly rm ON rmt.rbc_monthly_id = rm.id
JOIN core_account a ON rm.account_id = a.id
WHERE rmt.quantity < 0
ORDER BY rm.statement_date DESC;
```

### 7b. Negative prices

```sql
-- REVERSE CHRONOLOGICAL: Latest first
SELECT 
    rmt.id,
    a.account_id AS acc_display,
    rm.statement_date AS stmt_date,
    rmt.date AS tx_date,
    rmt.description,
    rmt.symbol,
    rmt.quantity,
    rmt.price,
    rmt.amount
FROM core_rbcmonthlytransaction rmt
JOIN core_rbcmonthly rm ON rmt.rbc_monthly_id = rm.id
JOIN core_account a ON rm.account_id = a.id
WHERE rmt.price < 0
ORDER BY rm.statement_date DESC;
```

### 7c. Negative commissions

```sql
-- REVERSE CHRONOLOGICAL: Latest first
SELECT 
    rmt.id,
    a.account_id AS acc_display,
    rm.statement_date AS stmt_date,
    rmt.date AS tx_date,
    rmt.description,
    rmt.symbol,
    rmt.commission
FROM core_rbcmonthlytransaction rmt
JOIN core_rbcmonthly rm ON rmt.rbc_monthly_id = rm.id
JOIN core_account a ON rm.account_id = a.id
WHERE rmt.commission < 0
ORDER BY rm.statement_date DESC;
```

### 7d. Empty descriptions

```sql
-- REVERSE CHRONOLOGICAL: Latest first
SELECT 
    rmt.id,
    a.account_id AS acc_display,
    rm.statement_date AS stmt_date,
    rmt.date AS tx_date,
    rmt.symbol,
    rmt.activity_type,
    rmt.quantity,
    rmt.amount
FROM core_rbcmonthlytransaction rmt
JOIN core_rbcmonthly rm ON rmt.rbc_monthly_id = rm.id
JOIN core_account a ON rm.account_id = a.id
WHERE rmt.description = '' OR rmt.description IS NULL
ORDER BY rm.statement_date DESC;
```

---

## 8. Orphaned Records

### 8a. Orphaned RbcMonthlyBalance (no parent statement)

```sql
SELECT COUNT(*) AS orphan_balance_count
FROM core_rbcmonthlybalance
WHERE rbc_monthly_id NOT IN (SELECT id FROM core_rbcmonthly);
```

### 8b. Orphaned RbcMonthlyTransaction (no parent statement)

```sql
SELECT COUNT(*) AS orphan_tx_count
FROM core_rbcmonthlytransaction
WHERE rbc_monthly_id NOT IN (SELECT id FROM core_rbcmonthly);
```

### 8c. Orphaned RbcMonthly (no account)

```sql
SELECT COUNT(*) AS orphan_monthly_count
FROM core_rbcmonthly
WHERE account_id NOT IN (SELECT id FROM core_account);
```

---

## 9. Market Value Trends

### 9a. Extreme market value changes > 50%

```sql
-- REVERSE CHRONOLOGICAL: Latest changes first
WITH stmt_pairs AS (
    SELECT 
        rm1.account_id,
        a.account_id AS acc_display,
        rm1.currency,
        rm1.statement_date AS curr_date,
        rm2.statement_date AS prev_date,
        rm1.end_total_market_value AS curr_mv,
        rm2.end_total_market_value AS prev_mv,
        CASE 
            WHEN rm2.end_total_market_value = 0 THEN NULL
            ELSE ABS(rm1.end_total_market_value - rm2.end_total_market_value) / rm2.end_total_market_value * 100
        END AS change_pct
    FROM core_rbcmonthly rm1
    JOIN core_rbcmonthly rm2 ON rm1.account_id = rm2.account_id
        AND rm1.currency = rm2.currency
        AND rm1.statement_date > rm2.statement_date
    LEFT JOIN core_account a ON rm1.account_id = a.id
    WHERE NOT EXISTS (
        SELECT 1 FROM core_rbcmonthly rm3
        WHERE rm3.account_id = rm1.account_id
          AND rm3.currency = rm1.currency
          AND rm3.statement_date > rm2.statement_date
          AND rm3.statement_date < rm1.statement_date
    )
)
SELECT 
    acc_display,
    currency,
    prev_date,
    curr_date,
    prev_mv,
    curr_mv,
    change_pct,
    CASE 
        WHEN curr_mv < 0 THEN 'NEGATIVE VALUE'
        WHEN change_pct > 100 THEN 'EXTREME (>100%)'
        WHEN change_pct > 50 THEN 'LARGE (>50%)'
        WHEN change_pct > 30 THEN 'UNUSUAL (>30%)'
    END AS severity
FROM stmt_pairs
WHERE change_pct > 50 OR curr_mv < 0
ORDER BY curr_date DESC;
```

### 9b. Negative market values

```sql
-- REVERSE CHRONOLOGICAL: Latest first
SELECT 
    rm.id,
    a.account_id AS acc_display,
    rm.statement_date,
    rm.currency,
    rm.end_total_market_value,
    rm.file_name
FROM core_rbcmonthly rm
JOIN core_account a ON rm.account_id = a.id
WHERE rm.end_total_market_value < 0
ORDER BY rm.statement_date DESC;
```

---

## 10. Data Completeness

### 10a. Missing statement_date

```sql
SELECT 
    id,
    account_id,
    currency,
    file_name,
    created_at
FROM core_rbcmonthly
WHERE statement_date IS NULL
ORDER BY created_at DESC;
```

### 10b. Missing account

```sql
SELECT 
    id,
    account_id,
    currency,
    statement_date,
    file_name,
    created_at
FROM core_rbcmonthly
WHERE account_id IS NULL
ORDER BY created_at DESC;
```

### 10c. Missing report_year in annual

```sql
SELECT 
    id,
    account_id,
    currency,
    created_at
FROM core_rbcannual
WHERE report_year IS NULL
ORDER BY created_at DESC;
```

---

## Quick Summary Queries

### All records count by year

```sql
-- RbcMonthly by year and currency
SELECT 
    YEAR(statement_date) AS stmt_year,
    currency,
    COUNT(*) AS statement_count
FROM core_rbcmonthly
GROUP BY YEAR(statement_date), currency
ORDER BY stmt_year DESC, currency;
```

### All records count by account

```sql
-- RbcMonthly count per account
SELECT 
    a.account_id,
    a.name,
    COUNT(rm.id) AS statement_count,
    MIN(rm.statement_date) AS earliest,
    MAX(rm.statement_date) AS latest
FROM core_account a
LEFT JOIN core_rbcmonthly rm ON a.id = rm.account_id
WHERE a.broker = 'RBC'
GROUP BY a.id, a.account_id, a.name
ORDER BY statement_count DESC;
```

### Recent 2026 statements

```sql
-- All 2026 statements (latest first)
SELECT 
    a.account_id,
    a.name,
    rm.statement_date,
    rm.currency,
    rm.end_total_market_value,
    rm.begin_total_market_value,
    rm.fx_rate,
    rm.file_name
FROM core_rbcmonthly rm
JOIN core_account a ON rm.account_id = a.id
WHERE YEAR(rm.statement_date) = 2026
ORDER BY rm.statement_date DESC, a.account_id;
```

### Recent 2025 statements

```sql
-- All 2025 statements (latest first)
SELECT 
    a.account_id,
    a.name,
    rm.statement_date,
    rm.currency,
    rm.end_total_market_value,
    rm.begin_total_market_value,
    rm.fx_rate,
    rm.file_name
FROM core_rbcmonthly rm
JOIN core_account a ON rm.account_id = a.id
WHERE YEAR(rm.statement_date) = 2025
ORDER BY rm.statement_date DESC, a.account_id;
```

---

## Common Fix Patterns

### Fix exchange rate for specific statement

```sql
-- Update fx_rate for a specific USD statement
UPDATE core_rbcmonthly
SET fx_rate = 1.3650  -- Actual USD/CAD rate for this date
WHERE id = @rm_id;   -- Statement ID from audit error
```

### Delete duplicate account (keep the one with more statements)

```sql
-- Find which account has more statements
SELECT 
    account_id,
    COUNT(rm.id) AS stmt_count
FROM core_account a
LEFT JOIN core_rbcmonthly rm ON a.id = rm.account_id
WHERE a.account_id IN ('900-00003-1-8', '90000003')  -- <-- CHANGE
GROUP BY a.account_id
ORDER BY stmt_count DESC;

-- Then delete the duplicate (BE CAREFUL!)
-- DELETE FROM core_account WHERE id = @duplicate_id;
```

### Re-import missing statements

```sql
-- Find which months are missing
-- Run the continuity gap query above, then manually re-import PDFs
-- Move PDFs back to inbox/ and run: python manage.py manage_pdf_import
```

---

## Notes

- **Database**: PortfolioDEV (SQL Server 2025 dev / SQL Server 2022 prod)
- **Table Prefix**: `core_` (Django app label)
- **Date Range**: Statements span from ~2014 to 2026
- **Accounts**: 8 proper RBC accounts (90000004, 90000006, 90000007, 90000001, 90000002, 90000008, 90000005, 90000003)
- **Currencies**: CAD and USD
- **Statement Frequency**: Quarterly (RBC sends quarterly, not monthly)

---

*Generated for RBC Data Audit investigation - 2026-06-13*
