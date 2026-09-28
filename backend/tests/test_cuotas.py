"""Tests del parseo de la planilla de cuotas (sin tocar Google Sheets)."""
import datetime as dt

from app import cuotas


def test_estado_mes():
    assert cuotas._estado_mes("P") == "pago"
    assert cuotas._estado_mes("p") == "pago"      # minúscula
    assert cuotas._estado_mes("-") == "na"        # no corresponde
    assert cuotas._estado_mes("") == "debe"
    assert cuotas._estado_mes(" P ") == "pago"


def _fake_rows():
    # Filas 0 y 1 = encabezados; bloque 2026 arranca en la col 35.
    header0 = [""] * 53
    header1 = [""] * 53
    header1[1] = "# Matrícula"
    fila = [""] * 53
    fila[1] = "100"; fila[2] = "Pérez"; fila[3] = "Ana"; fila[4] = "Activo"
    # 2026 (col 35..46): pagó Ene, Feb; debe Mar; resto vacío
    fila[35] = "P"; fila[36] = "P"; fila[37] = ""
    return [header0, header1, fila]


def test_estado_cuotas_cuenta_deuda_solo_meses_vencidos(monkeypatch):
    monkeypatch.setattr(cuotas, "_read_rows", _fake_rows)
    # Forzamos "hoy" = abril 2026 → vencidos Ene-Abr; mayo+ no cuentan.
    real_date = cuotas.dt.date
    class FakeDate(real_date):
        @classmethod
        def today(cls):
            return real_date(2026, 4, 15)
    monkeypatch.setattr(cuotas.dt, "date", FakeDate)

    d = cuotas.estado_cuotas(2026)
    s = d["socios"][0]
    assert s["matricula"] == "100"
    assert s["pagos"] == 2                 # Ene, Feb
    assert s["debe"] == 2                  # Mar y Abr (vencidos, sin pagar)
    assert "Mar" in s["impagos"] and "May" not in s["impagos"]
    assert s["estado"] == "debe"
    assert s["ultimo_pago"] == {"mes": "Feb", "anio": 2026, "label": "Feb 2026",
                                "texto": "febrero 2026", "ord": 202602}


def test_ultimo_pago_mira_todos_los_anios():
    # Pagó hasta Oct 2025 y nada en 2026 → el último pago es de 2025.
    fila = [""] * 53
    fila[1] = "200"
    fila[23 + 9] = "P"    # 2025 bloque col 23; mes índice 9 = Oct
    assert cuotas._ultimo_pago(fila) == {"mes": "Oct", "anio": 2025, "label": "Oct 2025",
                                         "texto": "octubre 2025", "ord": 202510}
    # Sin ninguna P → None
    assert cuotas._ultimo_pago([""] * 53) is None


def _hoy(monkeypatch, anio, mes, dia):
    real_date = cuotas.dt.date

    class FakeDate(real_date):
        @classmethod
        def today(cls):
            return real_date(anio, mes, dia)
    monkeypatch.setattr(cuotas.dt, "date", FakeDate)


def test_un_anio_nuevo_sigue_a_la_derecha_en_la_planilla(monkeypatch):
    # Febrero de 2027: la planilla ya tiene el bloque 2027 (col 47) y Ana pagó enero.
    _hoy(monkeypatch, 2027, 2, 10)
    fila = [""] * 65
    fila[1] = "100"; fila[2] = "Pérez"; fila[3] = "Ana"; fila[4] = "Activo"
    fila[47] = "P"
    monkeypatch.setattr(cuotas, "_read_rows", lambda: [[""] * 65, [""] * 65, fila])
    monkeypatch.setattr(cuotas.pagos, "all_pagos", lambda: {})

    assert cuotas.anios_disponibles()[0] == 2027
    d = cuotas.estado_cuotas(max(cuotas.anios_disponibles()))
    s = d["socios"][0]
    assert d["anio"] == 2027
    assert s["debe"] == 1 and s["impagos"] == ["Feb"]
    assert s["ultimo_pago"]["texto"] == "enero 2027"


def test_un_anio_sin_columnas_todavia_toma_los_pagos_de_la_app(monkeypatch):
    # La planilla llega hasta 2026, pero el pago de enero 2027 se cargó desde la app.
    _hoy(monkeypatch, 2027, 2, 10)
    fila = [""] * 47
    fila[1] = "100"; fila[2] = "Pérez"; fila[3] = "Ana"
    monkeypatch.setattr(cuotas, "_read_rows", lambda: [[""] * 47, [""] * 47, fila])
    monkeypatch.setattr(cuotas.pagos, "all_pagos", lambda: {"100": {"2027-01": {"por": "biblio"}}})

    s = cuotas.estado_cuotas(2027)["socios"][0]
    assert s["debe"] == 1 and s["impagos"] == ["Feb"]
    assert s["ultimo_pago"]["texto"] == "enero 2027"
