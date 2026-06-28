-- ============================================================================
-- RBC Problematic Files - Find PDFs with Reconciliation Errors
-- ============================================================================
-- Purpose: Identify all unique file_names that have any reconciliation errors
-- so they can be re-imported selectively
-- Database: PortfolioDEV.dbo
-- Date: 2026-06-17
-- ============================================================================


-- ============================================================================
-- QUERY 1: All Files with Balance Sum Mismatch (end_total ≠ SUM(balances))
-- ============================================================================
-- This is the PRIMARY error - VL parser extracted wrong market value
-- ============================================================================

SELECT DISTINCT
    rm.file_name,
    rm.account_id,
    a.account_id AS account_display,
    rm.statement_date,
    rm.currency,
    rm.end_total_market_value,
    COALESCE(SUM(rmb.market_value), 0) AS sum_balances,
    ABS(rm.end_total_market_value - COALESCE(SUM(rmb.market_value), 0)) AS discrepancy
FROM dbo.core_rbcmonthly rm
JOIN dbo.core_account a ON rm.account_id = a.id
LEFT JOIN dbo.core_rbcmonthlybalance rmb ON rm.id = rmb.rbc_monthly_id
WHERE a.broker = 'RBC'
GROUP BY rm.file_name, rm.account_id, a.account_id, rm.statement_date, 
         rm.currency, rm.end_total_market_value
HAVING ABS(rm.end_total_market_value - COALESCE(SUM(rmb.market_value), 0)) > 1.00
ORDER BY discrepancy DESC;


-- ============================================================================
-- QUERY 2: All Files with Begin/End Mismatch (statement N ≠ statement N-1)
-- ============================================================================
-- Files where current begin_total doesn't match previous end_total
-- ============================================================================

SELECT DISTINCT
    curr.file_name AS curr_file,
    prev.file_name AS prev_file,
    curr.account_id,
    a.account_id AS account_display,
    prev.statement_date AS prev_date,
    curr.statement_date AS curr_date,
    prev.end_total_market_value AS prev_end,
    curr.begin_total_market_value AS curr_begin,
    ABS(prev.end_total_market_value - curr.begin_total_market_value) AS discrepancy
FROM dbo.core_rbcmonthly curr
JOIN dbo.core_rbcmonthly prev ON 
    curr.account_id = prev.account_id 
    AND curr.currency = prev.currency
    AND prev.statement_date = (
        SELECT MAX(p2.statement_date) 
        FROM dbo.core_rbcmonthly p2 
        WHERE p2.account_id = curr.account_id 
        AND p2.currency = curr.currency 
        AND p2.statement_date < curr.statement_date
    )
JOIN dbo.core_account a ON curr.account_id = a.id
WHERE a.broker = 'RBC'
AND ABS(prev.end_total_market_value - curr.begin_total_market_value) > 1.00
ORDER BY discrepancy DESC;


-- ============================================================================
-- QUERY 3: All Files with Transaction Cash Flow Mismatch
-- ============================================================================
-- Files where: begin + credits - debits ≠ end_total
-- ============================================================================

SELECT DISTINCT
    rm.file_name,
    rm.account_id,
    a.account_id AS account_display,
    rm.statement_date,
    rm.currency,
    rm.begin_total_market_value,
    rm.end_total_market_value,
    COALESCE(SUM(CASE WHEN rmt.credit > 0 THEN rmt.credit ELSE 0 END), 0) AS total_credits,
    COALESCE(SUM(CASE WHEN rmt.debit > 0 THEN rmt.debit ELSE 0 END), 0) AS total_debits,
    rm.begin_total_market_value 
        + (COALESCE(SUM(CASE WHEN rmt.credit > 0 THEN rmt.credit ELSE 0 END), 0) 
         - COALESCE(SUM(CASE WHEN rmt.debit > 0 THEN rmt.debit ELSE 0 END), 0)) 
        AS calculated_end,
    rm.end_total_market_value - (rm.begin_total_market_value 
        + (COALESCE(SUM(CASE WHEN rmt.credit > 0 THEN rmt.credit ELSE 0 END), 0) 
         - COALESCE(SUM(CASE WHEN rmt.debit > 0 THEN rmt.debit ELSE 0 END), 0))) 
        AS discrepancy
