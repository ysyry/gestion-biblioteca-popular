# Biblioteca App — Koha (DigiBePé)

App para la **Biblioteca Popular Osvaldo Bayer** que toma datos del sistema Koha
(DigiBePé) y los presenta a las bibliotecarias en una interfaz simple, sin SQL.

POC inicial: **préstamos / vencimientos** y **socios**. Solo lectura.

## Arquitectura

```
Frontend (React + Vite + TS + Mantine)
        │  HTTP/JSON
        ▼
Backend (FastAPI + httpx)  ──►  Koha (svc/report, JSON)
        │                         login de staff + reportes SQL guardados
        └─ login propio de la app (las bibliotecarias NO usan credenciales de Koha)
```

- **Koha es viejo (3.x): no tiene API REST.** Accedemos vía reportes SQL guardados,
  que se consumen como JSON desde `/cgi-bin/koha/svc/report?id=N`.
- Los reportes SQL se crean **una sola vez** en la intranet de Koha (ver `backend/sql/`).
  La app los invoca con parámetros; la bibliotecaria nunca ve SQL.
- **Solo lectura**: `svc/report` solo ejecuta `SELECT`. La app no puede modificar Koha.

## Estado / pasos

- [x] Diagnóstico: `svc/report` existe (HTTP 500 sin `id`); sin REST API; sin ILS-DI.
- [x] Backend FastAPI + auth contra Koha + endpoints (loans/members) + frontend POC.
- [x] Login verificado end-to-end contra el Koha real (rechaza credenciales falsas).
- [ ] **Paso pendiente A — Crear los 4 reportes SQL en la intranet** (`backend/sql/`) y
      cargar sus IDs en `backend/.env`. ← **lo necesito de vos**
- [ ] **Paso pendiente B — Probar con credenciales reales** (`scripts/probe_koha.py`)
      para confirmar el formato del JSON y, si hace falta, ajustar el mapeo.
- [ ] Producción — migrar frontend a React + Mantine y empaquetar con Docker Compose.

> El POC ya corre: con los 4 reportes creados y sus IDs en `.env`, devuelve datos reales.

## Backend — cómo correr

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # completá credenciales y IDs de reportes
```

### Sondeo (Paso 1 — hacer esto primero)

Antes de construir nada encima, verificamos el formato real del JSON de tu Koha:

```bash
# 1) Crear UN reporte de prueba en la intranet (ej: el de socios) y anotar su id
# 2) Completar KOHA_* en .env
python scripts/probe_koha.py <ID_DEL_REPORTE>
```

El script loguea a Koha, ejecuta el reporte y muestra la respuesta cruda + el
formato detectado (filas como arrays u objetos). Con eso afinamos el mapeo.

### Levantar la API

```bash
uvicorn app.main:app --reload --port 8000
# docs interactivas: http://localhost:8000/docs
```
