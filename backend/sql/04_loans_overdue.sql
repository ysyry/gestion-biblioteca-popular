-- ════════════════════════════════════════════════════════════════════════
--  Reporte: loans_overdue  → préstamos vencidos, con días de atraso y contacto
--  Variable .env: REPORT_LOANS_OVERDUE_ID
--  Parámetros: ninguno.
--  Columnas (EN ESTE ORDEN):
--    cardnumber, surname, firstname, phone, email, barcode, title, date_due, dias_atraso
-- ════════════════════════════════════════════════════════════════════════
SELECT
    br.cardnumber,
    br.surname,
    br.firstname,
    br.phone,
    br.email,
    i.barcode,
    b.title,
    iss.date_due,
    DATEDIFF(CURDATE(), iss.date_due) AS dias_atraso
FROM issues iss
JOIN borrowers br ON br.borrowernumber = iss.borrowernumber
JOIN items     i  ON i.itemnumber      = iss.itemnumber
JOIN biblio    b  ON b.biblionumber    = i.biblionumber
WHERE iss.date_due < CURDATE()
ORDER BY dias_atraso DESC;
