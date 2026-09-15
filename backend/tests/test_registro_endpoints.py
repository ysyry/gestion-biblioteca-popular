"""Tests HTTP del registro: el formulario público (sin usuario) y la bandeja."""
import pytest
from fastapi.testclient import TestClient

from app import permisos, registro
from app.api import routes
from app.auth import Sesion, get_current_username, get_session
from app.main import app


ACTIVIDAD = {"titulo": "Charla sobre huerta", "tipo": "charla", "fecha": "2026-09-10",
             "hora_inicio": "18:00", "personas_total": 24,
             "quien_completa": {"nombre": "Caro", "contacto": "caro@ejemplo.ar"}}
TALLER = {"titulo": "Taller de radio", "mes": "2026-08", "encuentros": 4,
          "participantes": 12, "quien_completa": {"nombre": "Caro"}}


@pytest.fixture
def cliente(store):
    """Cliente sin sesión: sirve para la parte pública."""
    routes._ENVIOS_POR_IP.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()
    routes._ENVIOS_POR_IP.clear()


@pytest.fixture
def como(cliente):
    def _entrar(rol="bibliotecaria", usuario="laura"):
        s = Sesion(sid="t", usuario=usuario, nombre=usuario.title(), rol=rol,
                   permisos=permisos.permisos_de(rol))
        app.dependency_overrides[get_session] = lambda: s
        app.dependency_overrides[get_current_username] = lambda: s.usuario
        return cliente
    return _entrar


def _link(clase="general", **kw):
    return registro.crear_link({"clase": clase, "titulo": kw.pop("titulo", "Un link"), **kw}, "Laura")


# ── Formulario público ──────────────────────────────────────────────────────
def test_el_formulario_publico_no_pide_usuario(cliente):
    l = _link()
    d = cliente.get(f"/api/publico/registro/{l['token']}").json()
    assert d["clase"] == "actividad" and "tipos" in d["catalogos"]
    r = cliente.post(f"/api/publico/registro/{l['token']}", json={"datos": ACTIVIDAD})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["resumen"] == {"titulo": "Charla sobre huerta", "cuando": "2026-09-10", "personas": 24}
    guardado = registro.listar()[0]
    assert guardado["estado"] == registro.RECIBIDO and guardado["link_id"] == l["id"]


def test_el_link_de_taller_pide_el_resumen_mensual(cliente):
    l = _link("taller", titulo="Taller de radio", precarga={"titulo": "Taller de radio"})
    assert cliente.get(f"/api/publico/registro/{l['token']}").json()["clase"] == "taller"
    r = cliente.post(f"/api/publico/registro/{l['token']}", json={"datos": TALLER})
    assert r.status_code == 200
    assert registro.listar()[0]["clase"] == "taller"


def test_el_link_de_una_actividad_precarga_y_se_cierra_solo(cliente):
    l = _link("actividad", titulo="Charla de huerta",
              precarga={"titulo": "Charla de huerta", "fecha": "2026-09-10", "tipo": "charla",
                        "hora_inicio": "18:00"})
    d = cliente.get(f"/api/publico/registro/{l['token']}").json()
    assert d["precarga"]["titulo"] == "Charla de huerta"
    # Alcanza con completar lo que falta: lo precargado se combina en el servidor.
    r = cliente.post(f"/api/publico/registro/{l['token']}",
                     json={"datos": {"personas_total": 30, "quien_completa": {"nombre": "Caro"}}})
    assert r.status_code == 200
    assert registro.listar()[0]["datos"]["titulo"] == "Charla de huerta"
    assert cliente.get(f"/api/publico/registro/{l['token']}").status_code == 410     # ya se usó


def test_token_invalido_o_cerrado(cliente):
    assert cliente.get("/api/publico/registro/nada").status_code == 404
    l = _link()
    registro.actualizar_link(l["id"], {"abierto": False})
    assert cliente.get(f"/api/publico/registro/{l['token']}").status_code == 410
    assert cliente.post(f"/api/publico/registro/{l['token']}", json={"datos": ACTIVIDAD}).status_code == 410


def test_datos_incompletos_dan_400_con_el_motivo(cliente):
    l = _link()
    r = cliente.post(f"/api/publico/registro/{l['token']}", json={"datos": {"titulo": "Algo"}})
    assert r.status_code == 400 and "fecha" in r.json()["detail"]
    assert registro.listar() == []


