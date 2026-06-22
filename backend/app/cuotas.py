"""Cuotas societarias: lee la planilla de Google (cuenta de servicio, solo lectura).

La planilla tiene UNA pestaña con varios años en bloques de 12 meses lado a lado.
Cada fila es un socio (con # Matrícula = carnet de Koha, que permite cruzar datos).

Marcas por mes:  'P' = pagó · vacío = debe · '-' = no corresponde (no suma deuda).
Para el año en curso, los meses futuros no cuentan como deuda.

Config (.env / variables de entorno):
  PAGOS_SHEET_ID            id de la planilla (en la URL)
  PAGOS_SHEET_TAB           nombre de la pestaña (por defecto 'SOCIOS 2026')
  GOOGLE_SERVICE_ACCOUNT_JSON   credencial inline (JSON en una variable) — para Railway
  GOOGLE_SERVICE_ACCOUNT_FILE   o ruta al .json — para desarrollo local
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("cuotas")

SHEET_ID = os.getenv("PAGOS_SHEET_ID", "1SDw0Xes3kPBUMmUaj9mOY5aPnu4577a_SUupKAk8F3o")
TAB = os.getenv("PAGOS_SHEET_TAB", "SOCIOS 2026")

# Columna (0-based) donde arranca el bloque de 12 meses de cada año.
YEAR_BLOCKS = {2024: 11, 2025: 23, 2026: 35}
MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
_CACHE: dict = {"rows": None, "ts": 0.0}
_TTL = 300  # segundos


def _creds_source():
    inline = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if inline:
        return ("inline", inline)
    path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if not path:
        default = Path(__file__).resolve().parent.parent / "credentials" / "google-service-account.json"
        path = str(default) if default.exists() else ""
    return ("file", path) if path else (None, None)


def configured() -> bool:
    kind, val = _creds_source()
    return bool(kind and val)


def _read_rows() -> list[list[str]]:
    """Lee todas las filas de la pestaña (con cache de _TTL segundos)."""
    now = time.time()
    if _CACHE["rows"] is not None and now - _CACHE["ts"] < _TTL:
        return _CACHE["rows"]

    import gspread
    from google.oauth2.service_account import Credentials

    kind, val = _creds_source()
    if kind == "inline":
        creds = Credentials.from_service_account_info(json.loads(val), scopes=_SCOPES)
    elif kind == "file":
        creds = Credentials.from_service_account_file(val, scopes=_SCOPES)
    else:
        raise RuntimeError("Credencial de Google no configurada (ver cuotas.py).")

    gc = gspread.authorize(creds)
    rows = gc.open_by_key(SHEET_ID).worksheet(TAB).get_all_values()
    _CACHE["rows"], _CACHE["ts"] = rows, now
    return rows


def anios_disponibles() -> list[int]:
    return sorted(YEAR_BLOCKS.keys(), reverse=True)


def _estado_mes(v: str) -> str:
    n = (v or "").strip().upper()
    if "P" in n:
        return "pago"
    if n == "-":
        return "na"        # no corresponde
    return "debe"          # vacío u otra marca


def estado_cuotas(anio: int) -> dict:
    """Devuelve el estado de cuotas de todos los socios para un año."""
    if anio not in YEAR_BLOCKS:
        anio = max(YEAR_BLOCKS)
    col0 = YEAR_BLOCKS[anio]
    rows = _read_rows()

    hoy = dt.date.today()
    mes_tope = hoy.month if anio == hoy.year else 12  # año en curso: hasta el mes actual

    socios = []
    for r in rows[2:]:  # filas 0 y 1 son encabezados
        if len(r) < 5 or not (r[1] or "").strip():   # sin matrícula → no es socio
            continue
        meses = []
        pagos = debe = 0
        for m in range(12):
            c = col0 + m
            est = _estado_mes(r[c]) if len(r) > c else "debe"
            vencido = (m + 1) <= mes_tope
            if est == "pago":
                pagos += 1
            elif est == "debe" and vencido:
                debe += 1
            meses.append({"mes": MESES[m], "estado": est, "vencido": vencido})
        socios.append({
            "matricula": (r[1] or "").strip(),
            "apellido": (r[2] or "").strip() if len(r) > 2 else "",
            "nombre": (r[3] or "").strip() if len(r) > 3 else "",
            "categoria": (r[4] or "").strip() if len(r) > 4 else "",
            "meses": meses,
            "pagos": pagos,
            "debe": debe,
            "estado": "al_dia" if debe == 0 else "debe",
        })

    total = len(socios)
    al_dia = sum(1 for s in socios if s["estado"] == "al_dia")
    # Recaudación por mes: cuántos pagaron cada mes.
    por_mes = []
    for m in range(12):
        n = sum(1 for s in socios if s["meses"][m]["estado"] == "pago")
        por_mes.append({"label": MESES[m], "count": n})

    return {
        "anio": anio,
        "anios": anios_disponibles(),
        "total": total,
        "al_dia": al_dia,
        "en_deuda": total - al_dia,
        "pct_al_dia": round(100 * al_dia / total, 1) if total else 0,
        "por_mes": por_mes,
        "socios": socios,
    }
