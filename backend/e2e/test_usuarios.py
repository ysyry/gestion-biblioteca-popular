"""Usuarios de la app, contraseñas, permisos por rol y sesión.

Las bibliotecarias entran con Koha; la comisión directiva y las subcomisiones, con un
usuario propio que da de alta quien tiene `usuarios.admin`. Acá se recorre ese ciclo
entero desde la pantalla (alta, la contraseña que se ve una sola vez, editar,
desactivar, resetear, borrar) y, para cada rol, qué ve en el menú y qué le contesta la
API cuando pide algo que no le toca. El menú se compara con `app/permisos.py`: si
cambia la tabla de roles, estos tests dicen qué tiene que ver cada uno.
"""
from __future__ import annotations

import datetime as dt
import re
from contextlib import contextmanager

import jwt
import pytest
from playwright.sync_api import Browser, Page, expect

from app import auth, permisos
from app.config import settings

from . import servidor
from .ayudas import crear_usuario, entrar, ir_a


# ── Ayudas de este archivo ──────────────────────────────────────────────────
def _vigilar(page: Page) -> list[str]:
    """Lo mismo que hace el fixture `page`: junta los errores de JavaScript."""
    errores: list[str] = []
    page.on("pageerror", lambda e: errores.append(str(e)))
    page.on("console", lambda m: errores.append(m.text) if m.type == "error"
            and "Failed to load resource" not in m.text else None)
    return errores


@contextmanager
def _otra_persona(browser: Browser, base_url: str):
    """Otra ventana, con su propia sesión (otro navegador, en la práctica)."""
    contexto = browser.new_context(base_url=base_url)
    pagina = contexto.new_page()
    errores = _vigilar(pagina)
    try:
        yield pagina
    finally:
        contexto.close()
    assert not errores, "Errores de JavaScript en la otra ventana:\n" + "\n".join(errores)


def _intentar_entrar(page: Page, usuario: str, clave: str) -> None:
    page.goto("/")
    page.locator("#u").fill(usuario)
    page.locator("#p").fill(clave)
    page.locator("#loginBtn").click()


def _no_entra(page: Page, usuario: str, clave: str, motivo: str = "incorrect") -> None:
    _intentar_entrar(page, usuario, clave)
    expect(page.locator("#loginMsg")).to_contain_text(motivo)
    expect(page.locator("#appView")).to_be_hidden()


def _token(page: Page) -> str:
    return page.evaluate("localStorage.getItem('token')") or ""


def _api(page: Page, metodo: str, ruta: str, datos: dict | None = None):
    """Llama a la API con el token de quien está adentro en esa página."""
    return page.request.fetch(ruta, method=metodo, data=datos,
                              headers={"Authorization": f"Bearer {_token(page)}"})


def _secciones(page: Page) -> list[str]:
    return page.locator("#tabs button[data-tab]").evaluate_all(
        "bs => bs.map(b => b.dataset.tab)")


def _grupos(page: Page) -> list[str]:
    return page.locator("#tabs .tab-grupo").evaluate_all("gs => gs.map(g => g.dataset.grupo)")


def _fila(page: Page, usuario: str):
    return page.locator("#usrLista tbody tr", has=page.locator("code", has_text=re.compile(
        rf"^{re.escape(usuario)}$")))


def _abrir_usuarios(page: Page) -> None:
    ir_a(page, "usuarios")
    expect(page.locator("#pageTitle")).to_have_text("Usuarios de la app")
    expect(page.locator("#usrLista")).not_to_contain_text("Cargando")


def _completar_alta(page: Page, *, nombre: str, usuario: str, rol: str,
                    subcomision: str = "", clave: str = "") -> None:
    page.locator("#usrNuevo").click()
    expect(page.locator("#usrForm h3")).to_have_text("Nuevo usuario")
    page.locator("#usrNombre").fill(nombre)
    page.locator("#usrUsuario").fill(usuario)
    page.locator("#usrRol").select_option(rol)
    page.locator("#usrSub").fill(subcomision)
    page.locator("#usrPass").fill(clave)
    page.locator("#usrGuardar").click()


