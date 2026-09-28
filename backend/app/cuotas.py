"""Cuotas societarias: lee la planilla de Google (cuenta de servicio, solo lectura).

La planilla tiene UNA pestaña con varios años en bloques de 12 meses lado a lado.
Cada fila es un socio (con # Matrícula = carnet de Koha, que permite cruzar datos).

Marcas por mes:  'P' = pagó · vacío = debe · '-' = no corresponde (no suma deuda).
Para el año en curso, los meses futuros no cuentan como deuda.

Config (.env / variables de entorno):
  PAGOS_SHEET_ID            id de la planilla (en la URL)
  PAGOS_SHEET_TAB           nombre de la pestaña (por defecto 'SOCIOS 2026')
  La credencial de Google es la de `cuenta_google.py` (compartida con el calendario).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import time

from . import cuenta_google, pagos

logger = logging.getLogger("cuotas")

SHEET_ID = os.getenv("PAGOS_SHEET_ID", "1SDw0Xes3kPBUMmUaj9mOY5aPnu4577a_SUupKAk8F3o")
TAB = os.getenv("PAGOS_SHEET_TAB", "SOCIOS 2026")

# Los años van en bloques de 12 meses, uno al lado del otro: 2024 arranca en la columna
# 11 (0-based) y cada año siguiente 12 columnas más a la derecha. Se ofrecen todos desde
# PRIMER_ANIO hasta el año en curso (como mínimo 2026): un año nuevo sigue a la derecha
# en la misma pestaña. Si la planilla todavía no tiene sus columnas, sus meses cuentan
# como impagos salvo los pagos que se carguen desde la app.
PRIMER_ANIO = 2024
_COL_PRIMER_ANIO = 11


def col_de(anio: int) -> int:
    """Columna (0-based) donde arranca el bloque de 12 meses de `anio`."""
    return _COL_PRIMER_ANIO + 12 * (anio - PRIMER_ANIO)
MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
MESES_LARGO = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
               "septiembre", "octubre", "noviembre", "diciembre"]

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
_CACHE: dict = {"rows": None, "ts": 0.0}
_TTL = 300  # segundos


def configured() -> bool:
    return cuenta_google.configurada()


def _read_rows() -> list[list[str]]:
    """Lee todas las filas de la pestaña (con cache de _TTL segundos)."""
    now = time.time()
    if _CACHE["rows"] is not None and now - _CACHE["ts"] < _TTL:
        return _CACHE["rows"]

    import gspread

    gc = gspread.authorize(cuenta_google.credenciales(_SCOPES))
    rows = gc.open_by_key(SHEET_ID).worksheet(TAB).get_all_values()
    _CACHE["rows"], _CACHE["ts"] = rows, now
    return rows


def clear_cache() -> None:
    _CACHE["rows"] = None


def anios_disponibles() -> list[int]:
    return list(range(max(dt.date.today().year, 2026), PRIMER_ANIO - 1, -1))


def _estado_mes(v: str) -> str:
    n = (v or "").strip().upper()
    if "P" in n:
        return "pago"
    if n == "-":
        return "na"        # no corresponde
    return "debe"          # vacío u otra marca


def _ultimo_pago(r: list[str], ov: dict | None = None) -> dict | None:
    """Mes más reciente con pago del socio, mirando TODOS los años (planilla + app).

    Devuelve {'mes','anio','label','texto','ord'} o None si nunca pagó. `label` es corto
    para las tablas ("Ago 2026"); `texto`, para los mails ("agosto 2026"). `ord` =
    anio*100+mes, sirve para ordenar. Cubre el caso de que el último pago sea de un año
    anterior.
    `ov` = meses pagos cargados en la app ({'AAAA-MM': ...}).
    """
    ov = ov or {}
    mejor = None  # (anio, indice_mes 0-11)
    for anio in anios_disponibles():
        col0 = col_de(anio)
        for m in range(12):
            c = col0 + m
            en_planilla = len(r) > c and _estado_mes(r[c]) == "pago"
            en_app = f"{anio:04d}-{m + 1:02d}" in ov
            if en_planilla or en_app:
                if mejor is None or (anio, m) > mejor:
                    mejor = (anio, m)
    if mejor is None:
        return None
    anio, m = mejor
    return {"mes": MESES[m], "anio": anio, "label": f"{MESES[m]} {anio}",
            "texto": f"{MESES_LARGO[m]} {anio}", "ord": anio * 100 + (m + 1)}


def estado_cuotas(anio: int) -> dict:
    """Devuelve el estado de cuotas de todos los socios para un año."""
    if anio not in anios_disponibles():
        anio = max(anios_disponibles())
    col0 = col_de(anio)
    rows = _read_rows()

    hoy = dt.date.today()
    mes_tope = hoy.month if anio == hoy.year else 12  # año en curso: hasta el mes actual
    pagos_app = pagos.all_pagos()   # pagos cargados desde la app (se superponen a la planilla)

    socios = []
    for r in rows[2:]:  # filas 0 y 1 son encabezados
        if len(r) < 5 or not (r[1] or "").strip():   # sin matrícula → no es socio
            continue
        ov = pagos_app.get((r[1] or "").strip(), {})   # {'AAAA-MM': {ts, por}}
        meses = []
        pagos_n = debe = 0
        for m in range(12):
            c = col0 + m
            est = _estado_mes(r[c]) if len(r) > c else "debe"
            mk = f"{anio:04d}-{m + 1:02d}"
            desde_app = mk in ov
            if desde_app:
                est = "pago"
            vencido = (m + 1) <= mes_tope
            if est == "pago":
                pagos_n += 1
            elif est == "debe" and vencido:
                debe += 1
            meses.append({"mes": MESES[m], "estado": est, "vencido": vencido,
                          "app": desde_app, "app_por": ov.get(mk, {}).get("por", "") if desde_app else ""})
        socios.append({
            "matricula": (r[1] or "").strip(),
            "apellido": (r[2] or "").strip() if len(r) > 2 else "",
            "nombre": (r[3] or "").strip() if len(r) > 3 else "",
            "categoria": (r[4] or "").strip() if len(r) > 4 else "",
            "meses": meses,
            "pagos": pagos_n,
            "debe": debe,
            "impagos": [x["mes"] for x in meses if x["estado"] == "debe" and x["vencido"]],
            "estado": "al_dia" if debe == 0 else "debe",
            "ultimo_pago": _ultimo_pago(r, ov),   # último mes pago + año (planilla + app)
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
