-- ════════════════════════════════════════════════════════════════════════
--  Reporte: loans_active  → todos los préstamos vigentes (lo prestado ahora)
--  Variable .env: REPORT_LOANS_ACTIVE_ID
--  Parámetros: ninguno.
--  Columnas (EN ESTE ORDEN):
--    cardnumber, surname, firstname, barcode, title, issuedate, date_due
-- ════════════════════════════════════════════════════════════════════════
SELECT
    br.cardnumber,
    br.surname,
    br.firstname,
    i.barcode,
    b.title,
    iss.issuedate,
    iss.date_due
FROM issues iss
JOIN borrowers br ON br.borrowernumber = iss.borrowernumber
JOIN items     i  ON i.itemnumber      = iss.itemnumber
JOIN biblio    b  ON b.biblionumber    = i.biblionumber
ORDER BY iss.date_due;