def _leer_clave_y_cerrar(page: Page, nombre: str, usuario: str) -> str:
    """El cartel con la contraseña: la lee y lo cierra con "Listo"."""
    modal = page.locator(".modal", has=page.locator("#claveTxt"))
    expect(modal.locator("h3")).to_have_text(f"Contraseña de {nombre}")
    expect(modal).to_contain_text("esta es la única vez que se ve")
    expect(modal).to_contain_text(f"Entra con el usuario {usuario}")
    clave = page.locator("#claveTxt").inner_text().strip()
    modal.locator("#claveOk").click()
    expect(page.locator("#claveTxt")).to_have_count(0)
    return clave


# ── Alta ────────────────────────────────────────────────────────────────────
def test_alta_de_comision_con_clave_generada_y_entra(bibliotecaria: Page, browser, base_url):
    page = bibliotecaria
    _abrir_usuarios(page)
    expect(page.locator("#usrLista")).to_contain_text("Todavía no hay usuarios cargados")

    _completar_alta(page, nombre="Marta Directiva", usuario="marta", rol="comision")
    clave = _leer_clave_y_cerrar(page, "Marta Directiva", "marta")
    # Generada: 10 caracteres, sin los que se confunden (l/1/O/0).
    assert re.fullmatch(r"[a-km-zA-HJ-NP-Z2-9]{10}", clave), clave

    fila = _fila(page, "marta")
    expect(fila).to_contain_text("Marta Directiva")
    expect(fila).to_contain_text("Comisión Directiva")
    expect(fila).to_contain_text("Activo")
    expect(fila).to_contain_text("nunca entró")

    # Se vio una sola vez: ya no está en la pantalla ni sale por la API.
    expect(page.locator("body")).not_to_contain_text(clave)
    listado = _api(page, "GET", "/api/usuarios").json()
    assert all("password" not in u for u in listado["items"])
    assert clave not in str(listado)

    with _otra_persona(browser, base_url) as marta:
        entrar(marta, "marta", clave)
        expect(marta.locator("#userLabel")).to_contain_text("Marta Directiva")
        expect(marta.locator("#userLabel")).to_contain_text("Comisión Directiva")

    page.locator("#usrRef").click()
    expect(fila).not_to_contain_text("nunca entró")


def test_alta_con_clave_elegida(bibliotecaria: Page, browser, base_url):
    page = bibliotecaria
    _abrir_usuarios(page)
    _completar_alta(page, nombre="Tesorería", usuario="tesoreria", rol="comision",
                    clave="una-clave-larga")
    assert _leer_clave_y_cerrar(page, "Tesorería", "tesoreria") == "una-clave-larga"
    with _otra_persona(browser, base_url) as otra:
        entrar(otra, "tesoreria", "una-clave-larga")


def test_alta_de_subcomision_exige_cual(bibliotecaria: Page, browser, base_url):
    page = bibliotecaria
    _abrir_usuarios(page)
    _completar_alta(page, nombre="Gente de Cultura", usuario="cultura", rol="subcomision")
    expect(page.locator("#usrMsg .msg.error")).to_have_text(
        "Un usuario de subcomisión necesita indicar cuál.")
    expect(page.locator("#usrForm")).to_be_visible()          # no se pierde lo escrito
    expect(page.locator("#usrNombre")).to_have_value("Gente de Cultura")

    # Las subcomisiones conocidas se sugieren al escribir.
    sugeridas = page.locator("#usrSubLista option").evaluate_all("os => os.map(o => o.value)")
    assert {"Cultura", "Prensa"} <= set(sugeridas)

    page.locator("#usrSub").fill("Cultura")
    page.locator("#usrGuardar").click()
    clave = _leer_clave_y_cerrar(page, "Gente de Cultura", "cultura")
    fila = _fila(page, "cultura")
    expect(fila).to_contain_text("Subcomisión")
    expect(fila).to_contain_text("Cultura")

    with _otra_persona(browser, base_url) as cultura:
        entrar(cultura, "cultura", clave)
        expect(cultura.locator("#userLabel")).to_contain_text("Subcomisión · Cultura")


