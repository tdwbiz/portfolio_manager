-- ============================================================================
-- RBC Balance Reconciliation SQL Queries
-- ============================================================================
-- Purpose: Investigate balance discrepancies in RBC monthly statement imports
-- Database: PortfolioDEV.dbo
-- Date: 2026-06-17
-- ============================================================================


-- ============================================================================
-- QUERY 1: Balance Sum vs end_total_market_value Mismatch
-- ============================================================================
-- Purpose: Find statements where the sum of individual balance market_values
--          does NOT match the end_total_market_value stored in RbcMonthly
-- Root Cause: VL parser may have extracted wrong value (single holding vs total)
-- ============================================================================

SELECT 
    rm.id AS rbc_monthly_id,
    a.account_id,
    rm.statement_date,
    rm.currency,
    rm.end_total_market_value,
    rm.begin_total_market_value,
    COALESCE(SUM(rmb.market_value), 0) AS sum_balances_market_value,
    rm.end_total_market_value - COALESCE(SUM(rmb.market_value), 0) AS discrepancy,
    rm.file_name
FROM dbo.core_rbcmonthly rm
JOIN dbo.core_account a ON rm.account_id = a.id
LEFT JOIN dbo.core_rbcmonthlybalance rmb ON rm.id = rmb.rbc_monthly_id
WHERE a.broker = 'RBC'
GROUP BY rm.id, a.account_id, rm.statement_date, rm.currency, 
         rm.end_total_market_value, rm.begin_total_market_value, rm.file_name
HAVING ABS(rm.end_total_market_value - COALESCE(SUM(rmb.market_value), 0)) > 1.00
ORDER BY ABS(rm.end_total_market_value - COALESCE(SUM(rmb.market_value), 0)) DESC;

--detail 30607:
SELECT * FROM [PortfolioDEV].[dbo].[core_rbcmonthly] where [id] in (30607)
SELECT * FROM [PortfolioDEV].[dbo].[core_rbcmonthlybalance] where [rbc_monthly_id] in (30607) 
--line missing Common Shares $350,974.13 99.87% which is the discrepancy

SELECT * FROM [PortfolioDEV].[dbo].[core_rbcmonthlytransaction] where [rbc_monthly_id] in (30607)
--346.79 debit is wrong. this is  the cash opening balance

--detail 31110
SELECT * FROM [PortfolioDEV].[dbo].[core_rbcmonthly] where [id] in (31110)
SELECT * FROM [PortfolioDEV].[dbo].[core_rbcmonthlybalance] where [rbc_monthly_id] in (31110) 
--book value is taken as opening balance. that is not correct

SELECT * FROM [PortfolioDEV].[dbo].[core_rbcmonthlytransaction] where [rbc_monthly_id] in (31110)
--323.01 debit is wrong. this is credit, and amount is wrong.



-- ============================================================================
-- QUERY 2: Begin/End Balance Reconciliation (Statement-to-Statement)
-- ============================================================================
-- Purpose: Check if statement N's begin_total matches statement N-1's end_total
-- This should reconcile if the VL parser extracted correct totals
-- ============================================================================

SELECT 
    curr.id AS curr_id,
    prev.id AS prev_id,
    curr.account_id,
    a.account_id AS account_display,
    prev.statement_date AS prev_statement_date,
    curr.statement_date AS curr_statement_date,
    prev.end_total_market_value AS prev_end_total,
    curr.begin_total_market_value AS curr_begin_total,
    prev.end_total_market_value - curr.begin_total_market_value AS begin_end_discrepancy,
    prev.file_name AS prev_file,
    curr.file_name AS curr_file
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
ORDER BY ABS(prev.end_total_market_value - curr.begin_total_market_value) DESC;


-- ============================================================================
-- QUERY 3: Transaction Cash Flow Reconciliation
-- ============================================================================
-- Purpose: Verify that:
--          begin_total + SUM(credits) - SUM(debits) = end_total
--          This checks if transactions properly account for balance changes
-- Note: This assumes ALL cash flows (buys, sells, dividends, fees) are captured
-- ============================================================================

