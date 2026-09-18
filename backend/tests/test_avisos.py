"""Tests del aviso por mail a quien pidió un espacio cuando se resuelve su pedido."""
import pytest
from fastapi.testclient import TestClient

from app import avisos, espacios, historial, permisos, solicitudes as sol, usuarios
from app.auth import Sesion, get_current_username, get_session
from app.config import settings
from app.main import app


@pytest.fixture(autouse=True)
def simulado(monkeypatch):
    """Los mails se simulan: ningún test manda uno de verdad."""
    monkeypatch.setattr(settings, "mail_dry_run", True)
    monkeypatch.setattr(settings, "app_public_url", "https://gestion.bibliotecabayer.org")


@pytest.fixture
def sala(store):
    store["espacios"] = []
    return espacios.crear("Sala principal", capacidad=40)


@pytest.fixture
def ana(store):
    """Una integrante de Prensa con usuario propio y mail cargado."""
    u, _ = usuarios.crear(usuario="ana", nombre="Ana Paz", rol="subcomision",
                          email="ana@correo.org", subcomision="Prensa")
    return u


def _de(u):
    return {"uid": u["id"], "usuario": u["usuario"], "nombre": u["nombre"],
            "subcomision": u["subcomision"]}


def _pedido(sala, **kw):
    return {"titulo": "Taller de radio", "espacio_id": sala["id"],
            "inicio": "2026-03-10T18:00", "fin": "2026-03-10T20:00", **kw}


# ── Qué dice cada aviso ─────────────────────────────────────────────────────
def test_aprobado_dice_donde_y_cuando(sala, ana):
    s = sol.resolver(sol.crear(_pedido(sala), _de(ana))["id"], "aprobar", por="flor")
    asunto, cuerpo = avisos.armar(s, "aprobar")
    assert asunto == "Aprobado: Taller de radio"
    assert "Hola {{nombre}}" in cuerpo
    assert "Sala principal" in cuerpo
    assert "martes 10 de marzo, de 18:00 a 20:00" in cuerpo
    assert "no es exactamente lo que habías pedido" not in cuerpo
    assert "https://gestion.bibliotecabayer.org" in cuerpo


def test_aprobado_con_cambios_muestra_lo_que_se_habia_pedido(sala, ana):
    s = sol.crear(_pedido(sala), _de(ana))
    s = sol.resolver(s["id"], "aprobar", por="flor",
                     cambios={"inicio": "2026-03-11T17:00", "fin": "2026-03-11T19:00"})
    _, cuerpo = avisos.armar(s, "aprobar")
    assert "miércoles 11 de marzo, de 17:00 a 19:00" in cuerpo       # lo que quedó
    assert "Habías pedido Sala principal, el martes 10 de marzo" in cuerpo


def test_aprobado_que_se_repite_dice_hasta_cuando(sala, ana):
    s = sol.crear(_pedido(sala, repeticion="semanal", hasta="2026-03-31"), _de(ana))
    s = sol.resolver(s["id"], "aprobar", por="flor")
    _, cuerpo = avisos.armar(s, "aprobar")
    assert "todas las semanas, 4 fechas hasta el 31 de marzo" in cuerpo


def test_rechazo_y_observaciones_llevan_el_motivo(sala, ana):
    s = sol.crear(_pedido(sala), _de(ana))
    r = sol.resolver(s["id"], "observar", por="flor", motivo="¿Puede ser más temprano?")
    asunto, cuerpo = avisos.armar(r, "observar")
    assert asunto.startswith("Tu pedido necesita cambios")
    assert "¿Puede ser más temprano?" in cuerpo

    r = sol.resolver(s["id"], "rechazar", por="flor", motivo="Ese día está cerrado")
    asunto, cuerpo = avisos.armar(r, "rechazar")
    assert asunto == "No se pudo aprobar: Taller de radio"
    assert "Motivo: Ese día está cerrado" in cuerpo


def test_cancelada_lleva_el_motivo(sala, ana):
    s = sol.crear(_pedido(sala), _de(ana))
    sol.resolver(s["id"], "aprobar", por="flor")
    c = sol.cancelar(s["id"], por="flor", motivo="Se inunda la sala")
    asunto, cuerpo = avisos.armar(c, "cancelar")
    assert asunto == "Reserva cancelada: Taller de radio"
    assert "Motivo: Se inunda la sala" in cuerpo