def test_usuario_repetido_no_se_crea(bibliotecaria: Page):
    crear_usuario("comision", "prensa")
    page = bibliotecaria
    _abrir_usuarios(page)
    # Sin distinguir mayúsculas: "Prensa" y "prensa" son el mismo usuario.
    _completar_alta(page, nombre="Otra Prensa", usuario="Prensa", rol="comision")
    expect(page.locator("#usrMsg .msg.error")).to_have_text("Ya existe un usuario con 'Prensa'.")
    expect(page.locator("#claveTxt")).to_have_count(0)
    expect(page.locator("#usrLista tbody tr")).to_have_count(1)


def test_alta_sin_nombre_avisa(bibliotecaria: Page):
    page = bibliotecaria
    _abrir_usuarios(page)
    _completar_alta(page, nombre="", usuario="sinnombre", rol="comision")
    expect(page.locator("#usrMsg .msg.error")).to_have_text("Falta el nombre de la persona.")


def test_usuario_igual_al_email_de_otro(bibliotecaria: Page, browser, base_url):
    page = bibliotecaria
    r = _api(page, "POST", "/api/usuarios", {"usuario": "prensa", "nombre": "Prensa",
                                             "rol": "comision", "email": "cultura@bayer.org",
                                             "password": "clave-prensa"})
    assert r.ok
    r = _api(page, "POST", "/api/usuarios", {"usuario": "cultura@bayer.org", "nombre": "Cultura",
                                             "rol": "subcomision", "subcomision": "Cultura",
                                             "password": "clave-cultura"})
    # O no se deja crear, o el usuario nuevo puede entrar con lo que se le dio.
    if r.status == 400:
        return
    with _otra_persona(browser, base_url) as cultura:
        entrar(cultura, "cultura@bayer.org", "clave-cultura")


# ── Edición, desactivar, resetear, borrar ───────────────────────────────────
def test_editar_nombre_y_rol_cambia_lo_que_ve(bibliotecaria: Page, browser, base_url):
    u = crear_usuario("comision", "juana")
    page = bibliotecaria
    _abrir_usuarios(page)
    _fila(page, "juana").get_by_role("button", name="Editar").click()
    expect(page.locator("#usrForm h3")).to_have_text("Editar Comision de Prueba")
    expect(page.locator("#usrUsuario")).to_be_disabled()     # el usuario no se cambia
    expect(page.locator("#usrUsuario")).to_have_value("juana")
    expect(page.locator("#usrPass")).to_have_count(0)        # la clave, solo reseteando

    page.locator("#usrNombre").fill("Juana Manso")
    page.locator("#usrRol").select_option("subcomision")
    page.locator("#usrGuardar").click()
    expect(page.locator("#usrMsg .msg.error")).to_have_text(
        "Un usuario de subcomisión necesita indicar cuál.")

    page.locator("#usrSub").fill("Prensa")
    page.locator("#usrGuardar").click()
    expect(page.locator("#usrForm")).to_be_hidden()
    fila = _fila(page, "juana")
    expect(fila).to_contain_text("Juana Manso")
    expect(fila).to_contain_text("Subcomisión")
    expect(fila).to_contain_text("Prensa")

    with _otra_persona(browser, base_url) as juana:
        entrar(juana, "juana", u["clave"])
        expect(juana.locator("#userLabel")).to_contain_text("Juana Manso")
        expect(juana.locator("#userLabel")).to_contain_text("Subcomisión · Prensa")
        assert _secciones(juana) == [s["id"] for s in permisos.secciones_de("subcomision")]


