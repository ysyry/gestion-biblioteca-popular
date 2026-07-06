"""Tests de los pagos cargados desde la app y su superposición sobre la planilla."""
import datetime as dt

import pytest

from app import cuotas, pagos


@pytest.fixture
def store(monkeypatch):
    """Storage en memoria (no toca archivo ni Postgres)."""
    mem = {}
    monkeypatch.setattr(pagos.storage, "get", lambda k: mem.get(k))
    monkeypatch.setattr(pagos.storage, "set", lambda k, v: mem.__setitem__(k, v))
    return mem


def test_set_pago_guarda_y_quita(store):
    pagos.set_pago("100", 2026, 6, True, por="lau")
    d = pagos.pagos_de("100")
    assert "2026-06" in d and d["2026-06"]["por"] == "lau"
    # quitar
    pagos.set_pago("100", 2026, 6, False)
    assert pagos.pagos_de("100") == {}                 # sin meses → socio removido
    assert pagos.all_pagos() == {}


def test_set_pago_valida_entrada(store):
    with pytest.raises(ValueError):
        pagos.set_pago("", 2026, 6, True)              # sin matrícula
    with pytest.raises(ValueError):
        pagos.set_pago("100", 2026, 13, True)          # mes fuera de rango


def _fila():
    fila = [""] * 53
    fila[1] = "100"; fila[2] = "Pérez"; fila[3] = "Ana"; fila[4] = "Activo"
    fila[35] = "P"   # 2026 Ene pago en la planilla
    return [[""] * 53, [""] * 53, fila]


def test_pago_de_app_se_superpone_a_planilla(store, monkeypatch):
    monkeypatch.setattr(cuotas, "_read_rows", _fila)
    real_date = cuotas.dt.date
    class FakeDate(real_date):
        @classmethod
        def today(cls):
            return real_date(2026, 4, 15)   # hoy = abril → vencidos Ene-Abr
    monkeypatch.setattr(cuotas.dt, "date", FakeDate)

    # Sin pagos en la app: debe Feb, Mar, Abr (Ene pago en planilla).
    s0 = cuotas.estado_cuotas(2026)["socios"][0]
    assert s0["debe"] == 3 and s0["ultimo_pago"]["label"] == "Ene 2026"

    # Cargamos Feb y Mar desde la app → baja la deuda y avanza el último pago.
    pagos.set_pago("100", 2026, 2, True, por="lau")
    pagos.set_pago("100", 2026, 3, True, por="lau")
    s1 = cuotas.estado_cuotas(2026)["socios"][0]
    assert s1["debe"] == 1                         # solo Abr
    assert s1["ultimo_pago"]["label"] == "Mar 2026"
    feb = next(m for m in s1["meses"] if m["mes"] == "Feb")
    assert feb["estado"] == "pago" and feb["app"] is True and feb["app_por"] == "lau"
