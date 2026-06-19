# Arquitectura

## Contexto

La biblioteca usa **Koha** (vía DigiBePé) en `http://3169.bepe.ar:8080` (interfaz de
staff / intranet). Diagnóstico verificado contra el servidor real:

| Vía de acceso | Estado | Decisión |
|---|---|---|
| API REST `/api/v1/` | HTTP 404 | No existe (Koha viejo) → descartada |
| ILS-DI `ilsdi.pl` | HTTP 404 | No disponible → descartada |
| `svc/report` (JSON) | Existe, pero **limita a 10 filas y NO aplica parámetros** | ❌ Descartada para uso real |
| Ejecutar informe + **Descargar** (TSV) | Trae **todas** las filas y **sí aplica parámetros** | ✅ **Vía elegida** |
| Login de staff `mainpage.pl` | HTTP 200, login validado | ✅ Verificado end-to-end |

Conclusión: la única vía programática viable en este Koha es **iniciar sesión como
staff, ejecutar el informe guardado y descargar el resultado** (formato `tab`/TSV).
Verificado contra datos reales: 1363 socios, 403 préstamos vigentes, 185 vencidos,
y filtrado por parámetros funcionando.

> ⚠️ `svc/report` (la típica "API JSON" de Koha) en esta versión vieja está
> **clavada en 10 filas** y **ignora los parámetros** `<<>>` y `limit/offset/page`.
> Por eso NO se usa; se usa el flujo de ejecutar + descargar (`phase=Export&format=tab`).

## Componentes

```
┌──────────────────────────┐
│  Frontend (static/        │   POC: una página HTML/JS servida por el backend.
│  index.html)              │   Login + pestañas: Vigentes / Vencidos / Socios.
└────────────┬─────────────┘   (Producción futura: migrar a React + Mantine.)
             │ HTTP/JSON + Bearer token
┌────────────▼─────────────┐
│  Backend FastAPI          │
│  app/main.py    rutas+CORS+errores
│  app/api/routes.py  endpoints REST limpios
│  app/auth.py    login contra Koha + sesiones JWT
│  app/koha/reports.py  capa de datos (operación → reporte SQL)
│  app/koha/client.py   sesión HTTP con Koha + svc/report
└────────────┬─────────────┘
             │ login de staff + ejecutar informe + descargar TSV
┌────────────▼─────────────┐
│  Koha (DigiBePé)          │   Reportes SQL guardados (ver backend/sql/).
└──────────────────────────┘
```

## Decisiones clave

1. **Solo lectura.** `svc/report` solo ejecuta `SELECT`. La app no puede modificar
   ni borrar datos de la biblioteca: riesgo operativo casi nulo.

2. **Auth = credenciales de Koha de cada bibliotecaria** (pass-through).
   - No se crean ni guardan contraseñas aparte: Koha es el proveedor de identidad.
   - Al loguear, la app inicia sesión real en Koha; si funciona, guarda esa sesión
     (cookie) en memoria bajo un `sid` y emite un JWT propio que lleva ese `sid`.
   - Las consultas posteriores usan la sesión real de esa bibliotecaria → Koha
     aplica sus permisos. La contraseña nunca se persiste.

3. **El SQL vive del lado servidor, escondido.** La bibliotecaria nunca escribe SQL;
   elige opciones y la app traduce a `reporte + parámetros`. Los reportes se crean
   una sola vez en la intranet (`backend/sql/`).

4. **Mapeo de columnas por posición.** `svc/report` devuelve filas en el orden del
   `SELECT`. Cada reporte declara sus `columns` en `reports.py` en ese mismo orden.
   El formato exacto (lista vs objeto) se confirma con `scripts/probe_koha.py`.

## Límites del POC (mejoras futuras)

- **Sesiones en memoria** (`auth.SESSIONS`): con varios procesos/uvicorn workers o
  reinicios se pierden. Producción: store compartido (Redis) o sesiones sin estado.
- **Frontend**: HTML único para validar rápido. Producción: React + Vite + Mantine
  (tablas con orden/filtro/paginado, exportar a Excel, gráficos).
- **Escritura en Koha** (renovar, registrar, etc.): fuera de alcance; requeriría
  otro mecanismo (no `svc/report`).
- **Caché**: hoy cada pedido va a Koha. Si pesa, cachear reportes pesados unos minutos.
