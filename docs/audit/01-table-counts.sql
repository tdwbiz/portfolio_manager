/* ============================================================================
 * 01-table-counts.sql — Data-model audit, part 2
 *
 * Run on mercury (SQL Server, production DB) as read-only.
 * Returns ONLY: table name, row count, and MIN/MAX of each table's main date
 * column. No row contents, no personal data, no amounts.
 *
 * Tables with no date column return NULL for min/max.
 * core_annual_report has an integer year instead of a date — see section 3.
 * ============================================================================ */

/* ---- 1. One result set: every model table ------------------------------ */
SELECT * FROM (
    /* ---- core app ----------------------------------------------------- */
    SELECT 1 AS sort_order, 'core' AS app, 'core_account'                  AS table_name, COUNT(*) AS row_count, MIN(a.[starting_balance_date]) AS min_date, MAX(a.[starting_balance_date]) AS max_date FROM [core_account] a
    UNION ALL SELECT 2, 'core', 'core_accountcurrency',       COUNT(*), MIN([starting_balance_date]), MAX([starting_balance_date]) FROM [core_accountcurrency]
    UNION ALL SELECT 3, 'core', 'core_transaction',           COUNT(*), MIN([date]),                  MAX([date])                  FROM [core_transaction]
    UNION ALL SELECT 4, 'core', 'core_brokerstatement',       COUNT(*), MIN([file_date]),             MAX([file_date])             FROM [core_brokerstatement]
    UNION ALL SELECT 5, 'core', 'core_position_history',      COUNT(*), MIN([date_from]),             MAX([date_from])             FROM [core_position_history]
    UNION ALL SELECT 6, 'core', 'core_monthly_statement',     COUNT(*), MIN([statement_month]),       MAX([statement_month])       FROM [core_monthly_statement]
    UNION ALL SELECT 7, 'core', 'core_ledger_account',        COUNT(*), MIN([created_at]),            MAX([created_at])            FROM [core_ledger_account]
    UNION ALL SELECT 8, 'core', 'core_ledger_entry',          COUNT(*), MIN([entry_date]),            MAX([entry_date])            FROM [core_ledger_entry]
    UNION ALL SELECT 9, 'core', 'core_transactiontype',       COUNT(*), CAST(NULL AS DATE),          CAST(NULL AS DATE)           FROM [core_transactiontype]
    UNION ALL SELECT 10, 'core', 'core_rbcmonthly',           COUNT(*), MIN([statement_date]),        MAX([statement_date])        FROM [core_rbcmonthly]
    UNION ALL SELECT 11, 'core', 'core_rbcmonthlybalance',    COUNT(*), CAST(NULL AS DATE),          CAST(NULL AS DATE)           FROM [core_rbcmonthlybalance]
    UNION ALL SELECT 12, 'core', 'core_rbcmonthlytransaction',COUNT(*), MIN([date]),                  MAX([date])                  FROM [core_rbcmonthlytransaction]
    UNION ALL SELECT 13, 'core', 'core_rbcmonthlyholding',    COUNT(*), CAST(NULL AS DATE),          CAST(NULL AS DATE)           FROM [core_rbcmonthlyholding]
    UNION ALL SELECT 14, 'core', 'core_rbcannual',            COUNT(*), MIN([report_period_end]),     MAX([report_period_end])     FROM [core_rbcannual]
    UNION ALL SELECT 15, 'core', 'core_fileimportlog',        COUNT(*), MIN([imported_at]),           MAX([imported_at])           FROM [core_fileimportlog]
    UNION ALL SELECT 16, 'core', 'core_brandingconfig',       COUNT(*), CAST(NULL AS DATE),          CAST(NULL AS DATE)           FROM [core_brandingconfig]
    UNION ALL SELECT 17, 'core', 'core_historicalprice',      COUNT(*), MIN([date]),                  MAX([date])                  FROM [core_historicalprice]
    UNION ALL SELECT 18, 'core', 'core_fixed_price_config',   COUNT(*), MIN([created_at]),            MAX([created_at])            FROM [core_fixed_price_config]
    UNION ALL SELECT 19, 'core', 'core_reconciliation',       COUNT(*), MIN([period_start]),          MAX([period_start])          FROM [core_reconciliation]
    UNION ALL SELECT 20, 'core', 'core_monthly_ledger_balance',COUNT(*),MIN([statement_month]),       MAX([statement_month])       FROM [core_monthly_ledger_balance]
    UNION ALL SELECT 21, 'core', 'core_ledger_month_closure', COUNT(*), MIN([statement_month]),       MAX([statement_month])       FROM [core_ledger_month_closure]
    UNION ALL SELECT 22, 'core', 'core_dividendschedule',     COUNT(*), MIN([pay_date]),              MAX([pay_date])              FROM [core_dividendschedule]
    /* ---- risk app ------------------------------------------------------ */
    UNION ALL SELECT 23, 'risk', 'rm_security_universe',          COUNT(*), MIN([created_at]),       MAX([created_at])            FROM [rm_security_universe]
    UNION ALL SELECT 24, 'risk', 'rm_market_price_history',       COUNT(*), MIN([date]),             MAX([date])                  FROM [rm_market_price_history]
    UNION ALL SELECT 25, 'risk', 'rm_security_risk_metrics',      COUNT(*), MIN([calc_date]),        MAX([calc_date])             FROM [rm_security_risk_metrics]
    UNION ALL SELECT 26, 'risk', 'rm_regime_state',               COUNT(*), MIN([calc_date]),        MAX([calc_date])             FROM [rm_regime_state]
    UNION ALL SELECT 27, 'risk', 'rm_market_condition',           COUNT(*), MIN([calc_date]),        MAX([calc_date])             FROM [rm_market_condition]
    UNION ALL SELECT 28, 'risk', 'rm_portfolio_risk_metrics',     COUNT(*), MIN([calc_date]),        MAX([calc_date])             FROM [rm_portfolio_risk_metrics]
    UNION ALL SELECT 29, 'risk', 'rm_urgency_score',              COUNT(*), MIN([calc_date]),        MAX([calc_date])             FROM [rm_urgency_score]
    UNION ALL SELECT 30, 'risk', 'rm_strategy_recommendation',    COUNT(*), MIN([calc_date]),        MAX([calc_date])             FROM [rm_strategy_recommendation]
    UNION ALL SELECT 31, 'risk', 'rm_strategy_template',          COUNT(*), MIN([created_at]),       MAX([created_at])            FROM [rm_strategy_template]
    UNION ALL SELECT 32, 'risk', 'rm_instrument_type',            COUNT(*), MIN([created_at]),       MAX([created_at])            FROM [rm_instrument_type]
    UNION ALL SELECT 33, 'risk', 'rm_simulation_run',             COUNT(*), MIN([calc_date]),        MAX([calc_date])             FROM [rm_simulation_run]
    UNION ALL SELECT 34, 'risk', 'rm_simulation_result',          COUNT(*), MIN([calc_timestamp]),   MAX([calc_timestamp])        FROM [rm_simulation_result]
    UNION ALL SELECT 35, 'risk', 'rm_backtest_run',               COUNT(*), MIN([start_date]),       MAX([start_date])            FROM [rm_backtest_run]
    UNION ALL SELECT 36, 'risk', 'rm_backtest_result',            COUNT(*), MIN([calc_timestamp]),   MAX([calc_timestamp])        FROM [rm_backtest_result]
    UNION ALL SELECT 37, 'risk', 'rm_volatility_summary',         COUNT(*), MIN([calc_date]),        MAX([calc_date])             FROM [rm_volatility_summary]
    UNION ALL SELECT 38, 'risk', 'rm_fx_rates',                   COUNT(*), MIN([date]),             MAX([date])                  FROM [rm_fx_rates]
    UNION ALL SELECT 39, 'risk', 'rm_economic_indicators',        COUNT(*), MIN([date]),             MAX([date])                  FROM [rm_economic_indicators]
    UNION ALL SELECT 40, 'risk', 'rm_correlation_matrix',         COUNT(*), MIN([calc_date]),        MAX([calc_date])             FROM [rm_correlation_matrix]
    UNION ALL SELECT 41, 'risk', 'rm_regime_sensitivity',         COUNT(*), CAST(NULL AS DATE),     CAST(NULL AS DATE)           FROM [rm_regime_sensitivity]
    UNION ALL SELECT 42, 'risk', 'rm_security_driver',            COUNT(*), MIN([updated_at]),       MAX([updated_at])            FROM [rm_security_driver]
    UNION ALL SELECT 43, 'risk', 'rm_scoring_config',             COUNT(*), MIN([updated_at]),       MAX([updated_at])            FROM [rm_scoring_config]
    UNION ALL SELECT 44, 'risk', 'rm_security_index_exposure',    COUNT(*), CAST(NULL AS DATE),     CAST(NULL AS DATE)           FROM [rm_security_index_exposure]
    UNION ALL SELECT 45, 'risk', 'rm_owner_config',               COUNT(*), MIN([updated_at]),       MAX([updated_at])            FROM [rm_owner_config]
    UNION ALL SELECT 46, 'risk', 'rm_strategy_instrument_link',   COUNT(*), CAST(NULL AS DATE),     CAST(NULL AS DATE)           FROM [rm_strategy_instrument_link]
    UNION ALL SELECT 47, 'risk', 'rm_alternative_portfolios',     COUNT(*), MIN([created_at]),       MAX([created_at])            FROM [rm_alternative_portfolios]
    UNION ALL SELECT 48, 'risk', 'rm_alternative_holdings',       COUNT(*), CAST(NULL AS DATE),     CAST(NULL AS DATE)           FROM [rm_alternative_holdings]
    UNION ALL SELECT 49, 'risk', 'rm_data_quality_log',           COUNT(*), MIN([logged_at]),        MAX([logged_at])             FROM [rm_data_quality_log]
    UNION ALL SELECT 50, 'risk', 'rm_historical_scenario',        COUNT(*), MIN([start_date]),       MAX([end_date])              FROM [rm_historical_scenario]
    UNION ALL SELECT 51, 'risk', 'rm_custom_stress_scenario',     COUNT(*), MIN([created_at]),       MAX([created_at])            FROM [rm_custom_stress_scenario]
    UNION ALL SELECT 52, 'risk', 'rm_tax_decision_acknowledgement',COUNT(*),MIN([acknowledged_at]),  MAX([acknowledged_at])       FROM [rm_tax_decision_acknowledgement]
    UNION ALL SELECT 53, 'risk', 'rm_alert_config',               COUNT(*), MIN([created_at]),       MAX([created_at])            FROM [rm_alert_config]
    UNION ALL SELECT 54, 'risk', 'rm_alert_history',              COUNT(*), MIN([triggered_at]),     MAX([triggered_at])          FROM [rm_alert_history]
    UNION ALL SELECT 55, 'risk', 'rm_audit_entry',                COUNT(*), MIN([calc_date]),        MAX([calc_date])             FROM [rm_audit_entry]
    UNION ALL SELECT 56, 'risk', 'rm_audit_action_log',           COUNT(*), MIN([created_at]),       MAX([created_at])            FROM [rm_audit_action_log]
    UNION ALL SELECT 57, 'risk', 'rm_audit_retention_log',        COUNT(*), MIN([check_date]),       MAX([check_date])            FROM [rm_audit_retention_log]
    UNION ALL SELECT 58, 'risk', 'rm_audit_score_snapshot',       COUNT(*), MIN([calc_date]),        MAX([calc_date])             FROM [rm_audit_score_snapshot]
    UNION ALL SELECT 59, 'risk', 'rm_audit_recommendation_log',   COUNT(*), MIN([calc_date]),        MAX([calc_date])             FROM [rm_audit_recommendation_log]
    UNION ALL SELECT 60, 'risk', 'rm_audit_simulation_run',       COUNT(*), MIN([calc_date]),        MAX([calc_date])             FROM [rm_audit_simulation_run]
    UNION ALL SELECT 61, 'risk', 'rm_audit_regime_log',           COUNT(*), MIN([calc_date]),        MAX([calc_date])             FROM [rm_audit_regime_log]
) AS t
ORDER BY t.sort_order;

/* ---- 2. core_annual_report: year column is an integer ------------------ */
SELECT 'core_annual_report' AS table_name,
       COUNT(*)             AS row_count,
       MIN([report_year])   AS min_report_year,
       MAX([report_year])   AS max_report_year
FROM [core_annual_report];