def test_cancelar_la_edicion_no_cambia_nada(bibliotecaria: Page):
    crear_usuario("comision", "juana")
    page = bibliotecaria
    _abrir_usuarios(page)
    _fila(page, "juana").get_by_role("button", name="Editar").click()
    page.locator("#usrNombre").fill("Otro Nombre")
    page.locator("#usrCancelar").click()
    expect(page.locator("#usrForm")).to_be_hidden()
    page.locator("#usrRef").click()
    expect(_fila(page, "juana")).to_contain_text("Comision de Prueba")


def test_desactivar_y_volver_a_activar(bibliotecaria: Page, browser, base_url):
    u = crear_usuario("comision", "pedro")
    page = bibliotecaria
    _abrir_usuarios(page)
    fila = _fila(page, "pedro")
    fila.get_by_role("button", name="Desactivar").click()
    expect(fila).to_contain_text("Desactivado")
    expect(fila).to_have_class(re.compile(r"\busr-off\b"))

    with _otra_persona(browser, base_url) as pedro:
        # Con la contraseña bien, igual no entra.
        _no_entra(pedro, "pedro", u["clave"], "Usuario o contraseña incorrectos.")

    fila.get_by_role("button", name="Activar").click()
    expect(fila).to_contain_text("Activo")
    with _otra_persona(browser, base_url) as pedro:
        entrar(pedro, "pedro", u["clave"])


@pytest.mark.parametrize("accion", ["Editar", "Desactivar"])
def test_se_ve_la_confirmacion_despues_de_guardar(bibliotecaria: Page, accion):
    crear_usuario("comision", "juana")
    page = bibliotecaria
    _abrir_usuarios(page)
    _fila(page, "juana").get_by_role("button", name=accion).click()
    if accion == "Editar":
        page.locator("#usrNombre").fill("Juana Manso")
        page.locator("#usrGuardar").click()
        expect(_fila(page, "juana")).to_contain_text("Juana Manso")
        esperado = "Listo: se actualizó Juana Manso."
    else:
        expect(_fila(page, "juana")).to_contain_text("Desactivado")
        esperado = "quedó desactivada/o"
    expect(page.locator("#usrMsg .msg.ok")).to_contain_text(esperado, timeout=3_000)


def test_resetear_clave(bibliotecaria: Page, browser, base_url):
    u = crear_usuario("comision", "pedro")
    page = bibliotecaria
    _abrir_usuarios(page)
    _fila(page, "pedro").get_by_role("button", name="Resetear clave").click()
    confirmar = page.locator(".modal", has=page.locator("#ucYes"))
    expect(confirmar).to_contain_text("La anterior deja de servir")
    page.locator("#ucYes").click()
    nueva = _leer_clave_y_cerrar(page, "Comision de Prueba", "pedro")
    assert nueva and nueva != u["clave"]

    with _otra_persona(browser, base_url) as pedro:
        _no_entra(pedro, "pedro", u["clave"])
        entrar(pedro, "pedro", nueva)


def test_resetear_clave_se_puede_cancelar(bibliotecaria: Page, browser, base_url):
    u = crear_usuario("comision", "pedro")
    page = bibliotecaria
    _abrir_usuarios(page)
    _fila(page, "pedro").get_by_role("button", name="Resetear clave").click()
    page.locator("#ucNo").click()
    expect(page.locator("#claveTxt")).to_have_count(0)
    with _otra_persona(browser, base_url) as pedro:
        entrar(pedro, "pedro", u["clave"])                   # la de siempre sigue andando


def test_borrar_usuario(bibliotecaria: Page, browser, base_url):
    u = crear_usuario("comision", "pedro")
    crear_usuario("comision", "queda")
    page = bibliotecaria
    _abrir_usuarios(page)
    _fila(page, "pedro").get_by_role("button", name="Borrar").click()
    expect(page.locator(".modal h3")).to_have_text("Borrar usuario")
    expect(page.locator(".modal")).to_contain_text("conviene desactivarla/o")
    page.locator("#ucYes").click()
    expect(_fila(page, "pedro")).to_have_count(0)
    expect(_fila(page, "queda")).to_have_count(1)

    with _otra_persona(browser, base_url) as pedro:
        # Ya no es usuario de la app: el login cae en Koha, que tampoco lo conoce.
        _no_entra(pedro, "pedro", u["clave"], "Koha")