SELECT 
    rm.id AS rbc_monthly_id,
    a.account_id,
    rm.statement_date,
    rm.currency,
    rm.begin_total_market_value,
    rm.end_total_market_value,
    COALESCE(SUM(CASE WHEN rmt.credit > 0 THEN rmt.credit ELSE 0 END), 0) AS total_credits,
    COALESCE(SUM(CASE WHEN rmt.debit > 0 THEN rmt.debit ELSE 0 END), 0) AS total_debits,
    COALESCE(SUM(rmt.credit), 0) - COALESCE(SUM(rmt.debit), 0) AS net_cash_flow,
    rm.begin_total_market_value 
        + (COALESCE(SUM(CASE WHEN rmt.credit > 0 THEN rmt.credit ELSE 0 END), 0) 
         - COALESCE(SUM(CASE WHEN rmt.debit > 0 THEN rmt.debit ELSE 0 END), 0)) 
        AS calculated_end_total,
    rm.end_total_market_value 
        - (rm.begin_total_market_value 
         + (COALESCE(SUM(CASE WHEN rmt.credit > 0 THEN rmt.credit ELSE 0 END), 0) 
          - COALESCE(SUM(CASE WHEN rmt.debit > 0 THEN rmt.debit ELSE 0 END), 0))) 
        AS reconciliation_discrepancy,
    COUNT(rmt.id) AS transaction_count,
    rm.file_name
FROM dbo.core_rbcmonthly rm
JOIN dbo.core_account a ON rm.account_id = a.id
LEFT JOIN dbo.core_rbcmonthlytransaction rmt ON rm.id = rmt.rbc_monthly_id
WHERE a.broker = 'RBC'
GROUP BY rm.id, a.account_id, rm.statement_date, rm.currency,
         rm.begin_total_market_value, rm.end_total_market_value, rm.file_name
HAVING ABS(rm.end_total_market_value 
         - (rm.begin_total_market_value 
          + (COALESCE(SUM(CASE WHEN rmt.credit > 0 THEN rmt.credit ELSE 0 END), 0) 
           - COALESCE(SUM(CASE WHEN rmt.debit > 0 THEN rmt.debit ELSE 0 END), 0)))) > 1.00
ORDER BY ABS(rm.end_total_market_value 
         - (rm.begin_total_market_value 
          + (COALESCE(SUM(CASE WHEN rmt.credit > 0 THEN rmt.credit ELSE 0 END), 0) 
           - COALESCE(SUM(CASE WHEN rmt.debit > 0 THEN rmt.debit ELSE 0 END), 0)))) DESC;


-- ============================================================================
-- QUERY 4: Transaction Analysis - BUY vs SELL Debit/Credit Patterns
-- ============================================================================
-- Purpose: Investigate the user's observation that both BUY and SELL go to debit
-- This shows the distribution of debit/credit usage by activity_type
-- ============================================================================

SELECT 
    rmt.activity_type,
    COUNT(*) AS transaction_count,
    SUM(CASE WHEN rmt.debit > 0 THEN 1 ELSE 0 END) AS has_debit,
    SUM(CASE WHEN rmt.credit > 0 THEN 1 ELSE 0 END) AS has_credit,
    SUM(rmt.debit) AS total_debit,
    SUM(rmt.credit) AS total_credit,
    AVG(rmt.debit) AS avg_debit,
    AVG(rmt.credit) AS avg_credit
FROM dbo.core_rbcmonthlytransaction rmt
JOIN dbo.core_rbcmonthly rm ON rmt.rbc_monthly_id = rm.id
JOIN dbo.core_account a ON rm.account_id = a.id
WHERE a.broker = 'RBC'
GROUP BY rmt.activity_type
ORDER BY transaction_count DESC;


-- ============================================================================
-- QUERY 5: Specific Account Investigation (Account ID = 60 / 90000002)
-- ============================================================================
-- Purpose: Detailed reconciliation for the specific account mentioned in audit
-- ============================================================================

