-- ════════════════════════════════════════════════════════════════════════
--  Reporte: member_loans  → préstamos vigentes de UN socio (por carnet)
--  Variable .env: REPORT_MEMBER_LOANS_ID
--  Parámetros (1): número de carnet (cardnumber).
--  Columnas (EN ESTE ORDEN): barcode, title, author, issuedate, date_due
-- ════════════════════════════════════════════════════════════════════════
SELECT
    i.barcode,
    b.title,
    b.author,
    iss.issuedate,
    iss.date_due
FROM issues iss
JOIN items     i  ON i.itemnumber   = iss.itemnumber
JOIN biblio    b  ON b.biblionumber = i.biblionumber
JOIN borrowers br ON br.borrowernumber = iss.borrowernumber
WHERE br.cardnumber = <<Numero de carnet>>
ORDER BY iss.date_due;