FROM dbo.core_rbcmonthly rm
JOIN dbo.core_account a ON rm.account_id = a.id
LEFT JOIN dbo.core_rbcmonthlytransaction rmt ON rm.id = rmt.rbc_monthly_id
WHERE a.broker = 'RBC'
GROUP BY rm.file_name, rm.account_id, a.account_id, rm.statement_date, rm.currency,
         rm.begin_total_market_value, rm.end_total_market_value
HAVING ABS(rm.end_total_market_value - (rm.begin_total_market_value 
        + (COALESCE(SUM(CASE WHEN rmt.credit > 0 THEN rmt.credit ELSE 0 END), 0) 
         - COALESCE(SUM(CASE WHEN rmt.debit > 0 THEN rmt.debit ELSE 0 END), 0)))) > 1.00
ORDER BY discrepancy DESC;


-- ============================================================================
-- QUERY 4: All Files with Market Value Trend Anomalies (>50% change)
-- ============================================================================
-- Files with extreme market value swings that may indicate parser errors
-- ============================================================================

SELECT DISTINCT
    curr.file_name AS curr_file,
    prev.file_name AS prev_file,
    curr.account_id,
    a.account_id AS account_display,
    curr.currency,
    prev.statement_date AS prev_date,
    curr.statement_date AS curr_date,
    prev.end_total_market_value AS prev_end,
    curr.end_total_market_value AS curr_end,
    CASE 
        WHEN prev.end_total_market_value = 0 THEN NULL
        ELSE ROUND((curr.end_total_market_value - prev.end_total_market_value) / prev.end_total_market_value * 100, 2)
    END AS change_pct
FROM dbo.core_rbcmonthly curr
JOIN dbo.core_rbcmonthly prev ON 
    curr.account_id = prev.account_id 
    AND curr.currency = prev.currency
    AND prev.statement_date = (
        SELECT MAX(p2.statement_date) 
        FROM dbo.core_rbcmonthly p2 
        WHERE p2.account_id = curr.account_id 
        AND p2.currency = curr.currency 
        AND p2.statement_date < curr.statement_date
    )
JOIN dbo.core_account a ON curr.account_id = a.id
WHERE a.broker = 'RBC'
AND prev.end_total_market_value > 0
AND ABS((curr.end_total_market_value - prev.end_total_market_value) / prev.end_total_market_value * 100) > 50
ORDER BY ABS((curr.end_total_market_value - prev.end_total_market_value) / prev.end_total_market_value * 100) DESC;


-- ============================================================================
-- QUERY 5: All Files with FX Rate Issues
-- ============================================================================
-- USD statements with suspicious FX rates (should be 0.70-1.60, not 1.000000)
-- ============================================================================

SELECT DISTINCT
    rm.file_name,
    rm.account_id,
    a.account_id AS account_display,
    rm.statement_date,
    rm.currency,
    rm.fx_rate,
    rm.end_total_market_value
FROM dbo.core_rbcmonthly rm
JOIN dbo.core_account a ON rm.account_id = a.id
WHERE a.broker = 'RBC'
  AND rm.currency = 'USD'
  AND (rm.fx_rate < 0.70 OR rm.fx_rate > 1.60 OR rm.fx_rate = 1.000000)
ORDER BY rm.statement_date DESC;


-- ============================================================================
-- QUERY 6: ALL PROBLEMATIC FILES COMBINED (UNIQUE LIST)
-- ============================================================================
-- This is the MAIN query you need - returns all unique file_names with ANY error
-- ============================================================================

