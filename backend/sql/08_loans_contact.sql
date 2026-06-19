-- ════════════════════════════════════════════════════════════════════════
--  Reporte: loans_contact  → TODOS los préstamos vigentes con contacto del socio
--  y días respecto del vencimiento. Alimenta los envíos automáticos
--  (resumen interno + recordatorios a socios).
--  Variable .env: REPORT_LOANS_CONTACT_ID
--  Parámetros: ninguno.
--  Columnas (EN ESTE ORDEN):
--    cardnumber, surname, firstname, email, phone, barcode, title,
--    issuedate, date_due, dias_atraso
--  dias_atraso = DATEDIFF(CURDATE(), date_due):
--    > 0  → vencido hace N días
--    = 0  → vence hoy
--    < 0  → faltan N días para vencer (por vencer)
-- ════════════════════════════════════════════════════════════════════════
SELECT
    br.cardnumber,
    br.surname,
    br.firstname,
    br.email,
    br.phone,
    i.barcode,
    b.title,
    iss.issuedate,
    iss.date_due,
    DATEDIFF(CURDATE(), iss.date_due) AS dias_atraso
FROM issues iss
JOIN borrowers br ON br.borrowernumber = iss.borrowernumber
JOIN items     i  ON i.itemnumber      = iss.itemnumber
JOIN biblio    b  ON b.biblionumber    = i.biblionumber
ORDER BY iss.date_due;
