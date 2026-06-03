-- MSSQL Script to create portfolio views and functions
-- Run this via SSMS on neptune or sqlcmd
USE PortfolioDEV

-- View 1: vw_current_portfolio
CREATE OR ALTER VIEW vw_current_portfolio AS
SELECT
    ph.id,
    ph.owner,
    ph.account_id,
    ca.account_id        AS account_code,
    ca.name              AS account_name,
    ca.category          AS account_category,
    ca.broker,
    ca.exclude_from_risk,
    ph.symbol,
    ph.quantity,
    ph.cost_basis_per_unit,
    ph.total_cost_basis,
    ph.currency,
    ph.date_from
FROM dbo.core_position_history ph
INNER JOIN dbo.core_account ca ON ca.id = ph.account_id
WHERE ph.date_to IS NULL
  AND ph.quantity > 0.0001
  AND ca.exclude_from_risk = 0;

-- Function 1: vw_portfolio_as_of
CREATE OR ALTER FUNCTION vw_portfolio_as_of (@as_of_date DATE)
RETURNS TABLE AS RETURN
(
    SELECT
        ph.id,
        ph.owner,
        ph.account_id,
        ca.account_id        AS account_code,
        ca.name              AS account_name,
        ca.category          AS account_category,
        ca.exclude_from_risk,
        ph.symbol,
        ph.quantity,
        ph.cost_basis_per_unit,
        ph.total_cost_basis,
        ph.currency,
        ph.date_from,
        ph.date_to
    FROM dbo.core_position_history ph
    INNER JOIN dbo.core_account ca ON ca.id = ph.account_id
    WHERE ph.date_from <= @as_of_date
      AND (ph.date_to IS NULL OR ph.date_to > @as_of_date)
      AND ph.quantity > 0.0001
);

-- Function 2: vw_portfolio_diff
CREATE OR ALTER FUNCTION vw_portfolio_diff (@date1 DATE, @date2 DATE)
RETURNS TABLE AS RETURN
(
    -- Positions at date2 not in date1 (new positions)
    SELECT 'd2_only' AS change_type, d2.owner, d2.account_id, d2.symbol,
           0 AS qty_date1, d2.quantity AS qty_date2,
           d2.quantity AS qty_change
    FROM vw_portfolio_as_of(@date2) d2
    WHERE NOT EXISTS (
        SELECT 1 FROM vw_portfolio_as_of(@date1) d1
        WHERE d1.account_id = d2.account_id AND d1.symbol = d2.symbol
    )
    UNION ALL
    -- Positions at date1 not in date2 (closed positions)
    SELECT 'd1_only', d1.owner, d1.account_id, d1.symbol,
           d1.quantity, 0, -d1.quantity
    FROM vw_portfolio_as_of(@date1) d1
    WHERE NOT EXISTS (
        SELECT 1 FROM vw_portfolio_as_of(@date2) d2
        WHERE d2.account_id = d1.account_id AND d2.symbol = d1.symbol
    )
    UNION ALL
    -- Positions in both with changed quantity
    SELECT 'changed', d1.owner, d1.account_id, d1.symbol,
           d1.quantity, d2.quantity, d2.quantity - d1.quantity
    FROM vw_portfolio_as_of(@date1) d1
    INNER JOIN vw_portfolio_as_of(@date2) d2
        ON d1.account_id = d2.account_id AND d1.symbol = d2.symbol
    WHERE ABS(d2.quantity - d1.quantity) > 0.0001
);