SELECT DISTINCT file_name
FROM (
    -- Balance sum mismatches
    SELECT rm.file_name
    FROM dbo.core_rbcmonthly rm
    JOIN dbo.core_account a ON rm.account_id = a.id
    LEFT JOIN dbo.core_rbcmonthlybalance rmb ON rm.id = rmb.rbc_monthly_id
    WHERE a.broker = 'RBC'
    GROUP BY rm.file_name, rm.id, rm.account_id, rm.statement_date, rm.currency, 
             rm.end_total_market_value
    HAVING ABS(rm.end_total_market_value - COALESCE(SUM(rmb.market_value), 0)) > 1.00
    
    UNION
    
    -- Begin/end mismatches (statement-to-statement)
    SELECT curr.file_name AS file_name
    FROM dbo.core_rbcmonthly curr
    JOIN dbo.core_rbcmonthly prev ON 
        curr.account_id = prev.account_id 
        AND curr.currency = prev.currency
        AND prev.statement_date = (
            SELECT MAX(p2.statement_date) 
            FROM dbo.core_rbcmonthly p2 
            WHERE p2.account_id = curr.account_id 
            AND p2.currency = curr.currency 
            AND p2.statement_date < curr.statement_date
        )
    JOIN dbo.core_account a ON curr.account_id = a.id
    WHERE a.broker = 'RBC'
    AND ABS(prev.end_total_market_value - curr.begin_total_market_value) > 1.00
    
    UNION
    
    -- Transaction cash flow mismatches
    SELECT rm.file_name
    FROM dbo.core_rbcmonthly rm
    JOIN dbo.core_account a ON rm.account_id = a.id
    LEFT JOIN dbo.core_rbcmonthlytransaction rmt ON rm.id = rmt.rbc_monthly_id
    WHERE a.broker = 'RBC'
    GROUP BY rm.file_name, rm.id, rm.account_id, rm.statement_date, rm.currency,
             rm.begin_total_market_value, rm.end_total_market_value
    HAVING ABS(rm.end_total_market_value - (rm.begin_total_market_value 
            + (COALESCE(SUM(CASE WHEN rmt.credit > 0 THEN rmt.credit ELSE 0 END), 0) 
             - COALESCE(SUM(CASE WHEN rmt.debit > 0 THEN rmt.debit ELSE 0 END), 0)))) > 1.00
    
    UNION
    
    -- Market value trend anomalies
    SELECT curr.file_name AS file_name
    FROM dbo.core_rbcmonthly curr
    JOIN dbo.core_rbcmonthly prev ON 
        curr.account_id = prev.account_id 
        AND curr.currency = prev.currency
        AND prev.statement_date = (
            SELECT MAX(p2.statement_date) 
            FROM dbo.core_rbcmonthly p2 
            WHERE p2.account_id = curr.account_id 
            AND p2.currency = curr.currency 
            AND p2.statement_date < curr.statement_date
        )
    JOIN dbo.core_account a ON curr.account_id = a.id
    WHERE a.broker = 'RBC'
    AND prev.end_total_market_value > 0
    AND ABS((curr.end_total_market_value - prev.end_total_market_value) / prev.end_total_market_value * 100) > 50
    
    UNION
    
    -- FX rate issues
    SELECT rm.file_name
    FROM dbo.core_rbcmonthly rm
    JOIN dbo.core_account a ON rm.account_id = a.id
    WHERE a.broker = 'RBC'
      AND rm.currency = 'USD'
      AND (rm.fx_rate < 0.70 OR rm.fx_rate > 1.60 OR rm.fx_rate = 1.000000)
) AS problematic_files
ORDER BY file_name;


-- ============================================================================
-- QUERY 7: Problematic Files with Error Details (for prioritization)
-- ============================================================================
-- Shows each problematic file with all error types it has
-- ============================================================================

SELECT 
    file_name,
    account_id,
    a.account_id AS account_display,
    statement_date,
    currency,
    -- Error flags
    CASE WHEN balance_mismatch > 0 THEN 'YES' ELSE 'NO' END AS has_balance_mismatch,
    CASE WHEN begin_end_mismatch > 0 THEN 'YES' ELSE 'NO' END AS has_begin_end_mismatch,
    CASE WHEN tx_mismatch > 0 THEN 'YES' ELSE 'NO' END AS has_tx_mismatch,
    CASE WHEN trend_anomaly > 0 THEN 'YES' ELSE 'NO' END AS has_trend_anomaly,
    CASE WHEN fx_issue > 0 THEN 'YES' ELSE 'NO' END AS has_fx_issue,
    -- Error counts
    balance_mismatch,
    begin_end_mismatch,
    tx_mismatch,
    trend_anomaly,
    fx_issue,
    -- Summary values
    end_total_market_value,
    fx_rate
