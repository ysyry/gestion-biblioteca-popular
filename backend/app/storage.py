"""Almacenamiento clave-valor (JSON) para datos propios de la app.

- Si hay `DATABASE_URL` (Postgres) → guarda en una tabla `app_kv (key, value jsonb)`.
- Si no → cae a un archivo JSON local en `APP_DATA_DIR` (útil en desarrollo o sin base).

Así migrar de archivo a base es transparente para el resto del código: todo pasa
por `get(key)` / `set(key, value)`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DATABASE_URL = os.getenv("DATABASE_URL")
_DATA_DIR = Path(os.getenv("APP_DATA_DIR") or (Path(__file__).resolve().parent.parent / "data"))


def using_db() -> bool:
    return bool(DATABASE_URL)


# ── Backend Postgres ────────────────────────────────────────────────────────
def _conn():
    import psycopg
    return psycopg.connect(DATABASE_URL)


def _ensure(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS app_kv (key text PRIMARY KEY, value jsonb NOT NULL)")


# ── Backend archivo (fallback) ──────────────────────────────────────────────
def _file(key: str) -> Path:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    return _DATA_DIR / f"{key}.json"


# ── API pública ─────────────────────────────────────────────────────────────
def get(key: str):
    if DATABASE_URL:
        with _conn() as conn:
            _ensure(conn)
            row = conn.execute("SELECT value FROM app_kv WHERE key = %s", (key,)).fetchone()
            return row[0] if row else None
    f = _file(key)
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def set(key: str, value) -> None:
    if DATABASE_URL:
        from psycopg.types.json import Jsonb
        with _conn() as conn:
            _ensure(conn)
            conn.execute(
                "INSERT INTO app_kv (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, Jsonb(value)),
            )
            conn.commit()
        return
    _file(key).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
