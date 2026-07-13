-- ============================================================================
-- DATA COMPLETENESS DASHBOARD
-- ============================================================================
-- Shows coverage by account and year for RBC and CSV data
-- ============================================================================

-- 1. COVERAGE MATRIX: ACCOUNTS vs YEARS
-- Shows which years have data in RBC and CSV for each account
SELECT 
    a.account_id,
    a.name AS account_name,
    y.year,
    CASE WHEN rbc_count > 0 THEN 'YES' ELSE 'NO' END AS rbc_coverage,
    CASE WHEN csv_count > 0 THEN 'YES' ELSE 'NO' END AS csv_coverage,
    rbc_count,
    csv_count,
    CASE 
        WHEN rbc_count > 0 AND csv_count = 0 THEN 'RBC ONLY'
        WHEN rbc_count = 0 AND csv_count > 0 THEN 'CSV ONLY'
        WHEN rbc_count > 0 AND csv_count > 0 THEN 'BOTH'
        ELSE 'NEITHER'
    END AS coverage_status
FROM (
    SELECT DISTINCT account_id FROM core_rbcmonthly
) accounts
CROSS JOIN (
    SELECT DISTINCT YEAR(statement_date) AS year 
    FROM core_rbcmonthly 
    WHERE statement_date >= '2018-01-01'
    UNION
    SELECT DISTINCT YEAR(statement_month) 
    FROM core_monthlystatement 
    WHERE statement_month >= '2018-01-01'
) years
LEFT JOIN core_account a ON accounts.account_id = a.id
LEFT JOIN (
    SELECT account_id, YEAR(statement_date) AS year, COUNT(*) AS rbc_count
    FROM core_rbcmonthly
    WHERE statement_date >= '2018-01-01'
    GROUP BY account_id, YEAR(statement_date)
) rbc ON accounts.account_id = rbc.account_id AND years.year = rbc.year
LEFT JOIN (
    SELECT account_id, YEAR(statement_month) AS year, COUNT(*) AS csv_count
    FROM core_monthlystatement
    WHERE statement_month >= '2018-01-01'
    GROUP BY account_id, YEAR(statement_month)
) csv ON accounts.account_id = csv.account_id AND years.year = csv.year
ORDER BY a.account_id, y.year;

-- 2. OVERALL SUMMARY STATISTICS
SELECT 
    COUNT(DISTINCT CASE WHEN rbc.statement_date IS NOT NULL THEN rbc.account_id END) AS accounts_with_rbc,
    COUNT(DISTINCT CASE WHEN csv.statement_month IS NOT NULL THEN csv.account_id END) AS accounts_with_csv,
    COUNT(DISTINCT CASE WHEN rbc.statement_date IS NOT NULL AND csv.statement_month IS NOT NULL 
                AND YEAR(rbc.statement_date) = YEAR(csv.statement_month) 
                THEN rbc.account_id END) AS accounts_with_both,
    MIN(COALESCE(rbc.statement_date, csv.statement_month)) AS earliest_data,
    MAX(COALESCE(rbc.statement_date, csv.statement_month)) AS latest_data,
    SUM(CASE WHEN rbc.statement_date IS NOT NULL THEN 1 ELSE 0 END) AS total_rbc_stmts,
    SUM(CASE WHEN csv.statement_month IS NOT NULL THEN 1 ELSE 0 END) AS total_csv_stmts
FROM core_rbcmonthly rbc
FULL OUTER JOIN core_monthlystatement csv 
    ON rbc.account_id = csv.account_id 
    AND rbc.statement_date = csv.statement_month
    AND rbc.currency = csv.currency
WHERE COALESCE(rbc.statement_date, csv.statement_month) >= '2020-01-01';

-- 3. ACCOUNT-LEVEL COVERAGE DETAIL
-- Shows percentage of months covered for each account
SELECT 
    a.account_id,
    a.name AS account_name,
    COUNT(DISTINCT CASE WHEN rbc.id IS NOT NULL THEN rbc.statement_date END) AS rbc_months,
    COUNT(DISTINCT CASE WHEN csv.id IS NOT NULL THEN csv.statement_month END) AS csv_months,
    COUNT(DISTINCT CASE WHEN rbc.id IS NOT NULL THEN rbc.statement_date END) * 100.0 / 
        NULLIF(COUNT(DISTINCT rbc.statement_date), 0) AS rbc_coverage_pct,
    CASE 
        WHEN COUNT(DISTINCT CASE WHEN rbc.id IS NOT NULL THEN rbc.statement_date END) = 
             COUNT(DISTINCT CASE WHEN csv.id IS NOT NULL THEN csv.statement_month END)
        THEN 'FULL MATCH'
        ELSE 'PARTIAL'
    END AS match_status
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
GROUP BY a.account_id, a.name
ORDER BY a.account_id;

-- 4. MISSING DATA ALERTS
-- Shows accounts with significant coverage gaps
SELECT 
    a.account_id,
    a.name AS account_name,
    MIN(rbc.statement_date) AS rbc_start,
    MAX(rbc.statement_date) AS rbc_end,
    MIN(csv.statement_month) AS csv_start,
    MAX(csv.statement_month) AS csv_end,
    DATEDIFF(MONTH, 
        MIN(COALESCE(rbc.statement_date, csv.statement_month)),
        MAX(COALESCE(rbc.statement_date, csv.statement_month))
    ) AS total_months,
    COUNT(DISTINCT rbc.statement_date) AS rbc_months,
    COUNT(DISTINCT csv.statement_month) AS csv_months,
    COUNT(DISTINCT rbc.statement_date) + COUNT(DISTINCT csv.statement_month) - 
        COUNT(DISTINCT CASE WHEN rbc.statement_date = csv.statement_month AND rbc.currency = csv.currency 
                          THEN rbc.statement_date END) AS gaps
FROM core_account a
LEFT JOIN core_rbcmonthly rbc 
    ON a.id = rbc.account_id 
    AND rbc.statement_date >= '2018-01-01'
LEFT JOIN core_monthlystatement csv 
    ON a.id = csv.account_id 
    AND csv.statement_month >= '2018-01-01'
WHERE a.account_id IN (
    SELECT DISTINCT account_id FROM core_rbcmonthly
)
GROUP BY a.account_id, a.name
HAVING COUNT(DISTINCT rbc.statement_date) + COUNT(DISTINCT csv.statement_month) - 
       COUNT(DISTINCT CASE WHEN rbc.statement_date = csv.statement_month AND rbc.currency = csv.currency 
                         THEN rbc.statement_date END) > 10
ORDER BY gaps DESC;
