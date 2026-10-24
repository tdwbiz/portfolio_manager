-- ============================================================================
-- RBC PDF Data Continuity Report (2020+)
-- ============================================================================
-- Purpose: Find the earliest continuous data per account for:
--   1. [core_rbcannual] - Annual investment performance reports
--   2. [core_rbcmonthly] - Monthly statements
-- 
-- Strategy: Use CSV transactions (core_transaction) as primary source,
--           fill gaps with PDF data where available.
-- ============================================================================


-- ============================================================================
-- QUERY 1: RBC Annual - Earliest Continuous Data Per Account (2020+)
-- ============================================================================
-- Shows the year range per account, counting only years with complete data.
-- A "complete" annual record has non-zero bal_ending_value.
-- ============================================================================

SELECT 
    a.id AS account_id,
    a.account_id AS account_display,
    a.name AS account_name,
    a.category AS account_type,
    -- Annual data range
    MIN(ra.report_year) AS earliest_year,
    MAX(ra.report_year) AS latest_year,
    DATEDIFF(YEAR, MIN(ra.report_year), MAX(ra.report_year)) + 1 AS year_span,
    COUNT(*) AS annual_records,
    -- Data completeness
    COUNT(CASE WHEN ra.bal_ending_value > 0 THEN 1 END) AS records_with_value,
    COUNT(CASE WHEN ra.bal_ending_value = 0 THEN 1 END) AS records_zero_value,
    -- Currency breakdown
    COUNT(CASE WHEN ra.currency = 'CAD' THEN 1 END) AS cad_records,
    COUNT(CASE WHEN ra.currency = 'USD' THEN 1 END) AS usd_records,
    -- Latest annual record details
    (SELECT TOP 1 ra2.report_year FROM dbo.core_rbcannual ra2 WHERE ra2.account_id = a.id ORDER BY ra2.report_year DESC) AS latest_year_record,
    (SELECT TOP 1 ra2.bal_ending_value FROM dbo.core_rbcannual ra2 WHERE ra2.account_id = a.id ORDER BY ra2.report_year DESC) AS latest_ending_value
FROM dbo.core_account a
JOIN dbo.core_rbcannual ra ON a.id = ra.account_id
WHERE a.broker = 'RBC'
GROUP BY a.id, a.account_id, a.name, a.category
ORDER BY a.account_id;


-- ============================================================================
-- QUERY 2: RBC Monthly - Earliest Continuous Data Per Account (2020+)
-- ============================================================================
-- Shows the date range per account, counting statements and checking continuity.
-- A "continuous" monthly record means no more than 3 months gap between statements.
-- ============================================================================

WITH monthly_gaps AS (
    SELECT 
        a.id AS account_id,
        a.account_id AS account_display,
        a.name AS account_name,
        rm.statement_date,
        rm.currency,
        rm.end_total_market_value,
        -- Calculate months since previous statement
        DATEDIFF(MONTH, 
            LAG(rm.statement_date) OVER (PARTITION BY a.id, rm.currency ORDER BY rm.statement_date),
            rm.statement_date
        ) AS months_since_last
    FROM dbo.core_account a
    JOIN dbo.core_rbcmonthly rm ON a.id = rm.account_id
    WHERE a.broker = 'RBC'
    AND rm.statement_date >= '2020-01-01'
)
SELECT 
    account_id,
    account_display,
    account_name,
    currency,
    MIN(statement_date) AS earliest_statement,
    MAX(statement_date) AS latest_statement,
    DATEDIFF(MONTH, MIN(statement_date), MAX(statement_date)) AS month_span,
    COUNT(*) AS statement_count,
    -- Continuity check: count gaps > 3 months
    COUNT(CASE WHEN months_since_last > 3 THEN 1 END) AS large_gaps,
    -- Latest statement details
    MAX(CASE WHEN statement_date = (SELECT MAX(rm2.statement_date) FROM dbo.core_rbcmonthly rm2 WHERE rm2.account_id = account_id) 
        THEN end_total_market_value END) AS latest_balance
