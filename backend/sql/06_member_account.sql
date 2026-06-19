-- ════════════════════════════════════════════════════════════════════════
--  Reporte: member_account  → estado de cuenta del socio (multas, cuotas, pagos)
--  Variable .env: REPORT_MEMBER_ACCOUNT_ID
--  Parámetros (1): número de carnet.
--  Columnas: date, accounttype, description, amount, amountoutstanding
--  Tipos (accounttype): A/Cob = cuota societaria, Pay = pago, F/FU = multa,
--                       L = extravío, Rent = alquiler, C/CR = crédito, W = quita.
-- ════════════════════════════════════════════════════════════════════════
SELECT
    a.date,
    a.accounttype,
    a.description,
    ROUND(a.amount, 2)            AS amount,
    ROUND(a.amountoutstanding, 2) AS amountoutstanding
FROM accountlines a
JOIN borrowers b ON b.borrowernumber = a.borrowernumber
WHERE b.cardnumber = <<Numero de carnet>>
ORDER BY a.date DESC, a.accountlines_id DESC
