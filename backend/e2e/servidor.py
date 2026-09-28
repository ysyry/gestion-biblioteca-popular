"""La app de verdad, pero sin salir a ningún lado: para las pruebas con Playwright.

Todo lo de la app corre tal cual (API, frontend, historial, automáticos, permisos).
Lo único que se reemplaza es lo que da a servicios de afuera:

  · Koha            → `datos.py` (socios, préstamos, notas). Entra "biblio" / "clave".
  · Planilla Google → `datos.filas_planilla()`.
  · Google Calendar → apagado.
  · SMTP            → una bandeja en memoria (`BANDEJA`): cada mail que la app mandaría,
                      ya armado (asunto, texto y HTML), para revisar qué le llega a cada socio.

Los datos propios de la app van a una carpeta temporal que se crea en cada corrida.

IMPORTANTE: este módulo fija las variables de entorno ANTES de importar `app`, así que
tiene que importarse antes que cualquier módulo de la app.
"""
from __future__ import annotations

import os
import socket
import tempfile
import threading
import time

PUERTO = int(os.getenv("E2E_PORT") or 0) or None


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PUERTO = PUERTO or _puerto_libre()
URL = f"http://127.0.0.1:{PUERTO}"
DATA_DIR = tempfile.mkdtemp(prefix="biblioteca-e2e-")

USUARIO_KOHA, CLAVE_KOHA = "biblio", "clave"

_ENTORNO = {
    "APP_DATA_DIR": DATA_DIR,
    "DATABASE_URL": "",
    "APP_SECRET_KEY": "e2e-" + "x" * 40,
    "APP_PUBLIC_URL": URL,
    "KOHA_BASE_URL": "http://koha.e2e.invalid",
    "KOHA_USER": "servicio",
    "KOHA_PASSWORD": "servicio",
    "MAIL_PROVIDER": "smtp",
    "MAIL_DRY_RUN": "false",
    "SMTP_HOST": "smtp.e2e.invalid",
    "SMTP_USER": "biblioteca@example.org",
    "SMTP_FROM": "biblioteca@example.org",
    "GOOGLE_SERVICE_ACCOUNT_JSON": "",
    "GOOGLE_SERVICE_ACCOUNT_FILE": "/nonexistent",
    "RESERVAS_CALENDARIO": "",
    "CALENDAR_ICS_URLS": "",
    "CALENDAR_ICS_URL": "",
    "SUBCOMISIONES": "Cultura,Prensa",
    "ESPACIOS": "Sala principal,Patio",
    "ARCA_ENABLED": "false",
    "SCHED_HOUR": "3",
    **{f"CALENDAR_{n}_URL": "" for n in range(1, 10)},
    **{f"REPORT_{k}_ID": str(i) for i, k in enumerate(
        ["MEMBER_SEARCH", "MEMBER_LOANS", "LOANS_ACTIVE", "LOANS_OVERDUE", "MEMBER_PROFILE",
         "MEMBER_ACCOUNT", "MEMBER_HISTORY", "LOANS_CONTACT"], start=1)},
}
os.environ.update(_ENTORNO)

# ── Recién ahora, la app ────────────────────────────────────────────────────────
import uvicorn  # noqa: E402

from app import cache, calendario_google, cuenta_google, cuotas, mail  # noqa: E402
from app.koha import client as koha_client  # noqa: E402
from app.main import app  # noqa: E402

from . import datos  # noqa: E402

# ── Koha ────────────────────────────────────────────────────────────────────────
_CUENTAS_KOHA = {(USUARIO_KOHA, CLAVE_KOHA), ("servicio", "servicio")}


async def _login(self):
    if (self._userid, self._password) not in _CUENTAS_KOHA:
        raise koha_client.KohaAuthError("Usuario o contraseña de Koha incorrectos.")


async def _run_report(self, report_id, params=None):
    return datos.reporte(int(report_id), params)


async def _run_sql(self, sql):
    return datos.sql(sql)


koha_client.KohaClient.login = _login
koha_client.KohaClient._ensure_login = _login
koha_client.KohaClient.run_report = _run_report
koha_client.KohaClient.run_sql = _run_sql

# ── Google ──────────────────────────────────────────────────────────────────────
cuenta_google.configurada = lambda: False
calendario_google.configurado = lambda: False
cuotas.configured = lambda: True
cuotas._read_rows = datos.filas_planilla

# ── SMTP: bandeja en memoria ────────────────────────────────────────────────────
BANDEJA: list[dict] = []


def _enviar(mensajes: list[dict]) -> list[dict]:
    resultados = []
    for m in mensajes:
        BANDEJA.append({"to": m["to"], "subject": m["subject"], "plain": m["plain"],
                        "html": m["html"]})
        resultados.append(mail._res(m, "sent"))
    return resultados


mail._send_sync = _enviar


def reiniciar() -> None:
    """Deja la app como recién instalada: sin datos propios, cachés ni mails."""
    import shutil

    from app import auth

    BANDEJA.clear()
    cache._store.clear()
    auth.SESIONES.clear()
    for f in os.listdir(DATA_DIR):
        p = os.path.join(DATA_DIR, f)
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)


# ── Arranque en un hilo ─────────────────────────────────────────────────────────
_servidor: uvicorn.Server | None = None


def arrancar() -> str:
    global _servidor
    config = uvicorn.Config(app, host="127.0.0.1", port=PUERTO, log_level="warning")
    _servidor = uvicorn.Server(config)
    threading.Thread(target=_servidor.run, daemon=True).start()
    for _ in range(200):
        if _servidor.started:
            return URL
        time.sleep(0.05)
    raise RuntimeError("El servidor de pruebas no arrancó.")


def frenar() -> None:
    if _servidor:
        _servidor.should_exit = True
