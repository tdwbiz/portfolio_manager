-- ============================================================================
-- RBC CSV vs PDF Transaction Comparison
-- ============================================================================
-- Purpose: Compare core_transaction (CSV import) vs core_rbcmonthlytransaction (PDF import)
-- to identify gaps and determine which data is more reliable
-- Database: PortfolioDEV.dbo
-- Date: 2026-06-17
-- ============================================================================


-- ============================================================================
-- QUERY 1: Core Transaction Summary by Account
-- ============================================================================
-- Get earliest/latest dates and transaction counts from CSV import
-- ============================================================================

SELECT 
    a.id AS id,
    a.account_id AS account_display,
    a.name AS account_name,
    COUNT(ct.id) AS transaction_count,
    MIN(ct.date) AS earliest_date,
    MAX(ct.date) AS latest_date,
    SUM(ct.amount) AS total_amount,
    COUNT(CASE WHEN ct.type = 'BUY' THEN 1 END) AS buy_count,
    COUNT(CASE WHEN ct.type = 'SELL' THEN 1 END) AS sell_count,
    COUNT(CASE WHEN ct.type = 'DIVIDEND' THEN 1 END) AS dividend_count,
    COUNT(CASE WHEN ct.type = 'CASH' THEN 1 END) AS cash_count
FROM dbo.core_account a
JOIN dbo.core_transaction ct ON a.id = ct.account_id
WHERE a.broker = 'RBC'
GROUP BY a.id, a.account_id, a.name
ORDER BY a.account_id;


-- ============================================================================
-- QUERY 2: Core RBC Monthly Transaction Summary by Account
-- ============================================================================
-- Get earliest/latest dates and transaction counts from PDF import
-- ============================================================================

SELECT 
    a.id AS id,
    a.account_id AS account_display,
    a.name AS account_name,
    COUNT(rmt.id) AS transaction_count,
    MIN(rm.statement_date) AS earliest_statement,
    MAX(rm.statement_date) AS latest_statement,
    SUM(rmt.credit) AS total_credits,
    SUM(rmt.debit) AS total_debits,
    COUNT(CASE WHEN rmt.activity_type = 'BUY' THEN 1 END) AS buy_count,
    COUNT(CASE WHEN rmt.activity_type = 'SELL' THEN 1 END) AS sell_count,
    COUNT(CASE WHEN rmt.activity_type = 'DIVIDEND' THEN 1 END) AS dividend_count,
    COUNT(CASE WHEN rmt.activity_type = 'CASH' THEN 1 END) AS cash_count
FROM dbo.core_account a
JOIN dbo.core_rbcmonthly rm ON a.id = rm.account_id
JOIN dbo.core_rbcmonthlytransaction rmt ON rm.id = rmt.rbc_monthly_id
WHERE a.broker = 'RBC'
GROUP BY a.id, a.account_id, a.name
ORDER BY a.account_id;


-- ============================================================================
-- QUERY 3: Date Range Comparison (CSV vs PDF)
-- ============================================================================
-- Compare date ranges to see which covers more historical data
-- ============================================================================

SELECT 
    'CSV Import' AS source,
    a.id AS account_id,
    a.account_id AS account_display,
    MIN(ct.date) AS earliest_date,
    MAX(ct.date) AS latest_date,
    DATEDIFF(DAY, MIN(ct.date), MAX(ct.date)) AS date_span_days,
    COUNT(ct.id) AS transaction_count
FROM dbo.core_account a
JOIN dbo.core_transaction ct ON a.id = ct.account_id
WHERE a.broker = 'RBC'
GROUP BY a.id, a.account_id

UNION ALL

SELECT 
    'PDF Import' AS source,
    a.id AS account_id,
    a.account_id AS account_display,
    MIN(rm.statement_date) AS earliest_date,
    MAX(rm.statement_date) AS latest_date,
    DATEDIFF(DAY, MIN(rm.statement_date), MAX(rm.statement_date)) AS date_span_days,
    COUNT(rmt.id) AS transaction_count
FROM dbo.core_account a
JOIN dbo.core_rbcmonthly rm ON a.id = rm.account_id
JOIN dbo.core_rbcmonthlytransaction rmt ON rm.id = rmt.rbc_monthly_id
WHERE a.broker = 'RBC'
GROUP BY a.id, a.account_id

