"""Tests de las subcomisiones en el registro: de quién es cada actividad y quién ve qué."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import panorama, permisos, registro, usuarios
from app.api import routes
from app.auth import Sesion, get_current_username, get_session
from app.main import app

HOY = date(2026, 9, 16)


@pytest.fixture
def subcomisiones(store):
    """Dos subcomisiones con usuario: así la app las conoce por su nombre."""
    usuarios.crear(usuario="prensa", nombre="Ana", rol="subcomision", subcomision="Prensa")
    usuarios.crear(usuario="huerta", nombre="Beto", rol="subcomision", subcomision="Huerta")


def _act(fecha="2026-09-10", personas=20, titulo="Charla", **kw):
    datos = {"titulo": titulo, "tipo": "charla", "fecha": fecha, "hora_inicio": "18:00",
             "personas_total": personas, "quien_completa": {"nombre": "Caro"}, **kw}
    return registro.guardar("actividad", datos, cargado_por="Laura")


def _taller(mes="2026-08", participantes=12, **kw):
    datos = {"titulo": "Taller de radio", "mes": mes, "encuentros": 4,
             "participantes": participantes, "quien_completa": {"nombre": "Caro"}, **kw}
    return registro.guardar("taller", datos, cargado_por="Laura")


# ── De quién es cada registro ───────────────────────────────────────────────
def test_la_subcomision_se_guarda_con_el_nombre_que_conoce_la_app(subcomisiones):
    r = _act(organiza="subcomision", subcomision="  prensa ")
    assert r["datos"]["subcomision"] == "Prensa"
    assert r["datos"]["organiza"] == "subcomision"


def test_con_subcomision_y_sin_decir_quien_organiza_la_organizo_ella(subcomisiones):
    assert _act(subcomision="Huerta")["datos"]["organiza"] == "subcomision"


def test_si_la_organizo_la_biblioteca_no_queda_subcomision(subcomisiones):
    r = _act(organiza="subcomision", subcomision="Prensa")
    r = registro.corregir(r["id"], {"organiza": "biblioteca"}, por="Laura")
    assert r["datos"]["subcomision"] == ""


def test_una_subcomision_sin_usuarios_se_guarda_igual(subcomisiones):
    assert _act(subcomision="Cine club")["datos"]["subcomision"] == "Cine club"


def test_el_taller_tambien_dice_de_que_subcomision_es(subcomisiones):
    assert _taller(subcomision="huerta")["datos"]["subcomision"] == "Huerta"


def test_el_link_de_una_reserva_de_subcomision_viene_precargado(subcomisiones):
    l = registro.crear_link({"clase": "actividad", "titulo": "Radio abierta",
                             "precarga": {"titulo": "Radio abierta", "subcomision": "prensa"}}, "Laura")
    assert l["precarga"]["subcomision"] == "Prensa"
    assert l["precarga"]["organiza"] == "subcomision"


def test_el_formulario_publico_ofrece_la_lista_de_subcomisiones(subcomisiones):
    assert registro.catalogos()["subcomisiones"] == ["Huerta", "Prensa"]


def test_el_csv_lleva_la_subcomision(subcomisiones):
    csv = registro.exportar_csv([_act(subcomision="Prensa")])
    encabezado, fila = csv.splitlines()[:2]
    assert "subcomision" in encabezado.split(",") and "Prensa" in fila.split(",")


# ── Lo que ve cada subcomisión ──────────────────────────────────────────────
def test_cada_subcomision_ve_solo_lo_suyo_y_no_lo_descartado(subcomisiones):
    suya = _act(titulo="Radio abierta", subcomision="Prensa")
    _act(titulo="Siembra", subcomision="Huerta")
    _act(titulo="De la biblioteca")
    descartada = _act(titulo="Repetida", subcomision="Prensa")
    registro.resolver(descartada["id"], registro.DESCARTADO, "Laura", "Estaba dos veces")
    assert [r["id"] for r in registro.de_subcomision("prensa")] == [suya["id"]]


@pytest.fixture
def como(store):
    routes._ENVIOS_POR_IP.clear()

    def _entrar(rol="bibliotecaria", usuario="laura", subcomision=""):
        s = Sesion(sid="t", usuario=usuario, nombre=usuario.title(), rol=rol,
                   uid="u1" if rol != "bibliotecaria" else None, subcomision=subcomision,
                   permisos=permisos.permisos_de(rol))
        app.dependency_overrides[get_session] = lambda: s
        app.dependency_overrides[get_current_username] = lambda: s.usuario
        return TestClient(app)
    yield _entrar
    app.dependency_overrides.clear()


def test_nuestras_actividades_muestra_lo_suyo_sin_la_cocina_de_la_bandeja(como, subcomisiones):
    r = _act(titulo="Radio abierta", subcomision="Prensa")
    registro.corregir(r["id"], {"personas_total": 25}, por="Laura")
    _act(titulo="Siembra", subcomision="Huerta")

    d = como("subcomision", "ana", "Prensa").get("/api/registro/mios").json()
    assert d["subcomision"] == "Prensa"
    assert [x["datos"]["titulo"] for x in d["items"]] == ["Radio abierta"]
    item = d["items"][0]
    assert item["corregido"] is True
    for interno in ("historial", "posibles_duplicados", "link_id", "validado_por"):
        assert interno not in item


def test_sin_subcomision_asignada_no_ve_ningun_registro(como, subcomisiones):
    _act(subcomision="Prensa")
    assert como("subcomision", "suelta", "").get("/api/registro/mios").json()["items"] == []


def test_una_subcomision_no_entra_a_la_bandeja_completa(como, subcomisiones):
    c = como("subcomision", "ana", "Prensa")
    assert c.get("/api/registro").status_code == 403
    assert c.get("/api/registro/export.csv").status_code == 403


# ── Los números de cada subcomisión ─────────────────────────────────────────
def test_el_tablero_cuenta_solo_lo_de_la_subcomision(subcomisiones):
    _act(personas=30, subcomision="Prensa")
    _act(personas=10, subcomision="Huerta")
    _act(personas=50)
    todo = panorama.armar("mes", HOY)
    prensa = panorama.armar("mes", HOY, subcomision="Prensa")
    assert todo["numeros"]["personas"]["valor"] == 90
    assert prensa["numeros"]["personas"]["valor"] == 30
    assert prensa["subcomision"] == "Prensa"


def test_una_subcomision_mira_toda_la_biblioteca_o_lo_suyo(como, subcomisiones):
    c = como("subcomision", "ana", "Prensa")
    d = c.get("/api/panorama?periodo=mes").json()
    assert d["subcomisiones"] == ["Prensa"]
    assert c.get("/api/panorama?periodo=mes&subcomision=prensa").status_code == 200
    assert c.get("/api/panorama?periodo=mes&subcomision=Huerta").status_code == 403


def test_la_biblioteca_puede_mirar_cualquier_subcomision(como, subcomisiones):
    c = como("bibliotecaria")
    d = c.get("/api/panorama?periodo=mes&subcomision=Huerta").json()
    assert d["subcomision"] == "Huerta"
    assert d["subcomisiones"] == ["Huerta", "Prensa"]
