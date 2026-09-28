"""Tests de las etiquetas de los mails: cada una que se ofrece sale con su dato en cada envío.

Sobre todo las de cuota: {{meses_debe}} salía 0 en los automáticos que no tenían tildada
la casilla "Deuda de cuota", y en Mails si la etiqueta estaba en el asunto o con espacios.
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import auto_mail, cuotas, etiquetas, historial, mail, permisos
from app.auth import Sesion, get_current_username, get_session
from app.main import app

# Una socia con un libro vencido que además debe tres meses de cuota.
PRESTAMOS = [{"cardnumber": "0042", "firstname": "Ana", "surname": "Paz", "email": "ana@x.org",
              "title": "El Aleph", "date_due": "2026-09-01", "dias_atraso": 10}]
MIEMBROS = {"42": {"cardnumber": "0042", "firstname": "Ana", "surname": "Paz",
                   "email": "ana@x.org", "categorycode": "A"},
            "7": {"cardnumber": "7", "firstname": "Beto", "surname": "Gil",
                  "email": "beto@x.org", "categorycode": "A"}}
SOCIA = {"matricula": "42", "apellido": "Paz", "nombre": "Ana", "debe": 3,
         "impagos": ["Jul", "Ago", "Sep"],
         "ultimo_pago": {"mes": "Jun", "anio": 2026, "label": "Jun 2026", "texto": "junio 2026", "ord": 202606}}
BETO = {"matricula": "7", "apellido": "Gil", "nombre": "Beto", "debe": 0, "impagos": [], "ultimo_pago": None}


@pytest.fixture
def koha_y_planilla(monkeypatch):
    """Koha y la planilla de mentira. Cuenta cuántas veces se lee la planilla."""
    leidas = []

    async def prestamos():
        return PRESTAMOS

    async def miembros():
        return MIEMBROS

    async def planilla():
        leidas.append(1)
        return {"42": SOCIA, "7": BETO}

    monkeypatch.setattr(auto_mail, "_all_loans", prestamos)
    monkeypatch.setattr(auto_mail, "_members_map", miembros)
    monkeypatch.setattr(auto_mail, "_cuota_map", planilla)
    return leidas


def _socios(**kw):
    return {"tipo": "socios", "dias_antes": 3, "umbral_atraso": 1, "incluir_vencidos": True,
            "incluir_por_vencer": True, "incluir_cuotas": False, "umbral_cuota": 1, "excluidos": [],
            "subject": "Hola", "body": "Hola {{nombre}}", "footer": "", **kw}


def _interno(**kw):
    return {"tipo": "interno", "dias_antes": 7, "umbral_atraso": 1, "incluir_cuotas": False,
            "umbral_cuota": 1, "subject": "Resumen", "body": "{{lista_vencidos}}", "footer": "", **kw}


# ── El catálogo ─────────────────────────────────────────────────────────────
def test_detecta_etiquetas_con_y_sin_espacios():
    assert etiquetas.usadas("Hola {{ nombre }}", None, "debés {{meses_debe}}") == {"nombre", "meses_debe"}


def test_ofrece_el_ultimo_mes_pago():
    socio = [i["etiqueta"] for g in etiquetas.catalogo()["socio"] for i in g["items"]]
    assert "ultimo_mes_pago" in socio and "meses_debe" in socio and "email" in socio


def test_las_etiquetas_de_cuota_de_un_socio():
    assert etiquetas.cuota_socio(SOCIA) == {"meses_debe": "3", "meses_impagos": "Jul, Ago, Sep",
                                            "ultimo_mes_pago": "junio 2026"}
    assert etiquetas.cuota_socio(BETO)["ultimo_mes_pago"] == "sin pagos registrados"
    assert etiquetas.cuota_socio(BETO)["meses_impagos"] == "—"
    assert etiquetas.cuota_socio(None) == {"meses_debe": "0", "meses_impagos": "—", "ultimo_mes_pago": "—"}


# ── Automáticos a socios ────────────────────────────────────────────────────
async def test_a_socios_salen_todas_las_etiquetas_que_se_ofrecen(koha_y_planilla):
    recs = (await auto_mail._socios_recipients(_socios(body="{{meses_debe}}")))["recipients"]
    v = recs[0]["vars"]
    faltan = [k for k in etiquetas.nombres(etiquetas.SOCIO) if k not in v]
    assert faltan == []
    assert v["email"] == "ana@x.org" and v["carnet"] == "0042"


async def test_meses_debe_sale_aunque_no_este_tildada_la_cuota(koha_y_planilla):
    """El caso que salía 0: la casilla decide a quién se le manda, no qué datos lleva."""
    recs = (await auto_mail._socios_recipients(
        _socios(body="Debés {{meses_debe}} meses ({{meses_impagos}}). Último pago: {{ultimo_mes_pago}}.")))["recipients"]
    assert len(recs) == 1                                  # Beto no tiene libros: no entra
    v = recs[0]["vars"]
    assert (v["meses_debe"], v["meses_impagos"], v["ultimo_mes_pago"]) == ("3", "Jul, Ago, Sep", "junio 2026")


async def test_si_no_se_usa_la_cuota_no_se_lee_la_planilla(koha_y_planilla):
    await auto_mail._socios_recipients(_socios())
    assert koha_y_planilla == []


async def test_la_etiqueta_en_el_asunto_o_el_pie_tambien_cuenta(koha_y_planilla):
    recs = (await auto_mail._socios_recipients(_socios(subject="Debés {{ meses_debe }}")))["recipients"]
    assert recs[0]["vars"]["meses_debe"] == "3"
    recs = (await auto_mail._socios_recipients(_socios(footer="Último pago: {{ultimo_mes_pago}}")))["recipients"]
    assert recs[0]["vars"]["ultimo_mes_pago"] == "junio 2026"


# ── Resumen interno ─────────────────────────────────────────────────────────
async def test_interno_salen_todas_las_etiquetas_que_se_ofrecen(koha_y_planilla):
    v = (await auto_mail.build_interno(_interno(incluir_cuotas=True)))["vars"]
    assert [k for k in etiquetas.nombres(etiquetas.INTERNO) if k not in v] == []
    assert v["fecha"] == date.today().strftime("%d/%m/%Y")


async def test_interno_lista_las_cuotas_si_el_mensaje_las_usa(koha_y_planilla):
    d = await auto_mail.build_interno(_interno(body="Deben cuota ({{total_deudores_cuota}}):\n{{lista_cuotas}}"))
    assert d["vars"]["total_deudores_cuota"] == "1"
    assert "Paz, Ana (mat. 42) — debe 3: Jul, Ago, Sep · último pago: junio 2026" in d["vars"]["lista_cuotas"]
    assert "Último pago" in d["html"]["lista_cuotas"]


# ── Mails (pestaña Escribir) ────────────────────────────────────────────────
@pytest.fixture
def cliente(store, monkeypatch):
    monkeypatch.setattr(mail.settings, "mail_dry_run", True)
    monkeypatch.setattr(cuotas, "configured", lambda: True)
    monkeypatch.setattr(cuotas, "estado_cuotas", lambda anio: {"socios": [SOCIA, BETO]})
    s = Sesion(sid="t", usuario="flor", nombre="Flor", rol="bibliotecaria",
               permisos=permisos.permisos_de("bibliotecaria"))
    app.dependency_overrides[get_session] = lambda: s
    app.dependency_overrides[get_current_username] = lambda: s.usuario
    yield TestClient(app)
    app.dependency_overrides.clear()


def _enviar(cliente, subject, body, vars_=None):
    res = cliente.post("/api/mail/send", json={
        "subject": subject, "body": body,
        "recipients": [{"email": "ana@x.org", "vars": vars_ or {"nombre": "Ana", "carnet": "0042", "meses_debe": "0"}}]}).json()
    return historial.get_run(res["run_id"])["destinatarios"][0]


def test_mails_completa_la_cuota_aunque_la_etiqueta_este_en_el_asunto(cliente):
    d = _enviar(cliente, "Debés {{ meses_debe }} meses", "Hola {{nombre}}")
    assert d["subject"] == "Debés 3 meses"


def test_mails_completa_el_ultimo_mes_pago(cliente):
    d = _enviar(cliente, "Cuota", "Tu último pago fue en {{ultimo_mes_pago}} y debés {{meses_impagos}}.")
    assert d["body"] == "Tu último pago fue en junio 2026 y debés Jul, Ago, Sep."


def test_la_pantalla_recibe_el_catalogo(cliente):
    d = cliente.get("/api/mail/etiquetas").json()
    assert [g["grupo"] for g in d["socio"]] == ["Datos del socio", "Libros del socio", "Cuota societaria"]
    assert [g["grupo"] for g in d["interno"]] == ["Préstamos", "Cuotas"]


def test_cada_etiqueta_trae_explicacion_y_ejemplo():
    for juego in etiquetas.catalogo().values():
        for g in juego:
            for i in g["items"]:
                assert i["descripcion"].strip() and i["ejemplo"].strip(), i["etiqueta"]