ORDER BY 2, 1
 --   CASE source WHEN 'CSV Import' THEN 1 ELSE 2 END;


-- ============================================================================
-- QUERY 4: Transaction Type Distribution (CSV vs PDF)
-- ============================================================================
-- Compare how transactions are categorized in each source
-- ============================================================================

SELECT 
    'CSV Import' AS source,
    ct.type AS transaction_type,
    COUNT(*) AS transaction_count,
    SUM(ct.amount) AS total_amount
FROM dbo.core_account a
JOIN dbo.core_transaction ct ON a.id = ct.account_id
WHERE a.broker = 'RBC'
GROUP BY ct.type

UNION ALL

SELECT 
    'PDF Import' AS source,
    rmt.activity_type AS transaction_type,
    COUNT(*) AS transaction_count,
    SUM(rmt.credit - rmt.debit) AS total_amount
FROM dbo.core_account a
JOIN dbo.core_rbcmonthly rm ON a.id = rm.account_id
JOIN dbo.core_rbcmonthlytransaction rmt ON rm.id = rmt.rbc_monthly_id
WHERE a.broker = 'RBC'
GROUP BY rmt.activity_type

ORDER BY source, transaction_type;


-- ============================================================================
-- QUERY 5: Sample Transactions from CSV (first 50 per account)
-- ============================================================================
-- Examine actual CSV transaction data to assess quality
-- ============================================================================

SELECT TOP 50
    a.account_id,
    ct.date,
    ct.type,
    ct.symbol,
    ct.quantity,
    ct.price,
    ct.commission,
    ct.amount,
    ct.notes
FROM dbo.core_account a
JOIN dbo.core_transaction ct ON a.id = ct.account_id
WHERE a.broker = 'RBC'
ORDER BY a.account_id, ct.date;


-- ============================================================================
-- QUERY 6: Sample Transactions from PDF (first 50 per account)
-- ============================================================================
-- Examine actual PDF transaction data to assess quality
-- ============================================================================

SELECT TOP 50
    a.account_id,
    rm.statement_date,
    rmt.activity_type,
    rmt.description,
    rmt.symbol,
    rmt.quantity,
    rmt.price,
    rmt.commission,
    rmt.credit,
    rmt.debit
FROM dbo.core_account a
JOIN dbo.core_rbcmonthly rm ON a.id = rm.account_id
JOIN dbo.core_rbcmonthlytransaction rmt ON rm.id = rmt.rbc_monthly_id
WHERE a.broker = 'RBC'
ORDER BY a.account_id, rm.statement_date;


-- ============================================================================
-- QUERY 7: CSV Transactions by Year
-- ============================================================================
-- Understand the temporal distribution of CSV data
-- ============================================================================

SELECT 
    a.account_id,
    YEAR(ct.date) AS transaction_year,
    COUNT(*) AS transaction_count,
    SUM(ct.amount) AS total_amount,
    MIN(ct.date) AS earliest_in_year,
    MAX(ct.date) AS latest_in_year
FROM dbo.core_account a
JOIN dbo.core_transaction ct ON a.id = ct.account_id
WHERE a.broker = 'RBC'
GROUP BY a.account_id, YEAR(ct.date)
ORDER BY a.account_id, transaction_year;


-- ============================================================================
-- QUERY 8: PDF Statements by Year
-- ============================================================================
-- Understand the temporal distribution of PDF data
-- ============================================================================

SELECT 
    a.account_id,
    YEAR(rm.statement_date) AS statement_year,
    COUNT(DISTINCT rm.id) AS statement_count,
    COUNT(rmt.id) AS transaction_count,
    SUM(rmt.credit - rmt.debit) AS total_amount,
    MIN(rm.statement_date) AS earliest_in_year,
    MAX(rm.statement_date) AS latest_in_year
FROM dbo.core_account a
JOIN dbo.core_rbcmonthly rm ON a.id = rm.account_id
JOIN dbo.core_rbcmonthlytransaction rmt ON rm.id = rmt.rbc_monthly_id
WHERE a.broker = 'RBC'
GROUP BY a.account_id, YEAR(rm.statement_date)
ORDER BY a.account_id, statement_year;


-- ============================================================================
-- QUERY 9: CSV Transaction Amount Distribution
-- ============================================================================
-- Check for suspicious amounts in CSV data
-- ============================================================================