FROM monthly_gaps
GROUP BY account_id, account_display, account_name, currency
ORDER BY account_display, currency;


-- ============================================================================
-- QUERY 3: Combined Annual + Monthly Coverage (2020+)
-- ============================================================================
-- Shows which accounts have both annual and monthly data, and their ranges.
-- This helps identify where CSV transactions should be the primary source.
-- ============================================================================

SELECT 
    a.id AS account_id,
    a.account_id AS account_display,
    a.name AS account_name,
    a.category AS account_type,
    -- Annual coverage
    (SELECT MIN(ra.report_year) FROM dbo.core_rbcannual ra WHERE ra.account_id = a.id) AS annual_earliest_year,
    (SELECT MAX(ra.report_year) FROM dbo.core_rbcannual ra WHERE ra.account_id = a.id) AS annual_latest_year,
    (SELECT COUNT(*) FROM dbo.core_rbcannual ra WHERE ra.account_id = a.id) AS annual_count,
    -- Monthly coverage
    (SELECT MIN(rm.statement_date) FROM dbo.core_rbcmonthly rm WHERE rm.account_id = a.id) AS monthly_earliest_date,
    (SELECT MAX(rm.statement_date) FROM dbo.core_rbcmonthly rm WHERE rm.account_id = a.id) AS monthly_latest_date,
    (SELECT COUNT(*) FROM dbo.core_rbcmonthly rm WHERE rm.account_id = a.id) AS monthly_count,
    -- Balance coverage (from monthly balances)
    (SELECT COUNT(DISTINCT rm.id) FROM dbo.core_rbcmonthly rm JOIN dbo.core_rbcmonthlybalance rmb ON rm.id = rmb.rbc_monthly_id WHERE rm.account_id = a.id) AS balance_count,
    -- CSV transaction coverage (primary source)
    (SELECT COUNT(*) FROM dbo.core_transaction ct WHERE ct.account_id = a.id AND ct.date >= '2020-01-01') AS csv_tx_count_2020_plus,
    (SELECT MIN(ct.date) FROM dbo.core_transaction ct WHERE ct.account_id = a.id AND ct.date >= '2020-01-01') AS csv_earliest_2020,
    (SELECT MAX(ct.date) FROM dbo.core_transaction ct WHERE ct.account_id = a.id AND ct.date >= '2020-01-01') AS csv_latest
FROM dbo.core_account a
WHERE a.broker = 'RBC'
ORDER BY a.account_id;


-- ============================================================================
-- QUERY 4: Annual Data Gaps by Year (All Accounts)
-- ============================================================================
-- Shows which years have annual records across all accounts.
-- Helps identify missing years that might need PDF re-import.
-- ============================================================================

SELECT 
    report_year,
    COUNT(*) AS account_count,
    COUNT(CASE WHEN bal_ending_value > 0 THEN 1 END) AS accounts_with_value,
    SUM(bal_ending_value) AS total_ending_value,
    STRING_AGG(account_display, ', ') AS accounts
FROM (
    SELECT 
        ra.report_year,
        ra.bal_ending_value,
        a.account_id AS account_display
    FROM dbo.core_rbcannual ra
    JOIN dbo.core_account a ON ra.account_id = a.id
    WHERE a.broker = 'RBC'
    AND ra.report_year >= 2020
) sub
GROUP BY report_year
ORDER BY report_year;


-- ============================================================================
-- QUERY 5: Monthly Statement Continuity Analysis (2020+)
-- ============================================================================
-- For each account, shows the expected vs actual statement count per year.
-- Expected: 12 statements per year (monthly). Actual: what we have.
-- ============================================================================

