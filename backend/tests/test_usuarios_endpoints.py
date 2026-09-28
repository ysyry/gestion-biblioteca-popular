"""Tests HTTP del ABM de usuarios y de las guardas de permiso por rol."""
import pytest
from fastapi.testclient import TestClient

from app import permisos, usuarios
from app.auth import Sesion, get_current_username, get_session
from app.main import app


def _sesion(rol, usuario="quien", uid=None, subcomision=""):
    return Sesion(sid="test", usuario=usuario, nombre=usuario.title(), rol=rol, uid=uid,
                  subcomision=subcomision, permisos=permisos.permisos_de(rol))


@pytest.fixture
def como(store):
    """Devuelve una función que arma el cliente HTTP con el rol que se le pida."""
    def _entrar(rol="bibliotecaria", **kw):
        s = _sesion(rol, **kw)
        app.dependency_overrides[get_session] = lambda: s
        app.dependency_overrides[get_current_username] = lambda: s.usuario
        return TestClient(app)
    yield _entrar
    app.dependency_overrides.clear()


# ── /api/me ─────────────────────────────────────────────────────────────────
def test_me_trae_rol_permisos_y_secciones(como):
    r = como("comision", usuario="tesoreria").get("/api/me")
    assert r.status_code == 200
    d = r.json()
    assert d["rol"] == "comision"
    assert d["rol_etiqueta"] == "Comisión Directiva"
    assert permisos.KOHA in d["permisos"]
    assert [s["id"] for s in d["secciones"]][0] == "stats"


def test_me_de_subcomision_ve_lo_institucional(como):
    d = como("subcomision", subcomision="Prensa").get("/api/me").json()
    assert [s["id"] for s in d["secciones"]] == ["actualidad", "agenda", "solicitudes", "mias"]
    assert d["subcomision"] == "Prensa"
    # Nada de socios, cuotas, mails ni usuarios; de los registros, solo los suyos
    assert [e["titulo"] for e in d["menu"]] == ["Lo que anda pasando", "Agenda"]


# ── ABM ─────────────────────────────────────────────────────────────────────
def test_crear_y_listar(como):
    c = como("bibliotecaria")
    r = c.post("/api/usuarios", json={"usuario": "prensa", "nombre": "Ana",
                                      "rol": "subcomision", "subcomision": "Prensa"})
    assert r.status_code == 200
    assert r.json()["password"]                       # se dicta una sola vez
    assert "password" not in r.json()["usuario"]

    d = c.get("/api/usuarios").json()
    assert [u["usuario"] for u in d["items"]] == ["prensa"]
    assert "Prensa" in d["subcomisiones"]
    assert {r["id"] for r in d["roles"]} == set(permisos.ROLES)


def test_crear_con_datos_malos_da_400(como):
    r = como("bibliotecaria").post("/api/usuarios",
                                   json={"usuario": "x", "nombre": "X", "rol": "reina"})
    assert r.status_code == 400
    assert "Rol desconocido" in r.json()["detail"]


def test_editar_y_resetear(como):
    c = como("bibliotecaria")
    uid = c.post("/api/usuarios", json={"usuario": "cd", "nombre": "Ana",
                                        "rol": "comision"}).json()["usuario"]["id"]

    assert c.put(f"/api/usuarios/{uid}", json={"nombre": "Ana Paz"}).json()["nombre"] == "Ana Paz"

    nueva = c.post(f"/api/usuarios/{uid}/password", json={}).json()["password"]
    assert usuarios.verificar("cd", nueva)

    assert c.delete(f"/api/usuarios/{uid}").status_code == 200
    assert c.get("/api/usuarios").json()["items"] == []


def test_no_puedo_desactivarme_ni_borrarme(como):
    c = como("bibliotecaria")
    uid = c.post("/api/usuarios", json={"usuario": "yo", "nombre": "Yo",
                                        "rol": "comision"}).json()["usuario"]["id"]
    c = como("comision", usuario="yo", uid=uid)   # ahora entro como esa persona
    assert c.put(f"/api/usuarios/{uid}", json={"activo": False}).status_code == 400
    assert c.delete(f"/api/usuarios/{uid}").status_code == 400


def test_cambiar_la_propia_password(como):
    c = como("bibliotecaria")
    hecho = c.post("/api/usuarios", json={"usuario": "cd", "nombre": "Ana", "rol": "comision",
                                          "password": "clave-larga-1"}).json()
    c = como("comision", usuario="cd", uid=hecho["usuario"]["id"])
    assert c.post("/api/password", json={"actual": "clave-larga-1",
                                         "nueva": "clave-larga-2"}).status_code == 200
    assert usuarios.verificar("cd", "clave-larga-2")


