-- Fix bad RbcMonthlyTransaction records
-- Run this in MSSQL after re-importing or as a one-time cleanup

-- 0. Remove Opening Balance lines that were misclassified as transactions
DELETE FROM core_rbcmonthlytransaction
WHERE description LIKE '%Opening Balance%'
   OR description LIKE '%opening balance%';

-- 1. Fix SOLD: clear debit (likely Opening Balance leak), ensure credit has proceeds
UPDATE t
SET t.debit = 0
FROM core_rbcmonthlytransaction t
WHERE t.activity_type = 'SOLD'
  AND t.debit > 0;

UPDATE t
SET t.credit = t.debit,
    t.debit = 0
FROM core_rbcmonthlytransaction t
WHERE t.activity_type = 'SOLD'
  AND t.debit > 0
  AND t.credit = 0;

-- 2. Fix TFR OUT: quantity should be 0, cash out goes in debit
UPDATE t
SET t.quantity = 0,
    t.price = 0,
    t.debit = CASE WHEN t.credit > 0 AND t.debit = 0 THEN t.credit ELSE t.debit END,
    t.credit = CASE WHEN t.credit > 0 AND t.debit = 0 THEN 0 ELSE t.credit END
FROM core_rbcmonthlytransaction t
WHERE t.activity_type = 'TFR OUT'
  AND (t.quantity > 0 OR (t.credit > 0 AND t.debit = 0));

-- 2. Fix cash-only types: quantity should be 0
UPDATE t
SET t.quantity = 0,
    t.price = 0
FROM core_rbcmonthlytransaction t
WHERE t.activity_type IN ('TRANSFER', 'WIRE TFR', 'CONTRIB', 'DEPOSIT', 'WITHDRAWAL', 'FEE')
  AND t.quantity > 0;

-- 3. Fix DIV REIN: description contains REINV@ but was misclassified
UPDATE t
SET t.activity_type = 'DIV REIN',
    t.debit = 0,
    t.credit = 0
FROM core_rbcmonthlytransaction t
WHERE t.description LIKE '%REINV@%'
  AND t.activity_type != 'DIV REIN';

-- 4. Fix DIST: per-share rate should be in price, quantity should be 0
UPDATE t
SET t.price = t.quantity,
    t.quantity = 0,
    t.credit = CASE WHEN t.debit > 0 AND t.credit = 0 THEN t.debit ELSE t.credit END,
    t.debit = CASE WHEN t.debit > 0 AND t.credit = 0 THEN 0 ELSE t.debit END
FROM core_rbcmonthlytransaction t
WHERE t.activity_type = 'DIST'
  AND t.quantity > 0
  AND t.quantity < 5;

-- Verify fixes
SELECT 'Opening Balance lines remaining' AS issue, COUNT(*) AS cnt
FROM core_rbcmonthlytransaction
WHERE description LIKE '%Opening Balance%'
   OR description LIKE '%opening balance%'

UNION ALL

SELECT 'TFR OUT with qty > 0', COUNT(*)
FROM core_rbcmonthlytransaction
WHERE activity_type = 'TFR OUT' AND quantity > 0

UNION ALL

SELECT 'SOLD with debit > 0', COUNT(*)
FROM core_rbcmonthlytransaction
WHERE activity_type = 'SOLD' AND debit > 0

UNION ALL

SELECT 'Cash-only with qty > 0', COUNT(*)
FROM core_rbcmonthlytransaction
WHERE activity_type IN ('TRANSFER', 'WIRE TFR', 'CONTRIB', 'DEPOSIT', 'WITHDRAWAL', 'FEE')
  AND quantity > 0

UNION ALL

SELECT 'DIST with qty > 0 and < 5', COUNT(*)
FROM core_rbcmonthlytransaction
WHERE activity_type = 'DIST' AND quantity > 0 AND quantity < 5

UNION ALL

SELECT 'REINV not classified as DIV REIN', COUNT(*)
FROM core_rbcmonthlytransaction
WHERE description LIKE '%REINV@%'
  AND activity_type != 'DIV REIN';