def test_el_campo_trampa_descarta_al_robot(cliente):
    l = _link()
    r = cliente.post(f"/api/publico/registro/{l['token']}",
                     json={"datos": ACTIVIDAD, "website": "http://spam"})
    assert r.status_code == 200 and registro.listar() == []      # no se guarda nada


def test_tope_de_envios_por_conexion(cliente):
    l = _link()
    for _ in range(routes.TOPE_ENVIOS):
        assert cliente.post(f"/api/publico/registro/{l['token']}", json={"datos": ACTIVIDAD}).status_code == 200
    r = cliente.post(f"/api/publico/registro/{l['token']}", json={"datos": ACTIVIDAD})
    assert r.status_code == 429 and "muchos formularios" in r.json()["detail"]


def test_la_pagina_publica_se_sirve_con_cualquier_codigo(cliente):
    r = cliente.get("/registro/lo-que-sea")
    assert r.status_code == 200 and "Registro de actividades" in r.text
    assert "publico/registro" in r.text          # la validación del código la hace la API


# ── Bandeja de la biblioteca ────────────────────────────────────────────────
def test_las_subcomisiones_no_entran_a_la_bandeja(como):
    c = como("subcomision")
    assert c.get("/api/registro").status_code == 403
    assert c.get("/api/registro/links").status_code == 403
    assert c.post("/api/registro/links", json={"clase": "general"}).status_code == 403


def test_crear_link_y_compartirlo(como, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "app_public_url", "https://biblioteca.ejemplo.ar")
    c = como()
    l = c.post("/api/registro/links", json={"clase": "general"}).json()
    assert l["url"] == f"https://biblioteca.ejemplo.ar/registro/{l['token']}"
    d = c.get("/api/registro/links").json()
    assert d["publico_configurado"] is True and len(d["items"]) == 1
    assert c.put(f"/api/registro/links/{l['id']}", json={"abierto": False}).json()["abierto"] is False
    assert c.delete(f"/api/registro/links/{l['id']}").json() == {"ok": True}
    assert c.get("/api/registro/links").json()["items"] == []


def test_bandeja_validar_y_descartar(como, cliente):
    l = _link()
    cliente.post(f"/api/publico/registro/{l['token']}", json={"datos": ACTIVIDAD})
    c = como()
    d = c.get("/api/registro").json()
    assert d["pendientes"] == 1 and d["items"][0]["estado"] == "recibido"
    rid = d["items"][0]["id"]

    assert c.put(f"/api/registro/{rid}", json={"datos": {"personas_total": 28}}).json()["datos"]["personas_total"] == 28
    r = c.post(f"/api/registro/{rid}/resolver", json={"estado": "validado"}).json()
    assert r["estado"] == "validado" and r["validado_por"] == "Laura"
    assert c.get("/api/registro").json()["pendientes"] == 0
    assert c.post(f"/api/registro/{rid}/resolver", json={"estado": "descartado"}).status_code == 400
    assert c.post(f"/api/registro/{rid}/resolver", json={"estado": "descartado", "motivo": "duplicado"}).status_code == 200


def test_carga_directa_desde_la_app_queda_validada(como):
    c = como()
    r = c.post("/api/registro", json={"clase": "actividad", "datos": ACTIVIDAD}).json()
    assert r["estado"] == "validado"
    assert c.get("/api/registro?estado=validado").json()["items"][0]["id"] == r["id"]
    assert c.get(f"/api/registro/{r['id']}").json()["espacio_nombre"] == ""
    assert c.delete(f"/api/registro/{r['id']}").json() == {"ok": True}
    assert c.get(f"/api/registro/{r['id']}").status_code == 404


def test_incidentes_a_la_vista_en_la_bandeja(como):
    c = como()
    c.post("/api/registro", json={"clase": "actividad",
                                  "datos": {**ACTIVIDAD, "incidente": "Se rompió el proyector",
                                            "incidente_atencion": True}})
    d = c.get("/api/registro").json()
    assert len(d["atencion"]) == 1 and "proyector" in d["atencion"][0]["datos"]["incidente"]["texto"]


def test_export_csv(como):
    c = como()
    c.post("/api/registro", json={"clase": "actividad", "datos": ACTIVIDAD})
    r = c.get("/api/registro/export.csv")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert "Charla sobre huerta" in r.text and r.text.startswith("﻿")   # BOM: Excel lo abre bien