-- 5a. Statement balances
SELECT 
    rm.id,
    rm.account_id,
    a.account_id AS account_display,
    rm.statement_date,
    rm.currency,
    rm.begin_total_market_value,
    rm.end_total_market_value,
    rm.fx_rate,
    rm.file_name,
    (SELECT SUM(market_value) FROM dbo.core_rbcmonthlybalance WHERE rbc_monthly_id = rm.id) AS sum_balances,
    (SELECT COUNT(*) FROM dbo.core_rbcmonthlytransaction WHERE rbc_monthly_id = rm.id) AS tx_count
FROM dbo.core_rbcmonthly rm
JOIN dbo.core_account a ON rm.account_id = a.id
WHERE a.account_id = '90000002'  -- Change to specific account
ORDER BY rm.statement_date DESC;


-- 5b. Balance breakdown for specific statements
SELECT 
    rm.id AS rbc_monthly_id,
    rm.statement_date,
    rmb.account_type,
    rmb.opening_balance,
    rmb.closing_balance,
    rmb.market_value,
    rmb.percentage
FROM dbo.core_rbcmonthlybalance rmb
JOIN dbo.core_rbcmonthly rm ON rmb.rbc_monthly_id = rm.id
JOIN dbo.core_account a ON rm.account_id = a.id
WHERE a.account_id = '90000002'  -- Change to specific account
  AND rm.statement_date IN ('2021-05-31', '2021-06-30')  -- Change to specific dates
ORDER BY rm.statement_date, rmb.account_type;


-- 5c. Transaction details for specific statements
SELECT 
    rm.id AS rbc_monthly_id,
    rm.statement_date,
    rmt.date AS transaction_date,
    rmt.activity_type,
    rmt.description,
    rmt.symbol,
    rmt.quantity,
    rmt.price,
    rmt.debit,
    rmt.credit,
    rmt.commission,
    rmt.currency
FROM dbo.core_rbcmonthlytransaction rmt
JOIN dbo.core_rbcmonthly rm ON rmt.rbc_monthly_id = rm.id
JOIN dbo.core_account a ON rm.account_id = a.id
WHERE a.account_id = '90000002'  -- Change to specific account
  AND rm.statement_date IN ('2021-05-31', '2021-06-30')  -- Change to specific dates
ORDER BY rm.statement_date, rmt.date, rmt.id;


-- ============================================================================
-- QUERY 6: Summary Statistics - All RBC Accounts
-- ============================================================================
-- Purpose: Get overview of data quality across all RBC accounts
-- ============================================================================

SELECT 
    a.account_id,
    COUNT(DISTINCT rm.id) AS statement_count,
    SUM(CASE WHEN rm.end_total_market_value > 0 THEN 1 ELSE 0 END) AS statements_with_value,
    MIN(rm.statement_date) AS earliest_statement,
    MAX(rm.statement_date) AS latest_statement,
    SUM(rm.begin_total_market_value) AS sum_begin_totals,
    SUM(rm.end_total_market_value) AS sum_end_totals,
    (SELECT COUNT(*) FROM dbo.core_rbcmonthlybalance rmb 
     JOIN dbo.core_rbcmonthly rm2 ON rmb.rbc_monthly_id = rm2.id 
     WHERE rm2.account_id = a.id) AS balance_record_count,
    (SELECT COUNT(*) FROM dbo.core_rbcmonthlytransaction rmt 
     JOIN dbo.core_rbcmonthly rm2 ON rmt.rbc_monthly_id = rm2.id 
     WHERE rm2.account_id = a.id) AS transaction_count
FROM dbo.core_account a
LEFT JOIN dbo.core_rbcmonthly rm ON a.id = rm.account_id
WHERE a.broker = 'RBC'
GROUP BY a.id, a.account_id
ORDER BY statement_count DESC;


-- ============================================================================
-- QUERY 7: Market Value Trend Analysis - Large Swings
-- ============================================================================
-- Purpose: Identify statements with extreme market value changes (>50%)
-- These may indicate parser errors or legitimate market events
-- ============================================================================

