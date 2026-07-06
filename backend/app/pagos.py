"""Pagos de cuota cargados desde la app.

La planilla de Google sigue siendo el histórico (solo lectura). Acá se guardan los
pagos que cargan las bibliotecarias desde la app, por socio y mes/año, y se
superponen a la planilla al calcular el estado de cuotas.

Estructura en storage (clave 'cuotas_pagos'):
  { "<matricula>": { "AAAA-MM": {"ts": "<iso>", "por": "<usuario>"} } }
La presencia de la clave "AAAA-MM" significa que ese mes está pago (cargado desde
la app). Guardar el ts y quién lo cargó sirve de registro/auditoría.
"""
from __future__ import annotations

import datetime as dt

from . import storage

KEY = "cuotas_pagos"


def mes_key(anio: int, mes: int) -> str:
    return f"{int(anio):04d}-{int(mes):02d}"


def all_pagos() -> dict:
    return storage.get(KEY) or {}


def pagos_de(matricula: str) -> dict:
    """Meses pagos (desde la app) de un socio: {'AAAA-MM': {ts, por}}."""
    return all_pagos().get(str(matricula).strip(), {})


def set_pago(matricula: str, anio: int, mes: int, pagado: bool, por: str = "") -> dict:
    """Marca (o desmarca) un mes como pago para un socio. Devuelve el registro resultante."""
    mat = str(matricula or "").strip()
    if not mat:
        raise ValueError("Falta la matrícula.")
    if not (1 <= int(mes) <= 12):
        raise ValueError("Mes fuera de rango (1-12).")
    if int(anio) < 2000:
        raise ValueError("Año inválido.")
    data = all_pagos()
    meses = data.setdefault(mat, {})
    k = mes_key(anio, mes)
    if pagado:
        meses[k] = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "por": por}
    else:
        meses.pop(k, None)
        if not meses:
            data.pop(mat, None)
    storage.set(KEY, data)
    return {"matricula": mat, "mes": k, "pagado": pagado}