# ── A quién y cuándo ────────────────────────────────────────────────────────
async def test_se_manda_y_queda_anotado(store, sala, ana):
    s = sol.crear(_pedido(sala), _de(ana))
    sol.resolver(s["id"], "aprobar", por="flor")
    await avisos.avisar(s["id"], "aprobar", por="flor", por_uid=None)

    aviso = sol.obtener(s["id"])["avisos"][-1]
    assert aviso["que"] == "aprobar"
    assert aviso["a"] == "ana@correo.org"
    assert aviso["estado"] == "simulado"                  # MAIL_DRY_RUN
    envio = historial.listar(origen="avisos")[0]
    assert envio["titulo"] == "Aprobado: Taller de radio"
    assert envio["usuario"] == "flor"


async def test_no_se_avisa_a_quien_lo_hizo_ella_misma(sala, ana):
    s = sol.crear(_pedido(sala), _de(ana))
    sol.cancelar(s["id"], por="ana")
    await avisos.avisar(s["id"], "cancelar", por="ana", por_uid=ana["id"])
    assert sol.obtener(s["id"])["avisos"] == []


async def test_sin_mail_cargado_queda_anotado_que_no_se_pudo(store, sala):
    u, _ = usuarios.crear(usuario="beto", nombre="Beto", rol="subcomision", subcomision="Huerta")
    s = sol.crear(_pedido(sala), _de(u))
    sol.resolver(s["id"], "rechazar", por="flor", motivo="No hay lugar")
    await avisos.avisar(s["id"], "rechazar", por="flor", por_uid=None)
    aviso = sol.obtener(s["id"])["avisos"][-1]
    assert aviso["estado"] == "sin_email"
    assert historial.listar(origen="avisos") == []


async def test_lo_pedido_por_una_bibliotecaria_no_se_avisa(sala):
    s = sol.crear(_pedido(sala), {"uid": None, "usuario": "flor", "nombre": "Flor"})
    sol.resolver(s["id"], "aprobar", por="caro")
    await avisos.avisar(s["id"], "aprobar", por="caro", por_uid=None)
    assert sol.obtener(s["id"])["avisos"] == []


# ── Desde la app ────────────────────────────────────────────────────────────
@pytest.fixture
def como(store):
    def _entrar(rol="bibliotecaria", usuario="flor", uid=None, subcomision=""):
        s = Sesion(sid="t", usuario=usuario, nombre=usuario.title(), rol=rol, uid=uid,
                   subcomision=subcomision, permisos=permisos.permisos_de(rol))
        app.dependency_overrides[get_session] = lambda: s
        app.dependency_overrides[get_current_username] = lambda: s.usuario
        return TestClient(app)
    yield _entrar
    app.dependency_overrides.clear()


def test_al_resolver_desde_la_app_le_llega_el_aviso(como, sala, ana):
    pedida = como("subcomision", usuario="ana", uid=ana["id"], subcomision="Prensa") \
        .post("/api/solicitudes", json=_pedido(sala)).json()
    r = como("bibliotecaria").post(f"/api/solicitudes/{pedida['id']}/resolver",
                                   json={"decision": "aprobar"})
    assert r.status_code == 200
    avisos_ = sol.obtener(pedida["id"])["avisos"]
    assert [(a["que"], a["a"]) for a in avisos_] == [("aprobar", "ana@correo.org")]


def test_cancelar_lo_propio_desde_la_app_no_manda_nada(como, sala, ana):
    c = como("subcomision", usuario="ana", uid=ana["id"], subcomision="Prensa")
    pedida = c.post("/api/solicitudes", json=_pedido(sala)).json()
    assert c.post(f"/api/solicitudes/{pedida['id']}/cancelar", json={}).status_code == 200
    assert sol.obtener(pedida["id"])["avisos"] == []


def test_los_avisos_se_filtran_en_el_historial_de_envios(como, sala, ana):
    pedida = como("subcomision", usuario="ana", uid=ana["id"], subcomision="Prensa") \
        .post("/api/solicitudes", json=_pedido(sala)).json()
    c = como("bibliotecaria")
    c.post(f"/api/solicitudes/{pedida['id']}/resolver", json={"decision": "aprobar"})
    r = c.get("/api/envios?origen=avisos")
    assert r.status_code == 200
    assert [e["titulo"] for e in r.json()["items"]] == ["Aprobado: Taller de radio"]


def test_la_bandeja_dice_a_que_calendario_va_lo_aprobado(como, sala):
    d = como("bibliotecaria").get("/api/solicitudes").json()
    assert d["google"]["calendario"] == "Bayer Band"
    assert como("subcomision", uid="u9", subcomision="Prensa") \
        .get("/api/solicitudes").json()["google"] is None