SELECT 
    curr.account_id,
    a.account_id AS account_display,
    curr.currency,
    prev.statement_date AS prev_date,
    curr.statement_date AS curr_date,
    prev.end_total_market_value AS prev_end_total,
    curr.begin_total_market_value AS curr_begin_total,
    curr.end_total_market_value AS curr_end_total,
    CASE 
        WHEN prev.end_total_market_value = 0 THEN NULL
        ELSE ROUND((curr.begin_total_market_value - prev.end_total_market_value) / prev.end_total_market_value * 100, 2)
    END AS begin_end_change_pct,
    CASE 
        WHEN prev.end_total_market_value = 0 THEN NULL
        ELSE ROUND((curr.end_total_market_value - prev.end_total_market_value) / prev.end_total_market_value * 100, 2)
    END AS total_change_pct,
    curr.file_name
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
-- QUERY 8: FX Rate Validation
-- ============================================================================
-- Purpose: Find USD statements with suspicious FX rates (should be 0.70-1.60)
-- ============================================================================

SELECT 
    rm.id,
    a.account_id,
    rm.statement_date,
    rm.currency,
    rm.fx_rate,
    rm.end_total_market_value,
    rm.file_name
FROM dbo.core_rbcmonthly rm
JOIN dbo.core_account a ON rm.account_id = a.id
WHERE a.broker = 'RBC'
  AND rm.currency = 'USD'
  AND (rm.fx_rate < 0.70 OR rm.fx_rate > 1.60 OR rm.fx_rate = 1.000000)
ORDER BY rm.statement_date DESC;


-- ============================================================================
-- QUERY 9: Account Number Mismatch Detection
-- ============================================================================
-- Purpose: Find RbcMonthly records where account_number doesn't match account.account_id
-- This helps identify auto-created accounts or mapping issues
-- ============================================================================

SELECT 
    rm.id AS rbc_monthly_id,
    rm.account_id,
    a.account_id AS account_db_id,
    rm.account_number AS pdf_account_number,
    a.name AS account_name,
    rm.statement_date,
    rm.file_name
FROM dbo.core_rbcmonthly rm
JOIN dbo.core_account a ON rm.account_id = a.id
WHERE a.broker = 'RBC'
  AND rm.account_number != a.account_id
ORDER BY rm.statement_date DESC;


-- ============================================================================
-- QUERY 10: Auto-Created Account Detection
-- ============================================================================
-- Purpose: Find accounts that were auto-created during import
-- ============================================================================

SELECT 
    a.id,
    a.account_id,
    a.name,
    a.broker,
    COUNT(rm.id) AS statement_count,
    MIN(rm.statement_date) AS earliest_statement,
    MAX(rm.statement_date) AS latest_statement
FROM dbo.core_account a
LEFT JOIN dbo.core_rbcmonthly rm ON a.id = rm.account_id
WHERE a.broker = 'RBC'
  AND a.name LIKE 'Auto-created%'
GROUP BY a.id, a.account_id, a.name, a.broker
ORDER BY statement_count DESC;


-- ============================================================================
-- QUERY 11: Balance Opening/Closing vs Statement Begin/End
-- ============================================================================
-- Purpose: Check if balance opening/closing values align with statement totals
-- This helps identify if the parser is extracting the right fields
-- ============================================================================

SELECT 
    rm.id AS rbc_monthly_id,
    a.account_id,
    rm.statement_date,
    rm.currency,
    rm.begin_total_market_value AS statement_begin_total,
    rm.end_total_market_value AS statement_end_total,
    SUM(rmb.opening_balance) AS sum_opening_balances,
    SUM(rmb.closing_balance) AS sum_closing_balances,
    SUM(rmb.market_value) AS sum_market_values,
    rm.begin_total_market_value - SUM(rmb.opening_balance) AS begin_discrepancy,
    rm.end_total_market_value - SUM(rmb.closing_balance) AS end_discrepancy,
    rm.file_name
