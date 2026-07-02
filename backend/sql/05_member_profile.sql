-- ════════════════════════════════════════════════════════════════════════
--  Reporte: member_profile  → datos del socio + deuda total (por carnet)
--  Variable .env: REPORT_MEMBER_PROFILE_ID
--  Parámetros (1): número de carnet.
--  Columnas: cardnumber, surname, firstname, email, phone, mobile, address,
--            city, category, dateenrolled, dateexpiry, debarred, deuda
--  Nota: deuda = SUMA de amountoutstanding (positivo = debe; negativo = a favor).
-- ════════════════════════════════════════════════════════════════════════
SELECT
    b.cardnumber,
    b.surname,
    b.firstname,
    COALESCE(NULLIF(TRIM(b.email),''), NULLIF(TRIM(b.emailpro),''), NULLIF(TRIM(b.B_email),'')) AS email,
    b.phone,
    b.mobile,
    b.address,
    b.city,
    b.categorycode AS category,
    b.dateenrolled,
    b.dateexpiry,
    b.debarred,
    ROUND(COALESCE((SELECT SUM(a.amountoutstanding)
                    FROM accountlines a
                    WHERE a.borrowernumber = b.borrowernumber), 0), 2) AS deuda
FROM borrowers b
WHERE b.cardnumber = <<Numero de carnet>>