WITH yearly_counts AS (
    SELECT 
        a.id AS account_id,
        a.account_id AS account_display,
        a.name AS account_name,
        YEAR(rm.statement_date) AS statement_year,
        COUNT(DISTINCT rm.statement_date) AS actual_statements,
        COUNT(DISTINCT rm.currency) AS currencies,
        MIN(rm.statement_date) AS first_statement,
        MAX(rm.statement_date) AS last_statement
    FROM dbo.core_account a
    JOIN dbo.core_rbcmonthly rm ON a.id = rm.account_id
    WHERE a.broker = 'RBC'
    AND rm.statement_date >= '2020-01-01' and  rm.statement_date < '2026-01-01'
    GROUP BY a.id, a.account_id, a.name, YEAR(rm.statement_date)
)
SELECT 
    account_id,
    account_display,
    account_name,
    statement_year,
    actual_statements,
    currencies,
    actual_statements * currencies AS expected_total,
    first_statement,
    last_statement,
    CASE 
        WHEN actual_statements >= 10 THEN 'Good'
        WHEN actual_statements >= 6 THEN 'Partial'
        ELSE 'Sparse'
    END AS coverage_quality
FROM yearly_counts
ORDER BY account_display, statement_year;


-- ============================================================================
-- QUERY 6: Accounts with Best Annual Data Coverage (2020+)
-- ============================================================================
-- Ranks accounts by how many years of annual data they have since 2020.
-- These are the accounts where PDF annual reports are most valuable.
-- ============================================================================

SELECT TOP 10
    a.id AS account_id,
    a.account_id AS account_display,
    a.name AS account_name,
    a.category AS account_type,
    COUNT(*) AS annual_records,
    MIN(ra.report_year) AS earliest_year,
    MAX(ra.report_year) AS latest_year,
    MAX(ra.report_year) - MIN(ra.report_year) + 1 AS potential_years,
    CAST(COUNT(*) AS FLOAT) / (MAX(ra.report_year) - MIN(ra.report_year) + 1) * 100 AS coverage_pct,
    -- Latest annual data
    (SELECT TOP 1 ra2.bal_ending_value FROM dbo.core_rbcannual ra2 WHERE ra2.account_id = a.id ORDER BY ra2.report_year DESC) AS latest_value,
    (SELECT TOP 1 ra2.perf_5yr FROM dbo.core_rbcannual ra2 WHERE ra2.account_id = a.id ORDER BY ra2.report_year DESC) AS latest_5yr_return
FROM dbo.core_account a
JOIN dbo.core_rbcannual ra ON a.id = ra.account_id
WHERE a.broker = 'RBC'
AND ra.report_year >= 2020
GROUP BY a.id, a.account_id, a.name, a.category
ORDER BY coverage_pct DESC, annual_records DESC;


-- ============================================================================
-- QUERY 7: Accounts with Best Monthly Data Coverage (2020+)
-- ============================================================================
-- Ranks accounts by how many monthly statements they have since 2020.
-- These are the accounts where PDF monthly statements provide good detail.
-- ============================================================================

SELECT TOP 10
    a.id AS account_id,
    a.account_id AS account_display,
    a.name AS account_name,
    a.category AS account_type,
    COUNT(DISTINCT rm.id) AS statement_count,
    COUNT(rmt.id) AS transaction_count,
    MIN(rm.statement_date) AS earliest_statement,
    MAX(rm.statement_date) AS latest_statement,
    DATEDIFF(MONTH, MIN(rm.statement_date), MAX(rm.statement_date)) + 1 AS month_span,
    CAST(COUNT(DISTINCT rm.id) AS FLOAT) / (DATEDIFF(MONTH, MIN(rm.statement_date), MAX(rm.statement_date)) + 1) * 100 AS coverage_pct,
    -- Latest balance
    (SELECT TOP 1 rm2.end_total_market_value FROM dbo.core_rbcmonthly rm2 WHERE rm2.account_id = a.id ORDER BY rm2.statement_date DESC) AS latest_balance