FROM dbo.core_rbcmonthly rm
JOIN dbo.core_account a ON rm.account_id = a.id
LEFT JOIN dbo.core_rbcmonthlybalance rmb ON rm.id = rmb.rbc_monthly_id
WHERE a.broker = 'RBC'
GROUP BY rm.id, a.account_id, rm.statement_date, rm.currency,
         rm.begin_total_market_value, rm.end_total_market_value, rm.file_name
HAVING ABS(rm.end_total_market_value - SUM(rmb.closing_balance)) > 1.00
   OR ABS(rm.begin_total_market_value - SUM(rmb.opening_balance)) > 1.00
ORDER BY ABS(rm.end_total_market_value - SUM(rmb.closing_balance)) DESC;


-- ============================================================================
-- QUERY 12: Complete Reconciliation for Specific Account/Date Range
-- ============================================================================
-- Purpose: Full reconciliation report for a specific account and date range
-- Usage: Change @account_id and date range as needed
-- ============================================================================

DECLARE @account_id VARCHAR(50) = '90000002';  -- Change to account to investigate
DECLARE @start_date DATE = '2021-01-01';        -- Change to start date
DECLARE @end_date DATE = '2026-12-31';           -- Change to end date

-- Main reconciliation
SELECT 
    rm.id AS rbc_monthly_id,
    a.account_id,
    rm.statement_date,
    rm.currency,
    rm.begin_total_market_value,
    rm.end_total_market_value,
    (SELECT SUM(market_value) FROM dbo.core_rbcmonthlybalance WHERE rbc_monthly_id = rm.id) AS sum_balances_mv,
    (SELECT SUM(closing_balance) FROM dbo.core_rbcmonthlybalance WHERE rbc_monthly_id = rm.id) AS sum_balances_closing,
    (SELECT SUM(opening_balance) FROM dbo.core_rbcmonthlybalance WHERE rbc_monthly_id = rm.id) AS sum_balances_opening,
    (SELECT SUM(CASE WHEN credit > 0 THEN credit ELSE 0 END) FROM dbo.core_rbcmonthlytransaction WHERE rbc_monthly_id = rm.id) AS total_credits,
    (SELECT SUM(CASE WHEN debit > 0 THEN debit ELSE 0 END) FROM dbo.core_rbcmonthlytransaction WHERE rbc_monthly_id = rm.id) AS total_debits,
    (SELECT SUM(CASE WHEN credit > 0 THEN credit ELSE 0 END) - SUM(CASE WHEN debit > 0 THEN debit ELSE 0 END) 
     FROM dbo.core_rbcmonthlytransaction WHERE rbc_monthly_id = rm.id) AS net_cash_flow,
    rm.begin_total_market_value 
        + ((SELECT SUM(CASE WHEN credit > 0 THEN credit ELSE 0 END) FROM dbo.core_rbcmonthlytransaction WHERE rbc_monthly_id = rm.id) 
         - (SELECT SUM(CASE WHEN debit > 0 THEN debit ELSE 0 END) FROM dbo.core_rbcmonthlytransaction WHERE rbc_monthly_id = rm.id))
        AS calculated_end_total,
    rm.end_total_market_value 
        - (rm.begin_total_market_value 
         + ((SELECT SUM(CASE WHEN credit > 0 THEN credit ELSE 0 END) FROM dbo.core_rbcmonthlytransaction WHERE rbc_monthly_id = rm.id) 
          - (SELECT SUM(CASE WHEN debit > 0 THEN debit ELSE 0 END) FROM dbo.core_rbcmonthlytransaction WHERE rbc_monthly_id = rm.id)))
        AS reconciliation_discrepancy,
    rm.fx_rate,
    rm.file_name
FROM dbo.core_rbcmonthly rm
JOIN dbo.core_account a ON rm.account_id = a.id
WHERE a.broker = 'RBC'
  AND a.account_id = @account_id
  AND rm.statement_date BETWEEN @start_date AND @end_date
ORDER BY rm.statement_date DESC;