FROM (
    SELECT 
        rm.file_name,
        rm.account_id,
        rm.statement_date,
        rm.currency,
        rm.end_total_market_value,
        rm.fx_rate,
        -- Balance mismatch count
        (SELECT COUNT(*) FROM dbo.core_rbcmonthlybalance rmb 
         WHERE rmb.rbc_monthly_id = rm.id 
         HAVING ABS(rm.end_total_market_value - SUM(rmb.market_value)) > 1.00) AS balance_mismatch,
        -- Begin/end mismatch (will be 0 or 1 per statement)
        0 AS begin_end_mismatch,
        -- TX mismatch (will be 0 or 1 per statement)
        0 AS tx_mismatch,
        -- Trend anomaly (will be 0 or 1 per statement)
        0 AS trend_anomaly,
        -- FX issue (will be 0 or 1 per statement)
        CASE WHEN rm.currency = 'USD' AND (rm.fx_rate < 0.70 OR rm.fx_rate > 1.60 OR rm.fx_rate = 1.000000) THEN 1 ELSE 0 END AS fx_issue
    FROM dbo.core_rbcmonthly rm
    JOIN dbo.core_account a ON rm.account_id = a.id
    WHERE a.broker = 'RBC'
) AS error_counts
WHERE balance_mismatch > 0 OR begin_end_mismatch > 0 OR tx_mismatch > 0 
      OR trend_anomaly > 0 OR fx_issue > 0
ORDER BY file_name;


-- ============================================================================
-- QUERY 8: Export Problematic Files List (for scripting)
-- ============================================================================
-- Simple list of file_names to re-import, one per line
-- ============================================================================

SELECT rm.file_name
FROM dbo.core_rbcmonthly rm
JOIN dbo.core_account a ON rm.account_id = a.id
LEFT JOIN dbo.core_rbcmonthlybalance rmb ON rm.id = rmb.rbc_monthly_id
WHERE a.broker = 'RBC'
GROUP BY rm.file_name, rm.id, rm.account_id, rm.statement_date, rm.currency, 
         rm.end_total_market_value
HAVING ABS(rm.end_total_market_value - COALESCE(SUM(rmb.market_value), 0)) > 1.00
UNION
SELECT curr.file_name
FROM dbo.core_rbcmonthly curr
JOIN dbo.core_rbcmonthly prev ON 
    curr.account_id = prev.account_id 
    AND curr.currency = prev.currency
    AND prev.statement_date = (
        SELECT MAX(p2.statement_date) 
        FROM dbo.core_rbcmonthly p2 
        WHERE p2.account_id = curr.account_id 
        AND p2.currency = curr.currency 
        AND p2.statement_date < curr.statement_date
    )
JOIN dbo.core_account a ON curr.account_id = a.id
WHERE a.broker = 'RBC'
AND ABS(prev.end_total_market_value - curr.begin_total_market_value) > 1.00
UNION
SELECT rm.file_name
FROM dbo.core_rbcmonthly rm
JOIN dbo.core_account a ON rm.account_id = a.id
LEFT JOIN dbo.core_rbcmonthlytransaction rmt ON rm.id = rmt.rbc_monthly_id
WHERE a.broker = 'RBC'
GROUP BY rm.file_name, rm.id, rm.account_id, rm.statement_date, rm.currency,
         rm.begin_total_market_value, rm.end_total_market_value
HAVING ABS(rm.end_total_market_value - (rm.begin_total_market_value 
        + (COALESCE(SUM(CASE WHEN rmt.credit > 0 THEN rmt.credit ELSE 0 END), 0) 
         - COALESCE(SUM(CASE WHEN rmt.debit > 0 THEN rmt.debit ELSE 0 END), 0)))) > 1.00