def test_nadie_se_desactiva_ni_se_borra_a_si_mismo(page: Page):
    u = crear_usuario("comision", "marta")
    entrar(page, "marta", u["clave"])
    _abrir_usuarios(page)
    fila = _fila(page, "marta")
    fila.get_by_role("button", name="Desactivar").click()
    expect(page.locator("#usrMsg .msg.error")).to_have_text("No podés desactivarte a vos misma/o.")
    fila.get_by_role("button", name="Borrar").click()
    page.locator("#ucYes").click()
    expect(page.locator("#usrMsg .msg.error")).to_have_text("No podés borrarte a vos misma/o.")
    expect(fila).to_contain_text("Activo")


@pytest.mark.parametrize("accion", ["Desactivar", "Borrar"])
def test_sacarle_el_acceso_corta_la_sesion_abierta(bibliotecaria: Page, browser, base_url,
                                                     accion):
    u = crear_usuario("comision", "pedro")
    with _otra_persona(browser, base_url) as pedro:
        entrar(pedro, "pedro", u["clave"])

        page = bibliotecaria
        _abrir_usuarios(page)
        _fila(page, "pedro").get_by_role("button", name=accion).click()
        if accion == "Borrar":
            page.locator("#ucYes").click()
            expect(_fila(page, "pedro")).to_have_count(0)
        else:
            expect(_fila(page, "pedro")).to_contain_text("Desactivado")

        # Pedro sigue con la pestaña abierta: ya no debería poder leer datos de socios.
        assert _api(pedro, "GET", "/api/loans/active").status == 401


def test_la_comision_no_puede_crear_bibliotecarias(page: Page):
    u = crear_usuario("comision", "marta")
    entrar(page, "marta", u["clave"])
    _abrir_usuarios(page)
    page.locator("#usrNuevo").click()
    roles = page.locator("#usrRol option").evaluate_all("os => os.map(o => o.value)")
    r = _api(page, "POST", "/api/usuarios", {"usuario": "colada", "nombre": "Colada",
                                             "rol": "bibliotecaria", "password": "clave-colada"})
    assert "bibliotecaria" not in roles and r.status in (400, 403)


# ── La contraseña propia ────────────────────────────────────────────────────
def test_cambiar_mi_contrasena(page: Page):
    u = crear_usuario("subcomision", "cultura", subcomision="Cultura")
    entrar(page, "cultura", u["clave"])
    boton = page.locator("#claveBtn")
    expect(boton).to_be_visible()
    boton.click()
    modal = page.locator(".modal", has=page.locator("#miActual"))
    expect(modal.locator("h3")).to_have_text("Cambiar mi contraseña")

    page.locator("#miActual").fill("no-es-esta")
    page.locator("#miNueva").fill("nueva-clave-123")
    page.locator("#miOk").click()
    expect(page.locator("#miClaveMsg .msg.error")).to_have_text("La contraseña actual no es correcta.")

    page.locator("#miActual").fill(u["clave"])
    page.locator("#miNueva").fill("corta")
    page.locator("#miOk").click()
    expect(page.locator("#miClaveMsg .msg.error")).to_have_text(
        "La contraseña nueva tiene que tener al menos 8 caracteres.")

    page.locator("#miNueva").fill("nueva-clave-123")
    page.locator("#miOk").click()
    expect(modal).to_have_count(0)
    expect(page.locator("#panelMsg .msg.ok")).to_have_text("Listo: tu contraseña quedó cambiada.")

    page.locator("#logoutBtn").click()
    _no_entra(page, "cultura", u["clave"])
    entrar(page, "cultura", "nueva-clave-123")


