# Reportes SQL de Koha

Estos 4 reportes son la **fuente de datos real** del POC. Se crean **una sola vez**
en la intranet de Koha y la app los invoca por su `id`.

## Cómo crearlos en la intranet

Para cada archivo `.sql` de esta carpeta:

1. Entrá a la intranet: `http://3169.bepe.ar:8080`
2. Andá a **Informes (Reports)** → **Crear desde SQL** (*Create from SQL*).
3. Completá:
   - **Nombre**: el nombre del reporte (ej: `app_member_search`).
   - **Notas**: opcional.
   - **SQL**: pegá el contenido del `.sql` (sin las líneas de comentario `--` si no querés, son opcionales).
4. **Guardar** (*Save report*).
5. Ejecutalo una vez para probarlo. En la URL vas a ver el `id`, por ejemplo:
   `.../guided_reports.pl?reports=12&phase=Run...` → el **id es 12**.
6. Anotá ese id en `backend/.env`:

   | Archivo | Variable en .env |
   |---|---|
   | `01_member_search.sql` | `REPORT_MEMBER_SEARCH_ID` |
   | `02_member_loans.sql`  | `REPORT_MEMBER_LOANS_ID`  |
   | `03_loans_active.sql`  | `REPORT_LOANS_ACTIVE_ID`  |
   | `04_loans_overdue.sql` | `REPORT_LOANS_OVERDUE_ID` |

## Importante

- **No cambies el orden de las columnas del SELECT** sin actualizar `columns` en
  `app/koha/reports.py`: la app mapea las columnas por posición.
- Los reportes con parámetro (`<<...>>`) los completa la app automáticamente; la
  bibliotecaria nunca los escribe a mano.
- Son todos `SELECT` (solo lectura): no pueden modificar datos de la biblioteca.
- Si tu Koha pide "autorizar" reportes con SQL, puede que necesites el permiso
  `execute_reports` / `create_reports` en tu usuario de staff.