FROM dbo.core_account a
JOIN dbo.core_rbcmonthly rm ON a.id = rm.account_id
LEFT JOIN dbo.core_rbcmonthlytransaction rmt ON rm.id = rmt.rbc_monthly_id
WHERE a.broker = 'RBC'
AND rm.statement_date >= '2020-01-01'
GROUP BY a.id, a.account_id, a.name, a.category
ORDER BY coverage_pct DESC, statement_count DESC;


-- ============================================================================
-- QUERY 8: Summary Report - Data Source Recommendations
-- ============================================================================
-- Based on coverage analysis, recommends which data source to use per account.
-- Priority: CSV transactions (primary) > PDF annual > PDF monthly balances
-- ============================================================================

SELECT 
    a.id AS account_id,
    a.account_id AS account_display,
    a.name AS account_name,
    a.category AS account_type,
    -- CSV coverage
    (SELECT COUNT(*) FROM dbo.core_transaction ct WHERE ct.account_id = a.id AND ct.date >= '2020-01-01') AS csv_tx_count,
    (SELECT MIN(ct.date) FROM dbo.core_transaction ct WHERE ct.account_id = a.id AND ct.date >= '2020-01-01') AS csv_earliest,
    -- Annual PDF coverage
    (SELECT COUNT(*) FROM dbo.core_rbcannual ra WHERE ra.account_id = a.id AND ra.report_year >= 2020) AS annual_count,
    (SELECT MIN(ra.report_year) FROM dbo.core_rbcannual ra WHERE ra.account_id = a.id AND ra.report_year >= 2020) AS annual_earliest,
    -- Monthly PDF coverage
    (SELECT COUNT(*) FROM dbo.core_rbcmonthly rm WHERE rm.account_id = a.id AND rm.statement_date >= '2020-01-01') AS monthly_count,
    (SELECT MIN(rm.statement_date) FROM dbo.core_rbcmonthly rm WHERE rm.account_id = a.id AND rm.statement_date >= '2020-01-01') AS monthly_earliest,
    -- Balance PDF coverage
    (SELECT COUNT(*) FROM dbo.core_rbcmonthly rm JOIN dbo.core_rbcmonthlybalance rmb ON rm.id = rmb.rbc_monthly_id WHERE rm.account_id = a.id AND rm.statement_date >= '2020-01-01') AS balance_count,
    -- Recommendation
    CASE 
        WHEN (SELECT COUNT(*) FROM dbo.core_rbcannual ra WHERE ra.account_id = a.id AND ra.report_year >= 2020) > 0 
            THEN 'Use CSV for transactions, PDF annual for performance data'
        WHEN (SELECT COUNT(*) FROM dbo.core_rbcmonthly rm WHERE rm.account_id = a.id AND rm.statement_date >= '2020-01-01') > 6 
            THEN 'Use CSV for transactions, PDF monthly for balance snapshots'
        ELSE 'Rely primarily on CSV data'
    END AS data_strategy
FROM dbo.core_account a
WHERE a.broker = 'RBC'
ORDER BY a.account_id;


-- ============================================================================
-- QUERY 9: Earliest Continuous Data - Executive Summary
-- ============================================================================
-- High-level overview of data availability across all RBC accounts since 2020.
-- ============================================================================

SELECT 
    'Annual Reports' AS data_type,
    COUNT(DISTINCT ra.account_id) AS account_count,
    COUNT(*) AS record_count,
    MIN(ra.report_year) AS earliest,
    MAX(ra.report_year) AS latest,
    COUNT(DISTINCT ra.currency) AS currencies
FROM dbo.core_rbcannual ra
JOIN dbo.core_account a ON ra.account_id = a.id
WHERE a.broker = 'RBC'
AND ra.report_year >= 2020

UNION ALL