def test_cambiar_mi_contrasena_se_puede_cancelar(page: Page):
    u = crear_usuario("comision", "marta")
    entrar(page, "marta", u["clave"])
    page.locator("#claveBtn").click()
    page.locator("#miActual").fill(u["clave"])
    page.locator("#miNueva").fill("otra-clave-123")
    page.locator("#miNo").click()
    expect(page.locator("#miActual")).to_have_count(0)
    page.locator("#logoutBtn").click()
    entrar(page, "marta", u["clave"])


def test_la_bibliotecaria_no_tiene_boton_de_clave(bibliotecaria: Page):
    # Su contraseña es la de Koha: se cambia allá.
    expect(bibliotecaria.locator("#claveBtn")).to_be_hidden()
    r = _api(bibliotecaria, "POST", "/api/password", {"actual": "clave", "nueva": "otra-clave-123"})
    assert r.status == 400
    assert "Koha" in r.json()["detail"]


# ── Permisos por rol ────────────────────────────────────────────────────────
def _entrar_como(page: Page, rol: str) -> None:
    if rol == "bibliotecaria":
        entrar(page, servidor.USUARIO_KOHA, servidor.CLAVE_KOHA)
    else:
        u = crear_usuario(rol, subcomision="Cultura" if rol == "subcomision" else "")
        entrar(page, u["usuario"], u["clave"])


@pytest.mark.parametrize("rol", ["bibliotecaria", "comision", "subcomision"])
def test_el_menu_muestra_lo_que_permite_el_rol(page: Page, rol):
    _entrar_como(page, rol)
    esperadas = permisos.secciones_de(rol)
    assert _secciones(page) == [s["id"] for s in esperadas]
    assert _grupos(page) == [e["titulo"] for e in permisos.menu_de(rol) if e["tipo"] == "grupo"]
    expect(page.locator("#userLabel")).to_contain_text(permisos.ETIQUETAS[rol])
    # Arranca en la primera sección que le toca.
    boton = page.locator(f'#tabs button[data-tab="{esperadas[0]["id"]}"]')
    expect(boton).to_have_class(re.compile(r"\bactive\b"))


def test_lo_que_distingue_a_cada_rol_en_el_menu(browser, base_url):
    """Lo decidido, dicho en voz alta (por si alguien toca la tabla sin querer)."""
    menus = {}
    for rol in ("bibliotecaria", "comision", "subcomision"):
        with _otra_persona(browser, base_url) as p:
            _entrar_como(p, rol)
            menus[rol] = set(_secciones(p))
    assert "pizarron" in menus["bibliotecaria"]
    assert menus["comision"] == menus["bibliotecaria"] - {"pizarron"}
    assert "usuarios" not in menus["subcomision"]
    assert not {"loans", "members", "mails", "cuotas", "stats"} & menus["subcomision"]
    assert {"agenda", "solicitudes", "mias", "actualidad"} <= menus["subcomision"]


_MAIL = {"subject": "Hola", "body": "Hola {{nombre}}",
         "recipients": [{"cardnumber": "100", "email": "ana@example.org"}], "dry_run": True}

_PROHIBIDO_A_SUBCOMISION = [
    ("GET", "/api/usuarios", None),
    ("POST", "/api/usuarios", {"usuario": "x", "nombre": "X", "rol": "comision"}),
    ("GET", "/api/loans/active", None),
    ("GET", "/api/loans/overdue", None),
    ("GET", "/api/members?q=P%C3%A9rez", None),
    ("GET", "/api/members/100/profile", None),
    ("GET", "/api/stats", None),
    ("GET", "/api/mail/config", None),
    ("POST", "/api/mail/send", _MAIL),
    ("GET", "/api/pizarron", None),
    ("POST", "/api/pizarron", {"texto": "hola"}),
    ("GET", "/api/registro", None),
]


