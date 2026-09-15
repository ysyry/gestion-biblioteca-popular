"""Tests HTTP de espacios y solicitudes: quién ve qué y quién puede resolver."""
import pytest
from fastapi.testclient import TestClient

from app import agenda, cache, espacios, permisos
from app.auth import Sesion, get_current_username, get_session
from app.main import app


@pytest.fixture(autouse=True)
def sin_google(monkeypatch):
    """Los tests no salen a internet.

    Sin esto, /api/agenda se descarga los Google Calendar reales de la biblioteca:
    lento, dependiente de la red y con datos que cambian solos. Acá probamos lo que
    hace la app, no lo que hay cargado en Google.
    """
    monkeypatch.setattr(agenda, "configured", lambda: False)
    monkeypatch.setattr(agenda, "calendars", lambda: [])
    cache.invalidate("agenda")          # el caché es del proceso: no se cuela entre tests
    yield
    cache.invalidate("agenda")


@pytest.fixture
def como(store):
    store["espacios"] = []
    def _entrar(rol="bibliotecaria", usuario="quien", uid=None, subcomision=""):
        s = Sesion(sid="t", usuario=usuario, nombre=usuario.title(), rol=rol, uid=uid,
                   subcomision=subcomision, permisos=permisos.permisos_de(rol))
        app.dependency_overrides[get_session] = lambda: s
        app.dependency_overrides[get_current_username] = lambda: s.usuario
        return TestClient(app)
    yield _entrar
    app.dependency_overrides.clear()


@pytest.fixture
def sala(store):
    store["espacios"] = []
    return espacios.crear("Sala principal", capacidad=40)


def _pedido(sala, **kw):
    return {"titulo": "Taller de radio", "espacio_id": sala["id"],
            "inicio": "2026-03-10T18:00", "fin": "2026-03-10T20:00", **kw}


# ── Espacios ────────────────────────────────────────────────────────────────
def test_todos_ven_los_espacios_pero_no_todos_editan(como, sala):
    d = como("subcomision", subcomision="Prensa").get("/api/espacios").json()
    assert [e["nombre"] for e in d["items"]] == ["Sala principal"]
    assert d["puede_editar"] is False
    assert como("bibliotecaria").get("/api/espacios").json()["puede_editar"] is True


def test_la_subcomision_no_crea_ni_borra_espacios(como, sala):
    c = como("subcomision", subcomision="Prensa")
    assert c.post("/api/espacios", json={"nombre": "Patio"}).status_code == 403
    assert c.put(f"/api/espacios/{sala['id']}", json={"nombre": "X"}).status_code == 403
    assert c.delete(f"/api/espacios/{sala['id']}").status_code == 403


def test_crear_espacio_repetido_da_400(como, sala):
    r = como("bibliotecaria").post("/api/espacios", json={"nombre": "sala principal"})
    assert r.status_code == 400 and "Ya existe" in r.json()["detail"]


# ── Alta de solicitudes ─────────────────────────────────────────────────────
def test_una_subcomision_puede_pedir_espacio(como, sala):
    r = como("subcomision", usuario="prensa", uid="u1", subcomision="Prensa") \
        .post("/api/solicitudes", json=_pedido(sala))
    assert r.status_code == 200
    d = r.json()
    assert d["estado"] == "pendiente"
    assert d["espacio"] == "Sala principal"          # viene el nombre ya resuelto
    assert d["solicitante"]["subcomision"] == "Prensa"


def test_pedido_invalido_da_400(como, sala):
    r = como("bibliotecaria").post("/api/solicitudes", json=_pedido(sala, titulo=""))
    assert r.status_code == 400 and "actividad" in r.json()["detail"]


def test_aviso_de_superposicion_antes_de_pedir(como, sala):
    c = como("bibliotecaria")
    sid = c.post("/api/solicitudes", json=_pedido(sala)).json()["id"]
    c.post(f"/api/solicitudes/{sid}/resolver", json={"decision": "aprobar"})

    d = c.post("/api/solicitudes/conflictos", json={
        "espacio_id": sala["id"], "inicio": "2026-03-10T19:00", "fin": "2026-03-10T21:00"}).json()
    assert len(d["conflictos"]) == 1
    assert d["conflictos"][0]["titulo"] == "Taller de radio"


def test_conflictos_expande_la_repeticion(como, sala):
    d = como("subcomision", subcomision="Prensa").post("/api/solicitudes/conflictos", json={
        "espacio_id": sala["id"], "inicio": "2026-03-10T18:00", "fin": "2026-03-10T20:00",
        "repeticion": "semanal", "hasta": "2026-03-31"}).json()
    assert len(d["fechas"]) == 4 and d["conflictos"] == []


# ── Quién ve qué ────────────────────────────────────────────────────────────
def test_una_subcomision_solo_ve_las_suyas(como, sala):
    como("subcomision", usuario="prensa", uid="u1", subcomision="Prensa") \
        .post("/api/solicitudes", json=_pedido(sala, titulo="De prensa"))
    como("subcomision", usuario="cultura", uid="u2", subcomision="Cultura") \
        .post("/api/solicitudes", json=_pedido(sala, titulo="De cultura"))

    c = como("subcomision", usuario="prensa", uid="u1", subcomision="Prensa")
    d = c.get("/api/solicitudes").json()
    assert [s["titulo"] for s in d["items"]] == ["De prensa"]
    assert d["puede_resolver"] is False


def test_quien_resuelve_las_ve_todas(como, sala):
    como("subcomision", usuario="prensa", uid="u1", subcomision="Prensa") \
        .post("/api/solicitudes", json=_pedido(sala, titulo="De prensa"))
    d = como("comision").get("/api/solicitudes").json()
    assert [s["titulo"] for s in d["items"]] == ["De prensa"]
    assert d["puede_resolver"] is True and d["pendientes"] == 1


