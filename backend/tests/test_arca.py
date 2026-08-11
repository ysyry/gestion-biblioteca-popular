"""Tests del módulo de facturación ARCA (lógica pura, sin red ni certificado real)."""
import base64
import json
import time
import xml.etree.ElementTree as ET
from datetime import date

import pytest

from app import arca, arca_pdf


# ── WSAA: armado y parseo del ticket ────────────────────────────────────────
def test_build_tra_bien_formado():
    xml = arca._build_tra()
    root = ET.fromstring(xml)
    assert root.findtext("service") == "wsfe"
    gen = root.findtext(".//generationTime")
    exp = root.findtext(".//expirationTime")
    uid = root.findtext(".//uniqueId")
    assert gen and exp and uid
    assert gen < exp                       # expiración posterior a la generación


def test_parse_ta_extrae_token_sign_y_margen():
    xml = (
        '<loginTicketResponse><header>'
        '<expirationTime>2030-01-01T12:00:00.000-03:00</expirationTime>'
        '</header><credentials><token>ABC</token><sign>XYZ</sign></credentials>'
        '</loginTicketResponse>'
    )
    ta = arca._parse_ta(xml)
    assert ta["token"] == "ABC" and ta["sign"] == "XYZ"
    # expires = expirationTime - 600s de margen
    from datetime import datetime
    esperado = datetime.fromisoformat("2030-01-01T12:00:00.000-03:00").timestamp() - 600
    assert ta["expires"] == pytest.approx(esperado)


def test_ta_valido():
    assert arca._ta_valido(None) is False
    assert arca._ta_valido({"token": "t", "expires": time.time() - 10}) is False
    assert arca._ta_valido({"token": "t", "expires": time.time() + 3600}) is True
    assert arca._ta_valido({"token": "", "expires": time.time() + 3600}) is False


def test_fmt_fecha():
    assert arca._fmt_fecha(date(2026, 7, 31)) == "20260731"


# ── Configuración / diagnóstico ─────────────────────────────────────────────
def test_configured_y_estado(monkeypatch, tmp_path):
    crt = tmp_path / "c.crt"
    key = tmp_path / "c.key"
    crt.write_text("x")
    key.write_text("y")
    monkeypatch.setattr(arca.settings, "arca_cuit", "30675834306")
    monkeypatch.setattr(arca.settings, "arca_pto_vta", 2)
    monkeypatch.setattr(arca.settings, "arca_cert_path", str(crt))
    monkeypatch.setattr(arca.settings, "arca_key_path", str(key))
    monkeypatch.setattr(arca.settings, "arca_prod", False)

    assert arca.configured() is True
    st = arca.estado()
    assert st["cert_ok"] and st["key_ok"] and st["entorno"] == "homologación"

    # sin punto de venta → no configurado y _require_configured levanta ArcaError
    monkeypatch.setattr(arca.settings, "arca_pto_vta", 0)
    assert arca.configured() is False
    with pytest.raises(arca.ArcaError):
        arca._require_configured()


def test_require_configured_sin_cert(monkeypatch, tmp_path):
    monkeypatch.setattr(arca.settings, "arca_cuit", "30675834306")
    monkeypatch.setattr(arca.settings, "arca_pto_vta", 2)
    monkeypatch.setattr(arca.settings, "arca_cert_path", str(tmp_path / "noexiste.crt"))
    monkeypatch.setattr(arca.settings, "arca_key_path", str(tmp_path / "noexiste.key"))
    with pytest.raises(arca.ArcaError):
        arca._require_configured()


# ── QR oficial de ARCA ──────────────────────────────────────────────────────
def test_qr_url_decodifica_al_formato_arca():
    url = arca_pdf.qr_url(
        fecha=date(2026, 7, 31), cuit="30675834306", pto_vta=2, tipo_cmp=11,
        nro_cmp=6821, importe=9000.0, cae="86316299804775",
        tipo_doc_rec=96, nro_doc_rec=28508420,
    )
    assert url.startswith("https://www.afip.gob.ar/fe/qr/?p=")
    payload = url.split("p=", 1)[1]
    data = json.loads(base64.b64decode(payload))
    assert data == {
        "ver": 1, "fecha": "2026-07-31", "cuit": 30675834306, "ptoVta": 2,
        "tipoCmp": 11, "nroCmp": 6821, "importe": 9000.0, "moneda": "PES",
        "ctz": 1, "tipoDocRec": 96, "nroDocRec": 28508420,
        "tipoCodAut": "E", "codAut": 86316299804775,
    }


def test_qr_url_sin_receptor_omite_doc():
    url = arca_pdf.qr_url(
        fecha=date(2026, 7, 31), cuit=30675834306, pto_vta=2, tipo_cmp=11,
        nro_cmp=1, importe=100.0, cae="1",
    )
    data = json.loads(base64.b64decode(url.split("p=", 1)[1]))
    assert "tipoDocRec" not in data and "nroDocRec" not in data


# ── Utilidades del PDF ──────────────────────────────────────────────────────
def test_money_formato_argentino():
    assert arca_pdf._money(9000) == "9.000,00"
    assert arca_pdf._money(1234567.5) == "1.234.567,50"
    assert arca_pdf._money(0) == "0,00"


def test_fechas_pdf():
    assert arca_pdf._ar("20260731") == "31/07/2026"
    assert arca_pdf._ar("2026-07-31") == "31/07/2026"
    assert arca_pdf._ar(date(2026, 7, 31)) == "31/07/2026"
    assert arca_pdf._iso(date(2026, 7, 31)) == "2026-07-31"


# ── Render del PDF ──────────────────────────────────────────────────────────
def test_factura_c_pdf_genera_pdf():
    pdf = arca_pdf.factura_c_pdf(
        pto_vta=2, nro=6821, fecha=date(2026, 7, 31),
        cae="86316299804775", cae_vto="20260810", importe=9000.0,
        receptor={"doc_tipo": 96, "doc_nro": 28508420, "nombre": "PASSERIEU LUCILA"},
        items=[{"descripcion": "cuotas hasta julio 2026", "cantidad": 1, "precio": 9000.0}],
    )
    assert isinstance(pdf, (bytes, bytearray))
    assert bytes(pdf)[:5] == b"%PDF-"
    assert len(pdf) > 1000