def test_la_subcomision_no_llega_por_la_api_a_lo_que_no_le_toca(page: Page, bandeja):
    _entrar_como(page, "subcomision")
    assert _api(page, "GET", "/api/me").ok
    rechazos = {}
    for metodo, ruta, datos in _PROHIBIDO_A_SUBCOMISION:
        r = _api(page, metodo, ruta, datos)
        rechazos[f"{metodo} {ruta}"] = r.status
    assert rechazos == {k: 403 for k in rechazos}
    assert bandeja == []                                       # y no salió ningún mail
    assert _api(page, "GET", "/api/registro/mios").ok          # lo suyo, sí


def test_la_comision_no_llega_al_pizarron_por_la_api(page: Page):
    _entrar_como(page, "comision")
    assert _api(page, "GET", "/api/pizarron").status == 403
    assert _api(page, "GET", "/api/pizarron/novedades").status == 403
    assert _api(page, "POST", "/api/pizarron", {"texto": "hola"}).status == 403
    # Lo demás de las bibliotecarias, sí (Koha a través de la cuenta de servicio).
    assert _api(page, "GET", "/api/usuarios").ok
    prestamos = _api(page, "GET", "/api/loans/active")
    assert prestamos.ok and len(prestamos.json()) == 3


def test_sin_token_la_api_no_contesta(page: Page):
    page.goto("/")
    for ruta in ("/api/me", "/api/usuarios", "/api/loans/active", "/api/pizarron"):
        assert page.request.get(ruta).status == 401, ruta


# ── Sesión ──────────────────────────────────────────────────────────────────
def _al_login_sin_rastros(page: Page) -> None:
    expect(page.locator("#loginView")).to_be_visible()
    expect(page.locator("#appView")).to_be_hidden()
    expect(page.locator("#loginMsg")).to_be_empty()
    assert _token(page) == ""


def _con_token(page: Page, token: str) -> None:
    page.goto("/")
    page.evaluate("t => localStorage.setItem('token', t)", token)
    page.reload()


def test_token_basura_manda_al_login(page: Page):
    _con_token(page, "esto-no-es-un-token")
    _al_login_sin_rastros(page)


def test_token_vencido_manda_al_login(page: Page):
    vencido = jwt.encode({"sub": "biblio", "sid": "x", "rol": "bibliotecaria",
                          "exp": dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)},
                         settings.app_secret_key, algorithm=auth.ALGORITHM)
    _con_token(page, vencido)
    _al_login_sin_rastros(page)


def test_token_firmado_con_otra_clave_manda_al_login(page: Page):
    trucho = jwt.encode({"sub": "biblio", "sid": "x", "rol": "bibliotecaria",
                         "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)},
                        "otra-clave-" + "y" * 40, algorithm=auth.ALGORITHM)
    _con_token(page, trucho)
    _al_login_sin_rastros(page)


def test_servidor_reiniciado_manda_al_login_y_se_vuelve_a_entrar(bibliotecaria: Page):
    page = bibliotecaria
    auth.SESIONES.clear()                    # lo que pasa si se reinicia el backend
    page.reload()
    _al_login_sin_rastros(page)
    entrar(page, servidor.USUARIO_KOHA, servidor.CLAVE_KOHA)


def test_sesion_que_se_cae_con_la_app_abierta(bibliotecaria: Page):
    page = bibliotecaria
    # Que termine de cargar Inicio: si no, esos pedidos ya vuelven al login antes del clic.
    page.wait_for_load_state("networkidle")
    auth.SESIONES.clear()
    # La próxima vez que la pantalla le pide algo al servidor, vuelve al login.
    page.locator('#tabs button[data-tab="loans"]').click()
    expect(page.locator("#loginView")).to_be_visible()
    expect(page.locator("#appView")).to_be_hidden()
    assert _token(page) == ""


def test_despues_de_salir_el_token_ya_no_sirve(bibliotecaria: Page):
    page = bibliotecaria
    viejo = _token(page)
    page.locator("#logoutBtn").click()
    expect(page.locator("#loginView")).to_be_visible()
    r = page.request.get("/api/me", headers={"Authorization": f"Bearer {viejo}"})
    assert r.status == 401