def test_la_solicitud_ajena_no_existe_para_mi(como, sala):
    """404, no 403: no le confirmamos a nadie que existe algo que no puede ver."""
    sid = como("subcomision", usuario="cultura", uid="u2", subcomision="Cultura") \
        .post("/api/solicitudes", json=_pedido(sala)).json()["id"]
    c = como("subcomision", usuario="prensa", uid="u1", subcomision="Prensa")
    assert c.get(f"/api/solicitudes/{sid}").status_code == 404
    assert c.post(f"/api/solicitudes/{sid}/cancelar", json={}).status_code == 404


def test_veo_las_de_mi_subcomision_aunque_las_pidiera_otra_persona(como, sala):
    sid = como("subcomision", usuario="ana", uid="u1", subcomision="Prensa") \
        .post("/api/solicitudes", json=_pedido(sala)).json()["id"]
    c = como("subcomision", usuario="beto", uid="u9", subcomision="Prensa")
    assert c.get(f"/api/solicitudes/{sid}").status_code == 200


# ── Resolución ──────────────────────────────────────────────────────────────
def test_la_subcomision_no_puede_resolver(como, sala):
    c = como("subcomision", usuario="prensa", uid="u1", subcomision="Prensa")
    sid = c.post("/api/solicitudes", json=_pedido(sala)).json()["id"]
    r = c.post(f"/api/solicitudes/{sid}/resolver", json={"decision": "aprobar"})
    assert r.status_code == 403, "una subcomisión no puede aprobarse sus propios pedidos"


def test_aprobar_ajustando_el_horario(como, sala):
    """El caso normal: se aprueba, pero movido."""
    sid = como("subcomision", usuario="prensa", uid="u1", subcomision="Prensa") \
        .post("/api/solicitudes", json=_pedido(sala)).json()["id"]
    d = como("bibliotecaria").post(f"/api/solicitudes/{sid}/resolver", json={
        "decision": "aprobar",
        "cambios": {"inicio": "2026-03-11T18:00", "fin": "2026-03-11T20:00"}}).json()
    assert d["estado"] == "aprobada"
    assert d["inicio"] == "2026-03-11T18:00"
    assert d["pedido_original"]["inicio"] == "2026-03-10T18:00"


def test_rechazar_sin_motivo_da_400(como, sala):
    sid = como("bibliotecaria").post("/api/solicitudes", json=_pedido(sala)).json()["id"]
    r = como("bibliotecaria").post(f"/api/solicitudes/{sid}/resolver",
                                   json={"decision": "rechazar"})
    assert r.status_code == 400 and "motivo" in r.json()["detail"]


def test_ciclo_observar_editar_aprobar(como, sala):
    quien = dict(rol="subcomision", usuario="prensa", uid="u1", subcomision="Prensa")
    sid = como(**quien).post("/api/solicitudes", json=_pedido(sala)).json()["id"]

    como("bibliotecaria").post(f"/api/solicitudes/{sid}/resolver",
                               json={"decision": "observar", "motivo": "¿Más temprano?"})
    assert como(**quien).get(f"/api/solicitudes/{sid}").json()["estado"] == "observaciones"

    d = como(**quien).put(f"/api/solicitudes/{sid}",
                          json=_pedido(sala, inicio="2026-03-10T16:00", fin="2026-03-10T18:00"))
    assert d.json()["estado"] == "pendiente"

    final = como("bibliotecaria").post(f"/api/solicitudes/{sid}/resolver",
                                       json={"decision": "aprobar"}).json()
    assert final["estado"] == "aprobada" and final["inicio"] == "2026-03-10T16:00"


def test_puedo_cancelar_la_mia(como, sala):
    quien = dict(rol="subcomision", usuario="prensa", uid="u1", subcomision="Prensa")
    sid = como(**quien).post("/api/solicitudes", json=_pedido(sala)).json()["id"]
    d = como(**quien).post(f"/api/solicitudes/{sid}/cancelar", json={"motivo": "Se suspendió"})
    assert d.json()["estado"] == "cancelada"


# ── El calendario las muestra ───────────────────────────────────────────────
def test_la_reserva_aprobada_aparece_en_el_calendario(como, sala):
    c = como("bibliotecaria")
    sid = c.post("/api/solicitudes", json=_pedido(sala)).json()["id"]
    c.post(f"/api/solicitudes/{sid}/resolver", json={"decision": "aprobar"})

    d = c.get("/api/agenda?desde=2026-03-01&dias=31").json()
    reservas = [e for e in d["events"] if e.get("solicitud_id")]
    assert len(reservas) == 1
    assert reservas[0]["titulo"] == "Taller de radio"
    assert reservas[0]["lugar"] == "Sala principal"


def test_lo_pendiente_no_aparece_en_el_calendario(como, sala):
    como("bibliotecaria").post("/api/solicitudes", json=_pedido(sala))
    d = como("bibliotecaria").get("/api/agenda?desde=2026-03-01&dias=31").json()
    assert [e for e in d["events"] if e.get("solicitud_id")] == []


def test_el_calendario_anda_aunque_google_no_este_configurado(como, sala):
    """Sin calendarios de Google, la app igual muestra sus propias reservas."""
    c = como("bibliotecaria")
    sid = c.post("/api/solicitudes", json=_pedido(sala)).json()["id"]
    c.post(f"/api/solicitudes/{sid}/resolver", json={"decision": "aprobar"})
    d = c.get("/api/agenda?desde=2026-03-01&dias=31").json()
    assert d["configured"] is True and len(d["events"]) == 1
