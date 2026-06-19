"""Punto de entrada de la API + servidor del frontend del POC.

Correr:  uvicorn app.main:app --reload --port 8000
Docs:    http://localhost:8000/docs
App:     http://localhost:8000/
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .api.routes import router
from .config import settings
from .koha.client import KohaError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="Biblioteca App — Koha (DigiBePé)",
    description="POC: préstamos, vencimientos y socios desde el Koha de la Biblioteca Osvaldo Bayer.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(KohaError)
async def koha_error_handler(_request, exc: KohaError):
    """Traduce errores de Koha a respuestas HTTP claras para el frontend."""
    return JSONResponse(status_code=502, content={"detail": f"Koha: {exc}"})


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "koha_base_url": settings.koha_base_url}


app.include_router(router)

# ── Frontend del POC (HTML único + estáticos servidos por el mismo backend) ──
_STATIC = Path(__file__).resolve().parent.parent / "static"


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(_STATIC / "index.html")


@app.get("/logo.png", include_in_schema=False)
async def logo():
    return FileResponse(_STATIC / "logo.png", media_type="image/png")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(_STATIC / "logo.png", media_type="image/png")


# ── Programador de envíos automáticos (chequeo diario) ──────────────────────
# Tolerante: si falta APScheduler o falla, la app igual levanta (sin programador).
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from . import auto_mail

    try:
        from zoneinfo import ZoneInfo
        _tz = ZoneInfo(os.getenv("APP_TZ", "America/Argentina/Buenos_Aires"))
    except Exception:
        _tz = None

    _scheduler = AsyncIOScheduler(timezone=_tz) if _tz else AsyncIOScheduler()
    _hour = int(os.getenv("SCHED_HOUR", "9"))

    @app.on_event("startup")
    async def _start_scheduler():
        _scheduler.add_job(auto_mail.tick, "cron", hour=_hour, minute=0,
                           id="auto_mail_tick", replace_existing=True)
        _scheduler.start()
        logging.getLogger("main").info(
            "Programador activo (chequeo diario %02d:00, tz=%s).", _hour, _tz or "servidor")
except Exception as exc:  # noqa: BLE001
    logging.getLogger("main").warning("Programador no disponible: %s", exc)