SELECT 
    a.account_id,
    CASE 
        WHEN ct.amount < -10000 THEN '< -$10,000'
        WHEN ct.amount BETWEEN -10000 AND -1000 THEN '-$10,000 to -$1,000'
        WHEN ct.amount BETWEEN -1000 AND -100 THEN '-$1,000 to -$100'
        WHEN ct.amount BETWEEN -100 AND 0 THEN '-$100 to $0'
        WHEN ct.amount BETWEEN 0 AND 100 THEN '$0 to $100'
        WHEN ct.amount BETWEEN 100 AND 1000 THEN '$100 to $1,000'
        WHEN ct.amount BETWEEN 1000 AND 10000 THEN '$1,000 to $10,000'
        ELSE '> $10,000'
    END AS amount_range,
    COUNT(*) AS transaction_count,
    SUM(ct.amount) AS total_amount,
    AVG(ct.amount) AS avg_amount
FROM dbo.core_account a
JOIN dbo.core_transaction ct ON a.id = ct.account_id
WHERE a.broker = 'RBC'
GROUP BY a.account_id, 
    CASE 
        WHEN ct.amount < -10000 THEN '< -$10,000'
        WHEN ct.amount BETWEEN -10000 AND -1000 THEN '-$10,000 to -$1,000'
        WHEN ct.amount BETWEEN -1000 AND -100 THEN '-$1,000 to -$100'
        WHEN ct.amount BETWEEN -100 AND 0 THEN '-$100 to $0'
        WHEN ct.amount BETWEEN 0 AND 100 THEN '$0 to $100'
        WHEN ct.amount BETWEEN 100 AND 1000 THEN '$100 to $1,000'
        WHEN ct.amount BETWEEN 1000 AND 10000 THEN '$1,000 to $10,000'
        ELSE '> $10,000'
    END
ORDER BY a.account_id, amount_range;


-- ============================================================================
-- QUERY 10: PDF Transaction Amount Distribution
-- ============================================================================
-- Check for suspicious amounts in PDF data
-- ============================================================================

SELECT 
    a.account_id,
    CASE 
        WHEN (rmt.credit - rmt.debit) < -10000 THEN '< -$10,000'
        WHEN (rmt.credit - rmt.debit) BETWEEN -10000 AND -1000 THEN '-$10,000 to -$1,000'
        WHEN (rmt.credit - rmt.debit) BETWEEN -1000 AND -100 THEN '-$1,000 to -$100'
        WHEN (rmt.credit - rmt.debit) BETWEEN -100 AND 0 THEN '-$100 to $0'
        WHEN (rmt.credit - rmt.debit) BETWEEN 0 AND 100 THEN '$0 to $100'
        WHEN (rmt.credit - rmt.debit) BETWEEN 100 AND 1000 THEN '$100 to $1,000'
        WHEN (rmt.credit - rmt.debit) BETWEEN 1000 AND 10000 THEN '$1,000 to $10,000'
        ELSE '> $10,000'
    END AS amount_range,
    COUNT(*) AS transaction_count,
    SUM(rmt.credit - rmt.debit) AS total_amount,
    AVG(rmt.credit - rmt.debit) AS avg_amount
FROM dbo.core_account a
JOIN dbo.core_rbcmonthly rm ON a.id = rm.account_id
JOIN dbo.core_rbcmonthlytransaction rmt ON rm.id = rmt.rbc_monthly_id
WHERE a.broker = 'RBC'
GROUP BY a.account_id, 
    CASE 
        WHEN (rmt.credit - rmt.debit) < -10000 THEN '< -$10,000'
        WHEN (rmt.credit - rmt.debit) BETWEEN -10000 AND -1000 THEN '-$10,000 to -$1,000'
        WHEN (rmt.credit - rmt.debit) BETWEEN -1000 AND -100 THEN '-$1,000 to -$100'
        WHEN (rmt.credit - rmt.debit) BETWEEN -100 AND 0 THEN '-$100 to $0'
        WHEN (rmt.credit - rmt.debit) BETWEEN 0 AND 100 THEN '$0 to $100'
        WHEN (rmt.credit - rmt.debit) BETWEEN 100 AND 1000 THEN '$100 to $1,000'
        WHEN (rmt.credit - rmt.debit) BETWEEN 1000 AND 10000 THEN '$1,000 to $10,000'
        ELSE '> $10,000'
    END
