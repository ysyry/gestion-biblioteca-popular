-- ════════════════════════════════════════════════════════════════════════
--  Reporte: member_search  → busca socios por apellido, nombre o carnet
--  Variable .env: REPORT_MEMBER_SEARCH_ID
--  Parámetros (1): término de búsqueda. La app lo manda como %texto%.
--  Columnas (EN ESTE ORDEN, no cambiar sin actualizar reports.py):
--    cardnumber, surname, firstname, email, phone, category, dateexpiry
-- ════════════════════════════════════════════════════════════════════════
SELECT
    cardnumber,
    surname,
    firstname,
    email,
    phone,
    categorycode AS category,
    dateexpiry
FROM borrowers
WHERE CONCAT_WS(' ', surname, firstname, cardnumber) LIKE <<Buscar socio>>
ORDER BY surname, firstname
LIMIT 100;
