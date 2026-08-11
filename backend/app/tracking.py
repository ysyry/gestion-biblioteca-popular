"""Seguimiento de aperturas de mail (píxel invisible).

Cada destinatario de un envío recibe en el HTML una imagen de 1×1 transparente
cuya URL lleva un token firmado con `APP_SECRET_KEY`. Cuando el cliente de correo
carga esa imagen, la app registra la apertura (ver `historial.record_open`).

El token es `<run_id>.<idx>.<firma>`: identifica el envío y la posición del
destinatario dentro de él. Va firmado para que nadie pueda inventar aperturas ni
enumerar envíos ajenos; no lleva el mail de la persona.

LÍMITES REALES de esta técnica (importante para no leer de más los datos):
  - Muchos clientes BLOQUEAN imágenes por defecto → la persona puede haber leído
    el mail sin que se registre. "Sin abrir" NO significa "no lo leyó".
  - Apple Mail (Mail Privacy Protection) PRECARGA las imágenes → puede marcar
    abierto sin que nadie lo haya leído. Hay falsos positivos.
  - Gmail sirve las imágenes por su proxy y las cachea → la primera apertura se
    ve bien, las repeticiones no son confiables.
  - La parte en texto plano del mail no lleva píxel.
Sirve como señal agregada y orientativa, no como prueba de lectura individual.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from .config import settings

_SIG_LEN = 12


def new_run_id() -> str:
    """Identificador opaco de un envío (no correlativo: no se puede enumerar)."""
    return secrets.token_hex(8)


def _sign(payload: str) -> str:
    return hmac.new(settings.app_secret_key.encode("utf-8"),
                    payload.encode("utf-8"), hashlib.sha256).hexdigest()[:_SIG_LEN]


def make_token(run_id: str, idx: int) -> str:
    payload = f"{run_id}.{idx}"
    return f"{payload}.{_sign(payload)}"


def parse_token(token: str) -> tuple[str, int] | None:
    """Devuelve (run_id, idx) si la firma es válida; None si no."""
    token = (token or "").strip()
    if token.endswith(".gif"):
        token = token[:-4]
    parts = token.split(".")
    if len(parts) != 3:
        return None
    run_id, idx, sig = parts
    if not hmac.compare_digest(sig, _sign(f"{run_id}.{idx}")):
        return None
    try:
        return run_id, int(idx)
    except ValueError:
        return None


def pixel_url(run_id: str, idx: int) -> str | None:
    """URL absoluta del píxel. None si no hay APP_PUBLIC_URL (sin ella no hay seguimiento)."""
    base = (settings.app_public_url or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/api/t/{make_token(run_id, idx)}.gif"


def enabled() -> bool:
    return bool((settings.app_public_url or "").strip())
