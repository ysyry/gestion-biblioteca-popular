"""La cuenta de servicio de Google de la biblioteca.

Es una sola credencial y la usan varios módulos: las cuotas (leer la planilla) y el
calendario (publicar las reservas aprobadas). Cada uno pide solo el permiso (scope)
que necesita.

Config (.env / variables de entorno):
  GOOGLE_SERVICE_ACCOUNT_JSON   credencial inline (JSON en una variable) — para Railway
  GOOGLE_SERVICE_ACCOUNT_FILE   o ruta al .json — para desarrollo local
Si no hay ninguna, se busca `credentials/google-service-account.json`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _origen() -> tuple[str | None, str | None]:
    inline = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if inline:
        return ("inline", inline)
    path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if not path:
        default = Path(__file__).resolve().parent.parent / "credentials" / "google-service-account.json"
        path = str(default) if default.exists() else ""
    return ("file", path) if path else (None, None)


def configurada() -> bool:
    kind, val = _origen()
    return bool(kind and val)


def _info() -> dict:
    kind, val = _origen()
    if kind == "inline":
        # strict=False tolera saltos de línea reales dentro de private_key
        # (pasa cuando se pega la credencial sin minificar en las variables).
        return json.loads(val, strict=False)
    if kind == "file":
        return json.loads(Path(val).read_text(encoding="utf-8"))
    raise RuntimeError("Credencial de Google no configurada (GOOGLE_SERVICE_ACCOUNT_JSON).")


def credenciales(scopes: list[str]):
    """Credencial lista para usar con los permisos pedidos."""
    from google.oauth2.service_account import Credentials

    return Credentials.from_service_account_info(_info(), scopes=scopes)


def email() -> str:
    """La dirección de la cuenta de servicio: es con quien hay que compartir las cosas."""
    try:
        return _info().get("client_email", "")
    except Exception:  # noqa: BLE001
        return ""
