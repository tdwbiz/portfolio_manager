-- ============================================================================
-- RECONCILIATION DETAIL REPORT: RBC ACCOUNTS (2020-Present)
-- ============================================================================
-- This query compares monthly statements from CSV transactions (core_transaction)
-- against RBC PDF statements (core_rbcmonthly) for all RBC accounts since 2020.
-- ============================================================================

-- 1. ACCOUNT SUMMARY
-- Shows which accounts have RBC data, CSV data, and coverage gaps
SELECT 
    a.account_id,
    a.name AS account_name,
    COUNT(DISTINCT rbc.statement_date) AS rbc_stmts_total,
    COUNT(DISTINCT rbc_monthly_2020.id) AS rbc_stmts_2020_plus,
    COUNT(DISTINCT csv_stmts_2020.id) AS csv_stmts_2020_plus,
    MIN(rbc.statement_date) AS rbc_earliest,
    MAX(rbc.statement_date) AS rbc_latest,
    MIN(csv_stmts_2020.statement_month) AS csv_earliest,
    MAX(csv_stmts_2020.statement_month) AS csv_latest
FROM core_account a
LEFT JOIN core_rbcmonthly rbc ON a.id = rbc.account_id
LEFT JOIN (
    SELECT id, account_id, statement_date 
    FROM core_rbcmonthly 
    WHERE statement_date >= '2020-01-01'
) rbc_monthly_2020 ON a.id = rbc_monthly_2020.account_id
LEFT JOIN (
    SELECT id, account_id, statement_month 
    FROM core_monthlystatement 
    WHERE statement_month >= '2020-01-01'
) csv_stmts_2020 ON a.id = csv_stmts_2020.account_id
WHERE a.account_id IN (
    SELECT DISTINCT account_id FROM core_rbcmonthly
)
GROUP BY a.account_id, a.name
ORDER BY a.account_id;

-- 2. MONTHLY COMPARISON: 2026 SAMPLE (TOM_LIF example)
-- Shows RBC vs CSV beginning/ending values for each month
SELECT 
    COALESCE(rbc.statement_date, csv.statement_month) AS statement_period,
    rbc.currency,
    rbc.begin_total_market_value AS rbc_begin,
    rbc.end_total_market_value AS rbc_end,
    csv.beginning_market_value AS csv_begin,
    csv.ending_market_value AS csv_end,
    CASE 
        WHEN rbc.end_total_market_value IS NOT NULL AND csv.ending_market_value IS NOT NULL
        THEN rbc.end_total_market_value - csv.ending_market_value
        ELSE NULL
    END AS discrepancy
FROM (
    SELECT DISTINCT account_id, statement_date, currency 
    FROM core_rbcmonthly 
    WHERE account_id = 60  -- TOM_LIF
    AND statement_date >= '2026-01-01'
    
    UNION
    
    SELECT DISTINCT account_id, statement_month, currency 
    FROM core_monthlystatement 
    WHERE account_id = 60  -- TOM_LIF
    AND statement_month >= '2026-01-01'
) periods
LEFT JOIN core_rbcmonthly rbc 
    ON periods.account_id = rbc.account_id 
    AND periods.statement_date = rbc.statement_date 
    AND periods.currency = rbc.currency
LEFT JOIN core_monthlystatement csv 
    ON periods.account_id = csv.account_id 
    AND periods.statement_month = csv.statement_month 
    AND periods.currency = csv.currency
ORDER BY periods.statement_date, periods.currency;

-- 3. TRANSACTION-LEVEL COMPARISON (TOM_LIF, Jan 2026 example)
-- Shows actual transactions from CSV vs RBC PDF transactions
-- Note: RBC PDF transactions are in core_rbcmonthlytransaction
SELECT 
    'CSV' AS source,
    t.type,
    t.date,
    t.symbol,
    t.quantity,
    t.amount_cad,
    t.currency
FROM core_transaction t
WHERE t.account_id = 60  -- TOM_LIF
AND t.date >= '2026-01-01'
AND t.date < '2026-02-01'
AND t.currency = 'USD'
ORDER BY t.date

UNION ALL

SELECT 
    'RBC' AS source,
    rbc_txn.type,
    rbc_txn.date,
    rbc_txn.symbol,
    rbc_txn.quantity,
    rbc_txn.amount,
    rbc.statement_date AS currency
FROM core_rbcmonthly rbc
JOIN core_rbcmonthlytransaction rbc_txn ON rbc.id = rbc_txn.rbc_monthly_id
WHERE rbc.account_id = 60  -- TOM_LIF
AND rbc.statement_date = '2026-01-31'  -- RBC uses month-end dates
AND rbc.currency = 'USD'
ORDER BY rbc_txn.date;

-- 4. ANNUAL SUMMARY: DISCREPANCIES BY ACCOUNT AND YEAR
-- Shows the total discrepancy for each account by year
SELECT 
    a.account_id,
    a.name AS account_name,
    COALESCE(YEAR(rbc.statement_date), YEAR(csv.statement_month)) AS year,
    rbc.currency,
    SUM(rbc.end_total_market_value) AS rbc_total_end,
    SUM(csv.ending_market_value) AS csv_total_end,
    SUM(rbc.end_total_market_value - csv.ending_market_value) AS total_discrepancy,
    COUNT(*) AS months_compared
FROM core_account a
LEFT JOIN core_rbcmonthly rbc 
    ON a.id = rbc.account_id 
    AND rbc.statement_date >= '2020-01-01'
LEFT JOIN core_monthlystatement csv 
    ON a.id = csv.account_id 
    AND csv.statement_month >= '2020-01-01'
    AND csv.statement_month = rbc.statement_date
    AND csv.currency = rbc.currency
WHERE a.account_id IN (
    SELECT DISTINCT account_id FROM core_rbcmonthly
)
GROUP BY a.account_id, a.name, YEAR(COALESCE(rbc.statement_date, csv.statement_month)), rbc.currency
HAVING SUM(rbc.end_total_market_value - csv.ending_market_value) IS NOT NULL
ORDER BY a.account_id, year, rbc.currency;

-- 5. DATA COMPLETENESS: MISSING MONTHS
-- Shows which months exist in RBC but not in CSV (or vice versa)
SELECT 
    a.account_id,
    a.name AS account_name,
    'Missing in CSV' AS status,
    rbc.statement_date AS missing_period,
    rbc.currency
FROM core_account a
JOIN core_rbcmonthly rbc ON a.id = rbc.account_id
LEFT JOIN core_monthlystatement csv 
    ON a.id = csv.account_id 
    AND csv.statement_month = rbc.statement_date 
    AND csv.currency = rbc.currency
WHERE rbc.statement_date >= '2020-01-01'
AND csv.id IS NULL
AND a.account_id IN (
    SELECT DISTINCT account_id FROM core_rbcmonthly
)
UNION ALL
SELECT 
    a.account_id,
    a.name AS account_name,
    'Missing in RBC',
    csv.statement_month,
    csv.currency
FROM core_account a
JOIN core_monthlystatement csv ON a.id = csv.account_id
LEFT JOIN core_rbcmonthly rbc 
    ON a.id = rbc.account_id 
    AND rbc.statement_date = csv.statement_month 
    AND rbc.currency = csv.currency
WHERE csv.statement_month >= '2020-01-01'
AND rbc.id IS NULL
AND a.account_id IN (
    SELECT DISTINCT account_id FROM core_rbcmonthly
)
ORDER BY 1, 3, 4;
