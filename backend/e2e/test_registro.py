"""Registro de actividades realizadas: del link que se comparte por WhatsApp a lo que
queda en "Lo que anda pasando en la Bayer".

El recorrido completo, con las tres personas que lo tocan:

  · quien dio la actividad abre el link en el celular (sin usuario) y la cuenta;
  · la biblioteca la ve "recibida", la valida, la corrige o la descarta;
  · una subcomisión ve lo suyo en "Nuestras actividades" y, como todos, lo validado
    en "Lo que anda pasando".

Para armar el escenario de cada prueba se cargan links y registros directo con
`app.registro` (igual que `crear_usuario`): lo que se prueba es lo que se ve después.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import timedelta

import pytest
from playwright.sync_api import Browser, Page, expect

from app import registro
from app.api import routes

from . import datos, servidor
from .ayudas import crear_usuario, entrar, ir_a

HOY = datos.HOY
CELULAR = {"width": 390, "height": 844}


# ── Arnés propio de este archivo ────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _sin_tope_de_envios():
    """El tope de envíos por conexión vive en memoria: el reinicio de la app no lo borra."""
    routes._ENVIOS_POR_IP.clear()
    yield
    routes._ENVIOS_POR_IP.clear()


def _vigilar(page: Page) -> list[str]:
    errores: list[str] = []
    page.on("pageerror", lambda e: errores.append(str(e)))
    page.on("console", lambda m: errores.append(m.text) if m.type == "error"
            and "Failed to load resource" not in m.text else None)
    return errores


@pytest.fixture
def celular(browser: Browser, base_url: str):
    """Otra persona, sin usuario, con el link abierto en el teléfono."""
    ctx = browser.new_context(base_url=base_url, viewport=CELULAR, is_mobile=True,
                              has_touch=True, device_scale_factor=3, locale="es-AR")
    p = ctx.new_page()
    errores = _vigilar(p)
    yield p
    ctx.close()
    assert not errores, "Errores de JavaScript en el formulario público:\n" + "\n".join(errores)


def _dia(delta: int = 0) -> str:
    return (HOY + timedelta(days=delta)).isoformat()


def _ar(iso: str) -> str:
    a, m, d = iso.split("-")
    return f"{d}/{m}/{a}"


def _link(clase: str = "general", titulo: str = "Link general", **extra) -> dict:
    link = registro.crear_link({"clase": clase, "titulo": titulo, **extra}, "Biblio de prueba")
    return {**link, "url": f"{servidor.URL}/registro/{link['token']}"}


def _actividad(titulo: str, personas: int, *, fecha: str | None = None, subcomision: str = "",
               hora: str = "18:00", hasta: str = "20:00", tipo: str = "charla", **extra) -> dict:
    return {"titulo": titulo, "tipo": tipo, "fecha": fecha or _dia(), "hora_inicio": hora,
            "hora_fin": hasta, "personas_total": personas, "subcomision": subcomision,
            "organiza": "subcomision" if subcomision else "biblioteca",
            "quien_completa": {"nombre": "Marta Tallerista", "contacto": "marta@example.org"},
            **extra}


def _recibido(titulo: str, personas: int, **kw) -> dict:
    """Como si hubiera llegado por el link público: queda esperando revisión."""
    return registro.guardar("actividad", _actividad(titulo, personas, **kw), link=_link())


def _validado(titulo: str, personas: int, **kw) -> dict:
    """Cargado por la biblioteca desde la app: ya queda validado."""
    return registro.guardar("actividad", _actividad(titulo, personas, **kw), cargado_por="biblio")


def _descartado(titulo: str, personas: int, **kw) -> dict:
    r = _recibido(titulo, personas, **kw)
    return registro.resolver(r["id"], "descartado", "biblio", "Era una prueba")


def _tarjeta(page: Page, titulo: str, donde: str = "#regLista"):
    return page.locator(f"{donde} .sol-card", has=page.get_by_text(titulo, exact=True))


def _ir_a_links(page: Page) -> None:
    ir_a(page, "registro")
    page.locator('#regTabs button[data-vista="links"]').click()
    expect(page.locator("#regLinks")).to_be_visible()


def _filtrar(page: Page, estado: str) -> None:
    page.locator(f'#regEstado button[data-estado="{estado}"]').click()
    expect(page.locator("#regLista .loader, #regLista .loader-sm")).to_have_count(0)


def _kpi(page: Page, etiqueta: str):
    return page.locator(".viz-kpi", has=page.locator(".l", has_text=re.compile(f"^{etiqueta}$"))).locator(".n")


# ── Links para compartir ────────────────────────────────────────────────────
def test_crear_link_de_una_actividad_de_subcomision_con_su_direccion(bibliotecaria: Page):
    page = bibliotecaria
    _ir_a_links(page)
    expect(page.locator("#regLinkLista")).to_contain_text("Todavía no hay links")

    page.locator("#regLinkNuevo").click()
    page.locator("#lkClase").select_option("actividad")
    page.locator("#lkTitulo").fill("Charla de huerta")
    page.locator("#lkFecha").fill(_dia(-1))
    page.locator("#lkInicio").fill("18:00")
    page.locator("#lkFin").fill("19:30")
    page.locator("#lkTipo").select_option("charla")
    page.locator("#lkSub").select_option("Cultura")
    page.locator("#lkGuardar").click()

    tarjeta = _tarjeta(page, "Charla de huerta", "#regLinkLista")
    expect(tarjeta).to_contain_text("Abierto")
    expect(tarjeta).to_contain_text("De una actividad · usado 0 vez(ces)")
    # La dirección para compartir es la del sitio (APP_PUBLIC_URL), con un código largo.
    expect(tarjeta.locator(".meta")).to_have_text(
        re.compile("^" + re.escape(servidor.URL) + r"/registro/[\w-]{24,}$"))
    expect(page.locator("#regLinkLista")).not_to_contain_text("APP_PUBLIC_URL")
    expect(page.locator("#regLinkForm")).to_be_hidden()

    (link,) = registro.links()
    assert link["precarga"] == {"titulo": "Charla de huerta", "fecha": _dia(-1),
                                "hora_inicio": "18:00", "hora_fin": "19:30", "tipo": "charla",
                                "subcomision": "Cultura", "organiza": "subcomision"}


def test_link_de_actividad_sin_nombre_no_se_crea(bibliotecaria: Page):
    page = bibliotecaria
    _ir_a_links(page)
    page.locator("#regLinkNuevo").click()
    page.locator("#lkClase").select_option("actividad")
    page.locator("#lkGuardar").click()
    expect(page.locator("#regMsg .msg.error")).to_contain_text("Poné un nombre")
    assert registro.links() == []


def test_copiar_link_y_mandarlo_por_whatsapp(bibliotecaria: Page, base_url: str):
    page = bibliotecaria
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base_url)
    link = _link("taller", "Taller de cerámica")
    _ir_a_links(page)
    tarjeta = _tarjeta(page, "Taller de cerámica", "#regLinkLista")
    expect(tarjeta).to_contain_text("De un taller")

    tarjeta.get_by_role("button", name="Copiar link").click()
    expect(page.locator("#regMsg")).to_contain_text("Link copiado.")
    assert page.evaluate("navigator.clipboard.readText()") == link["url"]

    wa = tarjeta.get_by_role("link", name="Enviar por WhatsApp")
    href = wa.get_attribute("href")
    assert href.startswith("https://wa.me/?text=")
    assert link["token"] in href and "Taller%20de%20cer%C3%A1mica" in href


def _otro_celular(browser: Browser, base_url: str) -> Page:
    ctx = browser.new_context(base_url=base_url, viewport=CELULAR, is_mobile=True, has_touch=True)
    return ctx.new_page()


def test_cerrar_reabrir_y_borrar_un_link(bibliotecaria: Page, celular: Page,
                                         browser: Browser, base_url: str):
    page = bibliotecaria
    link = _link("general", "Link de la semana")
    _ir_a_links(page)
    tarjeta = _tarjeta(page, "Link de la semana", "#regLinkLista")

    tarjeta.get_by_role("button", name="Cerrar").click()
    expect(tarjeta).to_contain_text("Cerrado")
    celular.goto(link["url"])
    expect(celular.locator("#pantalla h1")).to_have_text("No pudimos abrir el formulario")
    expect(celular.locator(".msg.error")).to_have_text(
        "Este link está cerrado. Pedile uno nuevo a la biblioteca.")
    expect(celular.locator("#form")).to_have_count(0)

    tarjeta.get_by_role("button", name="Reabrir").click()
    expect(tarjeta).to_contain_text("Abierto")
    otro = _otro_celular(browser, base_url)       # alguien que no lo había abierto cerrado
    try:
        otro.goto(link["url"])
        expect(otro.locator("#f_titulo")).to_be_visible()

        tarjeta.get_by_role("button", name="Borrar").click()
        page.locator("#ucYes").click()
        expect(page.locator("#regLinkLista")).to_contain_text("Todavía no hay links")
        otro.reload()
        expect(otro.locator(".msg.error")).to_have_text("Este link no existe o fue dado de baja.")
    finally:
        otro.context.close()


def test_link_reabierto_anda_en_el_telefono_que_lo_vio_cerrado(bibliotecaria: Page, celular: Page):
    link = _link("general", "Link de la semana")
    registro.actualizar_link(link["id"], {"abierto": False})
    celular.goto(link["url"])
    expect(celular.locator(".msg.error")).to_contain_text("Este link está cerrado")

    _ir_a_links(bibliotecaria)
    tarjeta = _tarjeta(bibliotecaria, "Link de la semana", "#regLinkLista")
    tarjeta.get_by_role("button", name="Reabrir").click()
    expect(tarjeta).to_contain_text("Abierto")

    celular.reload()
    expect(celular.locator("#f_titulo")).to_be_visible(timeout=3_000)


def test_link_vencido_se_marca_y_no_abre(bibliotecaria: Page, celular: Page):
    link = _link("general", "Link viejo", vence=_dia(-1))
    _ir_a_links(bibliotecaria)
    expect(_tarjeta(bibliotecaria, "Link viejo", "#regLinkLista")).to_contain_text("Vencido")
    celular.goto(link["url"])
    expect(celular.locator(".msg.error")).to_have_text(
        "Este link venció. Pedile uno nuevo a la biblioteca.")


# ── Formulario público, desde el celular ────────────────────────────────────
def _paso_1(p: Page, titulo: str = "Charla sobre huerta urbana", fecha: str | None = None) -> None:
    p.locator("#f_titulo").fill(titulo)
    p.locator("#f_tipo").select_option("charla")
    p.locator("#f_fecha").fill(fecha or _dia(-1))
    p.locator("#f_hora_inicio").fill("18:00")
    p.locator("#f_hora_fin").fill("19:30")


def _siguiente(p: Page, paso: int) -> None:
    p.get_by_role("button", name="Siguiente").click()
    expect(p.locator(".paso-n")).to_have_text(f"Paso {paso} de 3")


def test_contar_una_actividad_desde_el_celular_llega_como_recibida(celular: Page, bibliotecaria: Page):
    link = _link("general", "Link general")
    p = celular
    p.goto(link["url"])
    expect(p.locator(".paso-n")).to_have_text("Paso 1 de 3")

    _paso_1(p)
    p.locator("#f_espacio_id").select_option(label="Sala principal")
    expect(p.locator("#f_espacio_otro")).to_have_count(0)        # con espacio, no pregunta dónde
    p.locator('.chip[data-valor="ambiente"]').click()
    expect(p.locator('.chip[data-valor="ambiente"]')).to_have_class(re.compile(r"\bon\b"))
    p.locator("#f_a_cargo").fill("Marta, tallerista")
    p.locator("#f_organiza").select_option("subcomision")
    p.locator("#f_subcomision").select_option("Cultura")
    _siguiente(p, 2)

    # Las edades suman solas el total.
    p.locator('[data-franja="18_29"] input').fill("10")
    p.locator('[data-franja="30_59"] button[data-paso="1"]').click()
    p.locator('[data-franja="30_59"] button[data-paso="1"]').click()
    expect(p.locator("#f_personas_total")).to_have_value("12")
    p.locator("#f_primera_vez").fill("3")
    p.locator('.chip[data-valor="whatsapp"]').click()
    _siguiente(p, 3)

    p.locator("#f_descripcion").fill("Armamos almácigos con semillas del banco del barrio.")
    p.locator('.estrella[aria-label="4 de 5"]').click()
    p.locator("#f_nombre_completa").fill("Marta Gómez")
    p.locator("#f_contacto_completa").fill("marta@example.org")
    # Nada se sale del ancho del teléfono.
    assert p.evaluate("document.documentElement.scrollWidth") <= CELULAR["width"]
    p.get_by_role("button", name="Enviar").click()

    expect(p.locator(".ok-grande h1")).to_have_text("¡Gracias!")
    expect(p.locator(".resumen")).to_contain_text("Charla sobre huerta urbana")
    expect(p.locator(".resumen")).to_contain_text("Personas: 12")

    # La biblioteca lo ve esperando revisión, con lo que se cargó.
    page = bibliotecaria
    ir_a(page, "registro")
    expect(page.locator('#regEstado button[data-estado="recibido"]')).to_have_text("Recibidos (1)")
    tarjeta = _tarjeta(page, "Charla sobre huerta urbana")
    expect(tarjeta).to_contain_text("Recibido")
    expect(tarjeta.locator(".sol-cuando")).to_have_text(
        f"{_ar(_dia(-1))} · 18:00–19:30 · Sala principal · 12 persona(s)")
    expect(tarjeta).to_contain_text("Actividad · Cultura")
    expect(tarjeta).to_contain_text("Marta Gómez")
    expect(tarjeta).to_contain_text("Ambiente / huerta")
    expect(tarjeta).to_contain_text("Armamos almácigos")
    expect(tarjeta).to_contain_text("Valoración: ★★★★☆")
    for boton in ("✓ Validar", "Corregir", "Descartar"):
        expect(tarjeta.get_by_role("button", name=boton)).to_be_visible()

    # El link general sigue abierto y cuenta el uso.
    page.locator('#regTabs button[data-vista="links"]').click()
    tarjeta_link = _tarjeta(page, "Link general", "#regLinkLista")
    expect(tarjeta_link).to_contain_text("Abierto")
    expect(tarjeta_link).to_contain_text("usado 1 vez(ces)")


def test_el_formulario_pide_lo_obligatorio_en_cada_paso(celular: Page):
    link = _link()
    p = celular
    p.goto(link["url"])

    p.get_by_role("button", name="Siguiente").click()
    expect(p.locator("#error")).to_have_text(
        "Falta el nombre de la actividad, el tipo, la fecha, la hora de inicio.")
    expect(p.locator(".paso-n")).to_have_text("Paso 1 de 3")

    p.locator("#f_titulo").fill("Club de lectura")
    p.get_by_role("button", name="Siguiente").click()
    expect(p.locator("#error")).to_have_text("Falta el tipo, la fecha, la hora de inicio.")

    _paso_1(p, "Club de lectura")
    _siguiente(p, 2)
    p.get_by_role("button", name="Siguiente").click()
    expect(p.locator("#error")).to_have_text("Falta cuántas personas vinieron.")

    p.locator("#f_personas_total").fill("8")
    _siguiente(p, 3)
    p.get_by_role("button", name="Enviar").click()
    expect(p.locator("#error")).to_have_text("Falta tu nombre.")

    # "Atrás" no pierde lo cargado.
    p.get_by_role("button", name="Atrás").click()
    expect(p.locator("#f_personas_total")).to_have_value("8")
    assert registro.listar() == []


def test_hora_de_fin_anterior_la_rechaza_la_biblioteca(celular: Page):
    link = _link()
    p = celular
    p.goto(link["url"])
    _paso_1(p)
    p.locator("#f_hora_fin").fill("17:00")                       # antes de las 18
    _siguiente(p, 2)
    p.locator("#f_personas_total").fill("5")
    _siguiente(p, 3)
    p.locator("#f_nombre_completa").fill("Marta")
    p.get_by_role("button", name="Enviar").click()
    expect(p.locator("#error")).to_have_text("La hora de fin no puede ser anterior a la de inicio.")
    expect(p.get_by_role("button", name="Enviar")).to_be_enabled()
    assert registro.listar() == []


def test_link_que_no_existe_muestra_un_error_entendible(celular: Page):
    celular.goto("/registro/esto-no-es-un-codigo")
    expect(celular.locator("#pantalla h1")).to_have_text("No pudimos abrir el formulario")
    expect(celular.locator(".msg.error")).to_have_text("Este link no existe o fue dado de baja.")
    expect(celular.locator("#pantalla")).to_contain_text("pedile uno nuevo")
    expect(celular.locator("#form")).to_have_count(0)


def test_lo_cargado_a_medias_queda_guardado_en_el_telefono(celular: Page):
    link = _link()
    celular.goto(link["url"])
    celular.locator("#f_titulo").fill("Muestra de fotos")
    celular.reload()
    expect(celular.locator("#f_titulo")).to_have_value("Muestra de fotos")


def test_link_de_una_actividad_viene_precargado_y_se_cierra_al_recibirse(celular: Page):
    link = _link("actividad", "Presentación del libro de Juana",
                 precarga={"titulo": "Presentación del libro de Juana", "fecha": _dia(-2),
                           "hora_inicio": "19:00", "tipo": "presentacion", "subcomision": "cultura"})
    p = celular
    p.goto(link["url"])
    expect(p.locator("#subtitulo")).to_have_text("Presentación del libro de Juana")
    expect(p.locator("#f_titulo")).to_have_value("Presentación del libro de Juana")
    expect(p.locator("#f_tipo")).to_have_value("presentacion")
    expect(p.locator("#f_fecha")).to_have_value(_dia(-2))
    expect(p.locator("#f_hora_inicio")).to_have_value("19:00")
    expect(p.locator("#f_organiza")).to_have_value("subcomision")
    expect(p.locator("#f_subcomision")).to_have_value("Cultura")   # con el nombre como lo conoce la app

    _siguiente(p, 2)
    p.locator("#f_personas_total").fill("25")
    _siguiente(p, 3)
    p.locator("#f_nombre_completa").fill("Juana")
    p.get_by_role("button", name="Enviar").click()
    expect(p.locator(".ok-grande h1")).to_have_text("¡Gracias!")

    (reg,) = registro.listar()
    assert (reg["estado"], reg["datos"]["subcomision"], reg["datos"]["personas_total"]) == \
        ("recibido", "Cultura", 25)
    # El link de una actividad sirve una sola vez.
    p.reload()
    expect(p.locator(".msg.error")).to_have_text(
        "Este link está cerrado. Pedile uno nuevo a la biblioteca.")


def test_resumen_mensual_de_un_taller(celular: Page, bibliotecaria: Page):
    link = _link("taller", "Taller de cerámica", precarga={"titulo": "Taller de cerámica"})
    p = celular
    p.goto(link["url"])
    expect(p.locator("#pantalla h1")).to_have_text("Taller de cerámica")
    p.get_by_role("button", name="Enviar").click()
    expect(p.locator("#error")).to_have_text(
        "Falta el mes, cuántos encuentros se hicieron, cuántas personas participaron, tu nombre.")

    p.locator("#f_mes").fill(HOY.strftime("%Y-%m"))
    p.locator("#f_encuentros").fill("4")
    p.locator("#f_suspendidos").fill("1")
    p.locator("#f_participantes").fill("11")
    p.locator("#f_nombre_completa").fill("Rosa")
    p.get_by_role("button", name="Enviar").click()
    expect(p.locator(".ok-grande h1")).to_have_text("¡Gracias!")
    expect(p.locator(".resumen")).to_contain_text("Personas: 11")

    ir_a(bibliotecaria, "registro")
    tarjeta = _tarjeta(bibliotecaria, "Taller de cerámica")
    expect(tarjeta).to_contain_text(f"Mes de {HOY.strftime('%Y-%m')}")
    expect(tarjeta).to_contain_text("11 participante(s) · 4 encuentro(s) · 1 suspendido(s)")
    expect(tarjeta).to_contain_text("Resumen mensual")
    # El de un taller sirve todos los meses: sigue abierto.
    p.reload()
    expect(p.locator("#f_mes")).to_be_visible()


def test_tope_de_envios_por_conexion(celular: Page):
    link = _link()
    p = celular
    for i in range(routes.TOPE_ENVIOS):
        r = p.request.post(f"/api/publico/registro/{link['token']}",
                           data={"datos": _actividad(f"Envío {i}", 5)})
        assert r.ok, r.text()

    p.goto(link["url"])
    _paso_1(p, "Una más")
    _siguiente(p, 2)
    p.locator("#f_personas_total").fill("5")
    _siguiente(p, 3)
    p.locator("#f_nombre_completa").fill("Marta")
    p.get_by_role("button", name="Enviar").click()
    expect(p.locator("#error")).to_have_text(
        "Se enviaron muchos formularios seguidos. Probá de nuevo en un rato.")
    assert len(registro.listar()) == routes.TOPE_ENVIOS


# ── La bandeja de la biblioteca ─────────────────────────────────────────────
def test_validar_saca_de_recibidos_y_pasa_a_validados(bibliotecaria: Page):
    _recibido("Proyección de cortos", 30)
    _recibido("Feria del libro usado", 60)
    page = bibliotecaria
    ir_a(page, "registro")
    expect(page.locator('#regEstado button[data-estado="recibido"]')).to_have_text("Recibidos (2)")

    _tarjeta(page, "Proyección de cortos").get_by_role("button", name="✓ Validar").click()
    expect(_tarjeta(page, "Proyección de cortos")).to_have_count(0)
    expect(page.locator('#regEstado button[data-estado="recibido"]')).to_have_text("Recibidos (1)")

    _filtrar(page, "validado")
    tarjeta = _tarjeta(page, "Proyección de cortos")
    expect(tarjeta).to_contain_text("Validado")
    expect(tarjeta.get_by_role("button", name="✓ Validar")).to_have_count(0)
    expect(tarjeta.get_by_role("button", name="Borrar")).to_be_visible()
    expect(_tarjeta(page, "Feria del libro usado")).to_have_count(0)


def test_corregir_un_registro_recibido(bibliotecaria: Page):
    r = _recibido("Charla de hueta", 15, subcomision="Cultura")      # con un error de tipeo
    page = bibliotecaria
    ir_a(page, "registro")
    _tarjeta(page, "Charla de hueta").get_by_role("button", name="Corregir").click()

    form = page.locator("#regForm")
    expect(form.locator("h3")).to_have_text("Corregir registro")
    expect(form.locator('[data-reg="personas_total"]')).to_have_value("15")
    expect(form.locator('[data-reg="subcomision"]')).to_have_value("Cultura")
    form.locator('[data-reg="titulo"]').fill("Charla de huerta")
    form.locator('[data-reg="personas_total"]').fill("18")
    form.locator('[data-reg="espacio_id"]').select_option(label="Patio")
    form.locator('[data-tem="ambiente"]').click()
    page.locator("#regGuardar").click()

    expect(form).to_be_hidden()
    tarjeta = _tarjeta(page, "Charla de huerta")
    expect(tarjeta).to_contain_text("Recibido")                       # corregir no valida
    expect(tarjeta.locator(".sol-cuando")).to_contain_text("Patio · 18 persona(s)")
    expect(tarjeta).to_contain_text("Ambiente / huerta")
    expect(tarjeta).to_contain_text("Actividad · Cultura")
    expect(tarjeta).to_contain_text("Corregido por la biblioteca.")
    expect(_tarjeta(page, "Charla de hueta")).to_have_count(0)
    assert registro.obtener(r["id"])["historial"][-1]["antes"]["titulo"] == "Charla de hueta"


def test_corregir_no_borra_el_contacto_de_quien_completo(bibliotecaria: Page):
    r = _recibido("Taller de títeres", 20)
    page = bibliotecaria
    ir_a(page, "registro")
    _tarjeta(page, "Taller de títeres").get_by_role("button", name="Corregir").click()
    page.locator('#regForm [data-reg="personas_total"]').fill("22")
    page.locator("#regGuardar").click()
    expect(_tarjeta(page, "Taller de títeres")).to_contain_text("22 persona(s)")
    # Si hay que preguntarle algo a quien la dio, su contacto tiene que seguir ahí.
    assert registro.obtener(r["id"])["datos"]["quien_completa"]["contacto"] == "marta@example.org"


def test_descartar_pide_motivo_y_lo_muestra(bibliotecaria: Page):
    _recibido("asdasd", 3)
    page = bibliotecaria
    ir_a(page, "registro")

    # Sin motivo no se descarta.
    _tarjeta(page, "asdasd").get_by_role("button", name="Descartar").click()
    page.locator("#ptSi").click()
    expect(_tarjeta(page, "asdasd")).to_contain_text("Recibido")

    _tarjeta(page, "asdasd").get_by_role("button", name="Descartar").click()
    page.locator("#ptValor").fill("Es spam")
    page.locator("#ptSi").click()
    expect(page.locator("#regLista")).to_have_text("No hay registros esperando revisión.")
    expect(page.locator('#regEstado button[data-estado="recibido"]')).to_have_text("Recibidos")

    _filtrar(page, "")
    tarjeta = _tarjeta(page, "asdasd")
    expect(tarjeta).to_contain_text("Descartado")
    expect(tarjeta).to_contain_text("Descartado: Es spam")


def test_carga_manual_desde_la_app_queda_validada(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "registro")
    page.locator("#regNuevo").click()
    form = page.locator("#regForm")
    expect(form.locator("h3")).to_have_text("Cargar una actividad")

    # Sin datos, avisa qué falta.
    page.locator("#regGuardar").click()
    expect(page.locator("#regMsg .msg.error")).to_have_text("Falta el nombre de la actividad.")

    form.locator('[data-reg="titulo"]').fill("Visita de la escuela 12")
    form.locator('[data-reg="tipo"]').select_option("visita")
    form.locator('[data-reg="fecha"]').fill(_dia(-3))
    form.locator('[data-reg="hora_inicio"]').fill("10:00")
    form.locator('[data-reg="hora_fin"]').fill("11:30")
    form.locator('[data-reg="espacio_id"]').select_option(label="Sala principal")
    form.locator('[data-reg="personas_total"]').fill("28")
    form.locator('[data-reg-franja="6_12"]').fill("25")
    form.locator('[data-reg-franja="30_59"]').fill("3")
    page.locator("#regGuardar").click()
    expect(form).to_be_hidden()

    # No pasa por "recibidos": lo cargó la biblioteca.
    expect(page.locator("#regLista")).to_have_text("No hay registros esperando revisión.")
    _filtrar(page, "validado")
    tarjeta = _tarjeta(page, "Visita de la escuela 12")
    expect(tarjeta).to_contain_text("Validado")
    expect(tarjeta.locator(".sol-cuando")).to_have_text(
        f"{_ar(_dia(-3))} · 10:00–11:30 · Sala principal · 28 persona(s)")
    expect(tarjeta).to_contain_text("Visita escolar o institucional")


def test_carga_manual_de_un_resumen_de_taller(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "registro")
    page.locator("#regNuevo").click()
    page.locator('#regClase button[data-clase="taller"]').click()
    form = page.locator("#regForm")
    form.locator('[data-reg="titulo"]').fill("Taller de ajedrez")
    form.locator('[data-reg="mes"]').fill(HOY.strftime("%Y-%m"))
    form.locator('[data-reg="subcomision"]').select_option("Prensa")
    form.locator('[data-reg="encuentros"]').fill("4")
    form.locator('[data-reg="participantes"]').fill("9")
    page.locator("#regGuardar").click()
    expect(form).to_be_hidden()

    _filtrar(page, "validado")
    tarjeta = _tarjeta(page, "Taller de ajedrez")
    expect(tarjeta).to_contain_text("9 participante(s) · 4 encuentro(s)")
    expect(tarjeta).to_contain_text("Resumen mensual · Prensa")


def test_filtro_por_estado(bibliotecaria: Page):
    _recibido("Llegó ayer", 10)
    _validado("Ya revisada", 20)
    _descartado("De prueba", 1)
    page = bibliotecaria
    ir_a(page, "registro")

    def titulos():
        return page.locator("#regLista .sol-card .sol-head b").first

    expect(page.locator("#regLista .sol-card")).to_have_count(1)
    expect(_tarjeta(page, "Llegó ayer")).to_be_visible()

    _filtrar(page, "validado")
    expect(page.locator("#regLista .sol-card")).to_have_count(1)
    expect(_tarjeta(page, "Ya revisada")).to_be_visible()

    _filtrar(page, "")
    expect(page.locator("#regLista .sol-card")).to_have_count(3)
    expect(titulos()).to_have_text("Llegó ayer")                   # lo que espera revisión, primero
    expect(_tarjeta(page, "De prueba")).to_contain_text("Descartado")

    _filtrar(page, "recibido")
    expect(page.locator("#regLista .sol-card")).to_have_count(1)


def test_exportar_csv(bibliotecaria: Page):
    _validado("Charla de memoria", 40, subcomision="Cultura", fecha=_dia(-5),
              tematicas=["memoria"], franjas={"60": 12})
    _recibido("Club de lectura", 7, fecha=_dia(-1))
    _descartado("Duplicada", 1, fecha=_dia(-2))
    page = bibliotecaria
    ir_a(page, "registro")

    with page.expect_download() as descarga:
        page.locator("#regExport").click()
    assert descarga.value.suggested_filename == "actividades.csv"
    with open(descarga.value.path(), encoding="utf-8-sig") as f:
        filas = list(csv.DictReader(io.StringIO(f.read())))
    expect(page.locator("#regExport")).to_have_text("Exportar CSV")

    por_titulo = {f["titulo"]: f for f in filas}
    assert list(por_titulo) == ["Charla de memoria", "Duplicada", "Club de lectura"]   # por fecha
    memoria = por_titulo["Charla de memoria"]
    assert memoria["estado"] == "Validado"
    assert memoria["fecha_o_mes"] == _dia(-5)
    assert memoria["tipo"] == "Charla / conversatorio"
    assert memoria["tematicas"] == "Memoria y DDHH"
    assert memoria["subcomision"] == "Cultura"
    assert memoria["personas"] == "40"
    assert memoria["edad_60"] == "12"
    assert memoria["duracion_min"] == "120"
    assert memoria["completo"] == "Marta Tallerista"
    assert por_titulo["Club de lectura"]["estado"] == "Recibido"
    assert por_titulo["Duplicada"]["estado"] == "Descartado"


# ── "Nuestras actividades": lo de cada subcomisión ──────────────────────────
def test_la_subcomision_ve_solo_lo_suyo(page: Page):
    _recibido("Lectura de poemas", 12, subcomision="Cultura")
    _validado("Peña folclórica", 80, subcomision="Cultura")
    _descartado("Peña repetida", 80, subcomision="Cultura")
    _validado("Taller de radio", 15, subcomision="Prensa")
    _validado("Visita escolar", 30)
    u = crear_usuario("subcomision", subcomision="Cultura")
    entrar(page, u["usuario"], u["clave"])
    ir_a(page, "mias")

    expect(page.locator("#miasIntro")).to_contain_text("Las actividades de Cultura")
    expect(page.locator("#miasLista .sol-card")).to_have_count(2)
    expect(_tarjeta(page, "Lectura de poemas", "#miasLista")).to_contain_text("Recibido")
    expect(_tarjeta(page, "Peña folclórica", "#miasLista")).to_contain_text("Validado")
    for ajena in ("Peña repetida", "Taller de radio", "Visita escolar"):
        expect(page.locator("#miasLista")).not_to_contain_text(ajena)
    # Solo para ver: ni validar, ni corregir, ni borrar.
    expect(page.locator("#miasLista button")).to_have_count(0)
    # Y la bandeja de la biblioteca no está en su menú.
    expect(page.locator('#tabs button[data-tab="registro"]')).to_have_count(0)

    page.locator("#miasNumeros").click()
    expect(page.locator("#actualidadView")).to_be_visible()
    expect(page.locator("#actRango")).to_contain_text("Solo Cultura")
    expect(_kpi(page, "Actividades")).to_have_text("1")
    expect(_kpi(page, "Personas que vinieron")).to_have_text("80")


# ── "Lo que anda pasando en la Bayer" ───────────────────────────────────────
def test_actualidad_muestra_solo_lo_validado(bibliotecaria: Page):
    _validado("Peña folclórica", 40, subcomision="Cultura", primera_vez=5)
    _validado("Charla de memoria", 10, fecha=_dia(-1), hora="10:00", hasta="11:00")
    _recibido("Recital sin revisar", 100)
    _descartado("Recital descartado", 200)
    page = bibliotecaria
    ir_a(page, "actualidad")

    expect(_kpi(page, "Actividades")).to_have_text("2")
    expect(_kpi(page, "Personas que vinieron")).to_have_text("50")
    expect(_kpi(page, "Vinieron por primera vez")).to_have_text("5")
    expect(_kpi(page, "Horas de actividad")).to_have_text("3")
    recientes = page.locator(".viz-card", has_text="Lo último que pasó")
    expect(recientes.locator(".viz-item b")).to_have_text(["Peña folclórica", "Charla de memoria"])
    expect(recientes).to_contain_text("40 personas")
    expect(page.locator(".viz-destacados")).to_contain_text(
        "La actividad con más público fue «Peña folclórica»: 40 personas")
    expect(page.locator("#actContenido")).not_to_contain_text("Recital")

    # Validar lo recibido lo suma.
    ir_a(page, "registro")
    _tarjeta(page, "Recital sin revisar").get_by_role("button", name="✓ Validar").click()
    expect(page.locator("#regLista")).to_have_text("No hay registros esperando revisión.")
    ir_a(page, "actualidad")
    expect(_kpi(page, "Actividades")).to_have_text("3")
    expect(_kpi(page, "Personas que vinieron")).to_have_text("150")
    expect(page.locator(".viz-destacados")).to_contain_text("«Recital sin revisar»: 100 personas")
    expect(page.locator("#actContenido")).not_to_contain_text("Recital descartado")


def test_actualidad_cambiar_periodo_y_subcomision(bibliotecaria: Page):
    _validado("Peña folclórica", 40, subcomision="Cultura")
    _validado("Taller de radio", 15, subcomision="Prensa")
    _validado("Muestra de hace dos meses", 25, fecha=(HOY.replace(day=1) - timedelta(days=20)).isoformat())
    page = bibliotecaria
    ir_a(page, "actualidad")
    expect(_kpi(page, "Actividades")).to_have_text("3")              # últimos 3 meses

    page.locator('#actPeriodo button[data-periodo="mes"]').click()
    expect(_kpi(page, "Actividades")).to_have_text("2")
    expect(page.locator("#actRango")).to_contain_text(f"Del {_ar(HOY.replace(day=1).isoformat())} al {_ar(HOY.isoformat())}")

    page.locator('#actPeriodo button[data-periodo="anio_anterior"]').click()
    expect(page.locator(".viz-vacio")).to_contain_text(
        "Todavía no hay actividades registradas en este período")
    expect(page.locator(".viz-kpi")).to_have_count(0)

    page.locator('#actPeriodo button[data-periodo="12m"]').click()
    expect(_kpi(page, "Actividades")).to_have_text("3")
    expect(page.locator("#actDe option")).to_have_text(["Toda la Bayer", "Cultura", "Prensa"])
    page.locator("#actDe").select_option("Cultura")
    expect(page.locator("#actRango")).to_contain_text("Solo Cultura")
    expect(_kpi(page, "Actividades")).to_have_text("1")
    expect(_kpi(page, "Personas que vinieron")).to_have_text("40")
    expect(page.locator("#actContenido")).not_to_contain_text("Taller de radio")

    page.locator("#actDe").select_option("")
    expect(_kpi(page, "Personas que vinieron")).to_have_text("80")


def test_actualidad_para_una_subcomision(page: Page):
    _validado("Peña folclórica", 40, subcomision="Cultura")
    _validado("Taller de radio", 15, subcomision="Prensa")
    u = crear_usuario("subcomision", subcomision="Cultura")
    entrar(page, u["usuario"], u["clave"])
    ir_a(page, "actualidad")
    # Ve toda la Bayer (lo validado no es sensible) o solo lo suyo; no lo de otras.
    expect(_kpi(page, "Actividades")).to_have_text("2")
    expect(page.locator("#actDe option")).to_have_text(["Toda la Bayer", "Lo de Cultura"])
    page.locator("#actDe").select_option("Cultura")
    expect(_kpi(page, "Actividades")).to_have_text("1")
    expect(page.locator("#actContenido")).not_to_contain_text("Taller de radio")

    r = page.request.get("/api/panorama?subcomision=Prensa",
                         headers={"Authorization": "Bearer " + page.evaluate("token")})
    assert r.status == 403