UNION
SELECT curr.file_name
FROM dbo.core_rbcmonthly curr
JOIN dbo.core_rbcmonthly prev ON 
    curr.account_id = prev.account_id 
    AND curr.currency = prev.currency
    AND prev.statement_date = (
        SELECT MAX(p2.statement_date) 
        FROM dbo.core_rbcmonthly p2 
        WHERE p2.account_id = curr.account_id 
        AND p2.currency = curr.currency 
        AND p2.statement_date < curr.statement_date
    )
JOIN dbo.core_account a ON curr.account_id = a.id
WHERE a.broker = 'RBC'
AND prev.end_total_market_value > 0
AND ABS((curr.end_total_market_value - prev.end_total_market_value) / prev.end_total_market_value * 100) > 50
UNION
SELECT rm.file_name
FROM dbo.core_rbcmonthly rm
JOIN dbo.core_account a ON rm.account_id = a.id
WHERE a.broker = 'RBC'
AND rm.currency = 'USD'
AND (rm.fx_rate < 0.70 OR rm.fx_rate > 1.60 OR rm.fx_rate = 1.000000)
ORDER BY 1;


-- ============================================================================
-- QUERY 9: Count of Problematic Files by Error Type
-- ============================================================================
-- Summary statistics to understand error distribution
-- ============================================================================

SELECT 
    'balance_mismatch' AS error_type,
    COUNT(DISTINCT rm.file_name) AS file_count
FROM dbo.core_rbcmonthly rm
JOIN dbo.core_account a ON rm.account_id = a.id
LEFT JOIN dbo.core_rbcmonthlybalance rmb ON rm.id = rmb.rbc_monthly_id
WHERE a.broker = 'RBC'
GROUP BY rm.file_name, rm.id, rm.account_id, rm.statement_date, rm.currency, 
         rm.end_total_market_value
HAVING ABS(rm.end_total_market_value - COALESCE(SUM(rmb.market_value), 0)) > 1.00

UNION ALL

SELECT 'begin_end_mismatch', COUNT(DISTINCT curr.file_name)
FROM dbo.core_rbcmonthly curr
JOIN dbo.core_rbcmonthly prev ON 
    curr.account_id = prev.account_id 
    AND curr.currency = prev.currency
    AND prev.statement_date = (
        SELECT MAX(p2.statement_date) 
        FROM dbo.core_rbcmonthly p2 
        WHERE p2.account_id = curr.account_id 
        AND p2.currency = curr.currency 
        AND p2.statement_date < curr.statement_date
    )
JOIN dbo.core_account a ON curr.account_id = a.id
WHERE a.broker = 'RBC'
AND ABS(prev.end_total_market_value - curr.begin_total_market_value) > 1.00

UNION ALL

SELECT 'tx_cashflow_mismatch', COUNT(DISTINCT rm.file_name)
FROM dbo.core_rbcmonthly rm
JOIN dbo.core_account a ON rm.account_id = a.id
LEFT JOIN dbo.core_rbcmonthlytransaction rmt ON rm.id = rmt.rbc_monthly_id
WHERE a.broker = 'RBC'
GROUP BY rm.file_name, rm.id, rm.account_id, rm.statement_date, rm.currency,
         rm.begin_total_market_value, rm.end_total_market_value
HAVING ABS(rm.end_total_market_value - (rm.begin_total_market_value 
        + (COALESCE(SUM(CASE WHEN rmt.credit > 0 THEN rmt.credit ELSE 0 END), 0) 
         - COALESCE(SUM(CASE WHEN rmt.debit > 0 THEN rmt.debit ELSE 0 END), 0)))) > 1.00

UNION ALL

SELECT 'trend_anomaly', COUNT(DISTINCT curr.file_name)
FROM dbo.core_rbcmonthly curr
JOIN dbo.core_rbcmonthly prev ON 
    curr.account_id = prev.account_id 
    AND curr.currency = prev.currency
    AND prev.statement_date = (
        SELECT MAX(p2.statement_date) 
        FROM dbo.core_rbcmonthly p2 
        WHERE p2.account_id = curr.account_id 
        AND p2.currency = curr.currency 
        AND p2.statement_date < curr.statement_date
    )
JOIN dbo.core_account a ON curr.account_id = a.id
WHERE a.broker = 'RBC'
AND prev.end_total_market_value > 0
AND ABS((curr.end_total_market_value - prev.end_total_market_value) / prev.end_total_market_value * 100) > 50

