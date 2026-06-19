# Issue — Módulo de pagos de cuotas (separado)

**Estado:** pendiente / a desarrollar más adelante.

## Contexto
Los pagos de cuotas societarias **no** se gestionan dentro de esta app: hoy se llevan
en una planilla de Google aparte. Por eso se sacó de la app todo lo vinculado a plata
(deuda, cuotas y estado de cuenta), para que la herramienta quede enfocada en
**socios y préstamos** y no muestre datos de pagos que viven en otro lado.

**Planilla online de pagos (fuente de verdad actual):**
https://docs.google.com/spreadsheets/d/1SDw0Xes3kPBUMmUaj9mOY5aPnu4577a_SUupKAk8F3o/edit?gid=1501034146#gid=1501034146

## Qué se quitó de la app (commit del Módulo A)
- **Backend** (`app/api/routes.py`): el endpoint `GET /members/{cardnumber}/profile`
  ya **no** devuelve `deuda`, `estado`, `cuenta` ni `cuotas`. Quedó solo `socio`,
  `prestamos_vigentes` e `historial`. Se eliminó el helper `_es_cuota`.
- **Frontend** (`static/index.html`): la ficha del socio ya no muestra el recuadro de
  deuda, ni la sección 💳 Cuotas, ni 🧾 Estado de cuenta.

Lo que NO se tocó (sigue disponible por si el módulo futuro lo reusa):
- El reporte `member_account` (`REPORT_MEMBER_ACCOUNT_ID`) y el método
  `KohaRepository.member_account()` siguen existiendo, pero **nadie los llama**.
- La columna `deuda` del reporte `member_profile` sigue viniendo de Koha pero se ignora.
- En el CSS quedan estilos `.ficha-deuda` sin uso (inocuos).

## Qué construir cuando se haga el módulo
Ideas a definir cuando arranquemos:
- **Lectura de la planilla de Google** (Sheets API o export CSV publicado) para traer
  el estado de cuota de cada socio por carnet/nombre.
- Vista de **morosos de cuota** y posibilidad de cruzarlo con préstamos.
- Eventual **conciliación** entre lo que dice Koha (`accountlines`) y la planilla, si se
  decide unificar. Hoy la fuente de verdad es la planilla, no Koha.
- Definir permisos: quién puede ver/editar estado de pagos.

## Decisiones abiertas
- ¿La app solo **lee** la planilla o también **escribe** pagos? (empezar por solo lectura).
- ¿Se sigue usando Koha para pagos o se abandona del todo en favor de la planilla?
