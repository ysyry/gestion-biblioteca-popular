#!/usr/bin/env python3
"""Exploración del Koha: corre un SELECT arbitrario (solo lectura) y muestra el TSV.

Uso (con el venv y .env de servicio):
    python scripts/explore.py "SELECT COUNT(*) FROM items"
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings          # noqa: E402
from app.koha.client import KohaClient, RUN_PATH  # noqa: E402


async def main() -> int:
    sql = sys.argv[1] if len(sys.argv) > 1 else "SELECT COUNT(*) FROM items"
    c = KohaClient(settings.koha_base_url, settings.koha_user, settings.koha_password)
    await c.login()
    try:
        resp = await c._client.post(
            RUN_PATH, data={"sql": sql, "format": "tab", "phase": "Export", "submit": "Bajar"}
        )
        print(resp.text[:6000])
    finally:
        await c.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