SELECT 
    'Monthly Statements' AS data_type,
    COUNT(DISTINCT rm.account_id) AS account_count,
    COUNT(*) AS record_count,
    MIN(rm.statement_date) AS earliest,
    MAX(rm.statement_date) AS latest,
    COUNT(DISTINCT rm.currency) AS currencies
FROM dbo.core_rbcmonthly rm
JOIN dbo.core_account a ON rm.account_id = a.id
WHERE a.broker = 'RBC'
AND rm.statement_date >= '2020-01-01'

UNION ALL

SELECT 
    'Monthly Balances' AS data_type,
    COUNT(DISTINCT rm.account_id) AS account_count,
    COUNT(DISTINCT rm.id) AS record_count,
    MIN(rm.statement_date) AS earliest,
    MAX(rm.statement_date) AS latest,
    COUNT(DISTINCT rm.currency) AS currencies
FROM dbo.core_rbcmonthly rm
JOIN dbo.core_rbcmonthlybalance rmb ON rm.id = rmb.rbc_monthly_id
JOIN dbo.core_account a ON rm.account_id = a.id
WHERE a.broker = 'RBC'
AND rm.statement_date >= '2020-01-01'

UNION ALL

SELECT 
    'CSV Transactions' AS data_type,
    COUNT(DISTINCT ct.account_id) AS account_count,
    COUNT(*) AS record_count,
    MIN(ct.date) AS earliest,
    MAX(ct.date) AS latest,
    COUNT(DISTINCT ct.currency) AS currencies
FROM dbo.core_transaction ct
JOIN dbo.core_account a ON ct.account_id = a.id
WHERE a.broker = 'RBC'
AND ct.date >= '2020-01-01'

ORDER BY data_type;


-- ============================================================================
-- QUERY 10: Per-Account Earliest Continuous Data (Consolidated View)
-- ============================================================================
-- Single query showing the earliest continuous data source per account.
-- Shows which source (Annual/Monthly/CSV) has the earliest data.
-- ============================================================================

SELECT 
    a.id AS account_id,
    a.account_id AS account_display,
    a.name AS account_name,
    a.category AS account_type,
    -- Annual earliest
    (SELECT MIN(ra.report_year) FROM dbo.core_rbcannual ra WHERE ra.account_id = a.id AND ra.report_year >= 2020) AS annual_earliest_year,
    (SELECT COUNT(*) FROM dbo.core_rbcannual ra WHERE ra.account_id = a.id AND ra.report_year >= 2020) AS annual_count,
    -- Monthly earliest
    (SELECT MIN(rm.statement_date) FROM dbo.core_rbcmonthly rm WHERE rm.account_id = a.id AND rm.statement_date >= '2020-01-01') AS monthly_earliest_date,
    (SELECT COUNT(*) FROM dbo.core_rbcmonthly rm WHERE rm.account_id = a.id AND rm.statement_date >= '2020-01-01') AS monthly_count,
    -- CSV earliest
    (SELECT MIN(ct.date) FROM dbo.core_transaction ct WHERE ct.account_id = a.id AND ct.date >= '2020-01-01') AS csv_earliest_date,
    (SELECT COUNT(*) FROM dbo.core_transaction ct WHERE ct.account_id = a.id AND ct.date >= '2020-01-01') AS csv_count,
    -- Primary source recommendation
    CASE 
        WHEN (SELECT MIN(ra.report_year) FROM dbo.core_rbcannual ra WHERE ra.account_id = a.id AND ra.report_year >= 2020) IS NOT NULL
            THEN 'Annual PDF'
        WHEN (SELECT MIN(rm.statement_date) FROM dbo.core_rbcmonthly rm WHERE rm.account_id = a.id AND rm.statement_date >= '2020-01-01') IS NOT NULL
            THEN 'Monthly PDF'
        ELSE 'CSV Only'
    END AS primary_source
FROM dbo.core_account a
WHERE a.broker = 'RBC'
ORDER BY a.account_id;
