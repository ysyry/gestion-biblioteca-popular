-- ════════════════════════════════════════════════════════════════════════
--  Reporte: member_history  → historial de préstamos devueltos (por carnet)
--  Variable .env: REPORT_MEMBER_HISTORY_ID
--  Parámetros (1): número de carnet.
--  Columnas: barcode, title, author, issuedate, returndate
-- ════════════════════════════════════════════════════════════════════════
SELECT
    i.barcode,
    bi.title,
    bi.author,
    o.issuedate,
    o.returndate
FROM old_issues o
LEFT JOIN items  i  ON i.itemnumber   = o.itemnumber
LEFT JOIN biblio bi ON bi.biblionumber = i.biblionumber
JOIN borrowers   b  ON b.borrowernumber = o.borrowernumber
WHERE b.cardnumber = <<Numero de carnet>>
ORDER BY o.returndate DESC
LIMIT 200