UNION ALL

SELECT 'fx_rate_issue', COUNT(DISTINCT rm.file_name)
FROM dbo.core_rbcmonthly rm
JOIN dbo.core_account a ON rm.account_id = a.id
WHERE a.broker = 'RBC'
AND rm.currency = 'USD'
AND (rm.fx_rate < 0.70 OR rm.fx_rate > 1.60 OR rm.fx_rate = 1.000000)

ORDER BY file_count DESC;


-- ============================================================================
-- QUERY 10: Total Unique Problematic Files Count
-- ============================================================================
-- Quick count of how many files need re-importing
-- ============================================================================

SELECT COUNT(DISTINCT file_name) AS total_problematic_files
FROM (
    SELECT rm.file_name
    FROM dbo.core_rbcmonthly rm
    JOIN dbo.core_account a ON rm.account_id = a.id
    LEFT JOIN dbo.core_rbcmonthlybalance rmb ON rm.id = rmb.rbc_monthly_id
    WHERE a.broker = 'RBC'
    GROUP BY rm.file_name, rm.id, rm.account_id, rm.statement_date, rm.currency, 
             rm.end_total_market_value
    HAVING ABS(rm.end_total_market_value - COALESCE(SUM(rmb.market_value), 0)) > 1.00
    
    UNION
    
    SELECT curr.file_name
    FROM dbo.core_rbcmonthly curr
    JOIN dbo.core_rbcmonthly prev ON 
        curr.account_id = prev.account_id 
        AND curr.currency = prev.currency
        AND prev.statement_date = (
            SELECT MAX(p2.statement_date) 
            FROM dbo.core_rbcmonthly p2 
            WHERE p2.account_id = curr.account_id 
            AND p2.currency = curr.currency 
            AND p2.statement_date < curr.statement_date
        )
    JOIN dbo.core_account a ON curr.account_id = a.id
    WHERE a.broker = 'RBC'
    AND ABS(prev.end_total_market_value - curr.begin_total_market_value) > 1.00
    
    UNION
    
    SELECT rm.file_name
    FROM dbo.core_rbcmonthly rm
    JOIN dbo.core_account a ON rm.account_id = a.id
    LEFT JOIN dbo.core_rbcmonthlytransaction rmt ON rm.id = rmt.rbc_monthly_id
    WHERE a.broker = 'RBC'
    GROUP BY rm.file_name, rm.id, rm.account_id, rm.statement_date, rm.currency,
             rm.begin_total_market_value, rm.end_total_market_value
    HAVING ABS(rm.end_total_market_value - (rm.begin_total_market_value 
            + (COALESCE(SUM(CASE WHEN rmt.credit > 0 THEN rmt.credit ELSE 0 END), 0) 
             - COALESCE(SUM(CASE WHEN rmt.debit > 0 THEN rmt.debit ELSE 0 END), 0)))) > 1.00
    
    UNION
    
    SELECT curr.file_name
    FROM dbo.core_rbcmonthly curr
    JOIN dbo.core_rbcmonthly prev ON 
        curr.account_id = prev.account_id 
        AND curr.currency = prev.currency
        AND prev.statement_date = (
            SELECT MAX(p2.statement_date) 
            FROM dbo.core_rbcmonthly p2 
            WHERE p2.account_id = curr.account_id 
            AND p2.currency = curr.currency 
            AND p2.statement_date < curr.statement_date
        )
    JOIN dbo.core_account a ON curr.account_id = a.id
    WHERE a.broker = 'RBC'
    AND prev.end_total_market_value > 0
    AND ABS((curr.end_total_market_value - prev.end_total_market_value) / prev.end_total_market_value * 100) > 50
    
    UNION
    
    SELECT rm.file_name
    FROM dbo.core_rbcmonthly rm
    JOIN dbo.core_account a ON rm.account_id = a.id
    WHERE a.broker = 'RBC'
    AND rm.currency = 'USD'
    AND (rm.fx_rate < 0.70 OR rm.fx_rate > 1.60 OR rm.fx_rate = 1.000000)
) AS all_problematic;