def test_la_bibliotecaria_cambia_su_clave_en_koha(como):
    """Su contraseña es la de Koha: la app no la puede tocar y lo dice claro."""
    r = como("bibliotecaria").post("/api/password", json={"actual": "a", "nueva": "b" * 9})
    assert r.status_code == 400
    assert "Koha" in r.json()["detail"]


# ── Guardas de permiso ──────────────────────────────────────────────────────
@pytest.mark.parametrize("metodo,ruta", [
    ("get", "/api/usuarios"),
    ("post", "/api/usuarios"),
    ("get", "/api/mail/config"),
    ("get", "/api/envios"),
    ("get", "/api/auto/config"),
    ("get", "/api/cuotas"),
    ("get", "/api/cuotas/pagos"),
])
def test_subcomision_no_pasa(como, metodo, ruta):
    c = como("subcomision", subcomision="Prensa")
    r = getattr(c, metodo)(ruta, **({"json": {}} if metodo == "post" else {}))
    assert r.status_code == 403, f"{metodo.upper()} {ruta} debería dar 403"
    assert "permiso" in r.json()["detail"]


def test_subcomision_si_pasa_a_la_agenda(como):
    """No está configurada en los tests, pero el permiso la deja entrar (no da 403)."""
    r = como("subcomision", subcomision="Prensa").get("/api/agenda")
    assert r.status_code != 403


def test_sin_token_da_401():
    assert TestClient(app).get("/api/usuarios").status_code == 401


# ── Sesiones abiertas ───────────────────────────────────────────────────────
def _login_real(usuario, clave):
    """Entra de verdad (sin overrides), como lo haría la pantalla."""
    c = TestClient(app)
    tok = c.post("/api/auth/login", json={"username": usuario, "password": clave}).json()
    c.headers["Authorization"] = f"Bearer {tok['access_token']}"
    return c


@pytest.mark.parametrize("sacar", [
    lambda uid: usuarios.actualizar(uid, {"activo": False}),
    lambda uid: usuarios.borrar(uid),
], ids=["desactivado", "borrado"])
def test_sacarle_el_acceso_corta_la_sesion_abierta(store, sacar):
    u, _ = usuarios.crear(usuario="pedro", nombre="Pedro", rol="comision", password="clave-pedro")
    c = _login_real("pedro", "clave-pedro")
    assert c.get("/api/me").status_code == 200
    sacar(u["id"])
    assert c.get("/api/me").status_code == 401


def test_un_cambio_de_rol_vale_en_la_sesion_abierta(store):
    u, _ = usuarios.crear(usuario="pedro", nombre="Pedro", rol="comision", password="clave-pedro")
    c = _login_real("pedro", "clave-pedro")
    assert c.get("/api/usuarios").status_code == 200
    usuarios.actualizar(u["id"], {"rol": "subcomision", "subcomision": "Prensa"})
    assert c.get("/api/usuarios").status_code == 403
    assert c.get("/api/me").json()["subcomision"] == "Prensa"


# ── Nadie da más de lo que tiene ────────────────────────────────────────────
def test_la_comision_no_crea_ni_toca_bibliotecarias(como, store):
    otra, _ = usuarios.crear(usuario="equipo", nombre="Equipo", rol="bibliotecaria")
    c = como("comision", usuario="tesoreria")
    assert {r["id"] for r in c.get("/api/usuarios").json()["roles"]} == {"comision", "subcomision"}
    assert c.post("/api/usuarios", json={"usuario": "colada", "nombre": "Colada",
                                         "rol": "bibliotecaria"}).status_code == 403
    assert c.post("/api/usuarios", json={"usuario": "prensa", "nombre": "Prensa",
                                         "rol": "subcomision", "subcomision": "Prensa"}).status_code == 200
    prensa = next(u for u in usuarios.listar() if u["usuario"] == "prensa")
    assert c.put(f"/api/usuarios/{prensa['id']}", json={"rol": "bibliotecaria"}).status_code == 403
    assert c.put(f"/api/usuarios/{otra['id']}", json={"nombre": "X"}).status_code == 403
    assert c.post(f"/api/usuarios/{otra['id']}/password").status_code == 403
    assert c.delete(f"/api/usuarios/{otra['id']}").status_code == 403