ORDER BY a.account_id, amount_range;


-- ============================================================================
-- QUERY 11: Gap Analysis - Dates in CSV but not covered by PDF statements
-- ============================================================================
-- Find CSV transactions that fall outside PDF statement date ranges
-- ============================================================================

-- For each account, find the date range covered by PDF statements
-- then identify CSV transactions outside that range

SELECT 
    a.account_id,
    'CSV Only' AS source,
    MIN(ct.date) AS date_range_start,
    MAX(ct.date) AS date_range_end,
    COUNT(*) AS transaction_count
FROM dbo.core_account a
JOIN dbo.core_transaction ct ON a.id = ct.account_id
WHERE a.broker = 'RBC'
AND (ct.date < (
        SELECT MIN(rm.statement_date)
        FROM dbo.core_rbcmonthly rm
        WHERE rm.account_id = a.id
    )
    OR ct.date > (
        SELECT MAX(rm.statement_date)
        FROM dbo.core_rbcmonthly rm
        WHERE rm.account_id = a.id
    ))
GROUP BY a.account_id

UNION ALL

SELECT 
    a.account_id,
    'PDF Only' AS source,
    MIN(rm.statement_date) AS date_range_start,
    MAX(rm.statement_date) AS date_range_end,
    COUNT(DISTINCT rm.id) AS statement_count
FROM dbo.core_account a
JOIN dbo.core_rbcmonthly rm ON a.id = rm.account_id
WHERE a.broker = 'RBC'
AND NOT EXISTS (
    SELECT 1 
    FROM dbo.core_transaction ct 
    WHERE ct.account_id = a.id 
    AND ct.date >= DATEADD(MONTH, -1, rm.statement_date)
    AND ct.date <= DATEADD(MONTH, 1, rm.statement_date)
)
GROUP BY a.account_id

ORDER BY a.account_id, source;


-- ============================================================================
-- QUERY 12: Complete Account Overview (CSV + PDF combined)
-- ============================================================================
-- Summary of both data sources for each account
-- ============================================================================

SELECT 
    a.id AS account_id,
    a.account_id AS account_display,
    a.name AS account_name,
    a.currency,
    -- CSV data
    (SELECT COUNT(*) FROM dbo.core_transaction ct WHERE ct.account_id = a.id) AS csv_transaction_count,
    (SELECT MIN(ct.date) FROM dbo.core_transaction ct WHERE ct.account_id = a.id) AS csv_earliest,
    (SELECT MAX(ct.date) FROM dbo.core_transaction ct WHERE ct.account_id = a.id) AS csv_latest,
    (SELECT SUM(ct.amount) FROM dbo.core_transaction ct WHERE ct.account_id = a.id) AS csv_total_amount,
    -- PDF data
    (SELECT COUNT(*) FROM dbo.core_rbcmonthly rm JOIN dbo.core_rbcmonthlytransaction rmt ON rm.id = rmt.rbc_monthly_id WHERE rm.account_id = a.id) AS pdf_transaction_count,
    (SELECT MIN(rm.statement_date) FROM dbo.core_rbcmonthly rm JOIN dbo.core_rbcmonthlytransaction rmt ON rm.id = rmt.rbc_monthly_id WHERE rm.account_id = a.id) AS pdf_earliest,
    (SELECT MAX(rm.statement_date) FROM dbo.core_rbcmonthly rm JOIN dbo.core_rbcmonthlytransaction rmt ON rm.id = rmt.rbc_monthly_id WHERE rm.account_id = a.id) AS pdf_latest,
    (SELECT SUM(rmt.credit - rmt.debit) FROM dbo.core_rbcmonthly rm JOIN dbo.core_rbcmonthlytransaction rmt ON rm.id = rmt.rbc_monthly_id WHERE rm.account_id = a.id) AS pdf_total_amount,
    -- Balance data
    (SELECT MAX(end_total_market_value) FROM dbo.core_rbcmonthly rm WHERE rm.account_id = a.id) AS latest_balance,
    -- Latest FX rate (TOP 1 for MSSQL)
    (SELECT TOP 1 rm.fx_rate FROM dbo.core_rbcmonthly rm WHERE rm.account_id = a.id ORDER BY rm.statement_date DESC) AS latest_fx_rate
FROM dbo.core_account a
WHERE a.broker = 'RBC'
ORDER BY a.account_id;
