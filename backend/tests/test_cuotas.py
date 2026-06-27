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
