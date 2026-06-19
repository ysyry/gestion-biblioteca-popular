# Gestión de Bibliotecas Populares

Software de gestión para bibliotecas populares, en desarrollo. Actualmente enfocado
en la **Biblioteca Popular Osvaldo Bayer** (Villa La Angostura, Neuquén).

Se conecta al sistema **Koha (DigiBePé)** y ofrece una interfaz simple para consultar
préstamos y socios, enviar correos y programar avisos automáticos, sin necesidad de
escribir SQL. Acceso de solo lectura sobre Koha.

## Características

- **Préstamos** — vigentes y vencidos, con búsqueda y ordenamiento.
- **Socios** — búsqueda y ficha con préstamos vigentes e historial.
- **Correos** — envío a socios seleccionados, con plantillas y variables
  (nombre, libros vencidos / por vencer, etc.).
- **Avisos automáticos** — resumen periódico interno y recordatorios a los socios,
  configurables.

## Tecnología

- **Backend:** FastAPI (Python).
- **Frontend:** página única (HTML/CSS/JS) servida por el mismo backend.
- **Datos:** Koha 3.x (sin API REST) vía reportes SQL guardados, exportados como TSV.
- **Autenticación:** cada integrante del equipo ingresa con sus credenciales de Koha.

## Desarrollo local

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # completar credenciales e IDs de reportes
uvicorn app.main:app --reload --port 8000
```

App en `http://localhost:8000` · documentación de la API en `/docs`.

Los reportes SQL se crean una vez en Koha con `python scripts/setup_reports.py`
(ver `backend/sql/`).

## Deploy

Pensado para correr con Docker. Ver [`DEPLOY.md`](DEPLOY.md).

## Licencia

[MIT](LICENSE).
