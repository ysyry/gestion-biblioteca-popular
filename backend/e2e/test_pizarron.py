"""El pizarrón semanal del equipo de la biblioteca.

Avisos, tareas y recordatorios por semana. Reglas (ver `app/pizarron.py`):
  · Editar o borrar una nota, o borrar una respuesta: solo quien la escribió.
  · Tildar una tarea y responder: cualquiera del equipo.
  · Una tarea sin hacer pasa sola a las semanas siguientes; un aviso queda en su semana.
  · Las semanas pasadas se miran, no se tocan.
  · El menú avisa cuántas notas y respuestas de OTRAS personas hay desde la última visita.

Para las pruebas con dos bibliotecarias se usa la cuenta de servicio de Koha, que
también entra ("servicio" / "servicio").
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from playwright.sync_api import Browser, Page, expect

from app import pizarron, storage

from . import servidor
from .ayudas import entrar, ir_a

OTRA, CLAVE_OTRA = "servicio", "servicio"


# ── Ayudas de este archivo ──────────────────────────────────────────────────
@contextmanager
def _otra_bibliotecaria(browser: Browser, base_url: str):
    """Otra bibliotecaria, en su propia ventana, ya adentro."""
    contexto = browser.new_context(base_url=base_url)
    pagina = contexto.new_page()
    errores: list[str] = []
    pagina.on("pageerror", lambda e: errores.append(str(e)))
    pagina.on("console", lambda m: errores.append(m.text) if m.type == "error"
              and "Failed to load resource" not in m.text else None)
    try:
        entrar(pagina, OTRA, CLAVE_OTRA)
        yield pagina
    finally:
        contexto.close()
    assert not errores, "Errores de JavaScript en la otra ventana:\n" + "\n".join(errores)


def _abrir(page: Page) -> None:
    ir_a(page, "pizarron")
    expect(page.locator("#pizSub")).to_have_text("Esta semana")


def _nota(page: Page, texto: str):
    return page.locator("#pizContenido .postit", has_text=texto)


def _bloque(page: Page, titulo: str):
    return page.locator("#pizContenido .piz-bloque", has=page.locator("h4", has_text=titulo))


def _nueva(page: Page, texto: str, tipo: str = "aviso", para: str = "") -> None:
    page.locator("#pizNueva").click()
    expect(page.locator("#pizForm h4")).to_have_text("Nueva nota")
    page.locator("#pizTexto").fill(texto)
    page.locator("#pizTipo").select_option(tipo)
    page.locator("#pizPara").fill(para)
    page.locator("#pizGuardar").click()
    expect(page.locator("#pizForm")).to_be_hidden()
    expect(_nota(page, texto)).to_be_visible()


def _responder(page: Page, texto_nota: str, respuesta: str) -> None:
    nota = _nota(page, texto_nota)
    nota.get_by_role("button", name="Responder").click()
    campo = nota.locator(".piz-responder input")
    campo.fill(respuesta)
    campo.press("Enter")
    expect(nota.locator(".piz-resp", has_text=respuesta)).to_be_visible()


def _badge(page: Page):
    return page.locator('#tabs button[data-tab="pizarron"] .menu-badge')


def _novedades(page: Page) -> int:
    """Lo que el servidor le contesta al menú de esa persona."""
    token = page.evaluate("localStorage.getItem('token')")
    r = page.request.get("/api/pizarron/novedades", headers={"Authorization": f"Bearer {token}"})
    assert r.ok
    return r.json()["nuevas"]


def _hace_un_rato(usuario: str) -> None:
    """Corre para atrás la última visita de alguien al pizarrón.

    Las visitas se guardan al segundo: sin esto, una nota escrita en el mismo segundo
    en que la otra miró podría no contar como novedad. En la vida real pasan minutos.
    """
    visto = storage.get(pizarron.CLAVE_VISTO) or {}
    antes = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")
    visto[usuario.casefold()]["cuando"] = antes
    storage.set(pizarron.CLAVE_VISTO, visto)


def _escrita_hace(nota: dict, dias: int) -> None:
    """Le pone a una nota cargada desde el test la fecha en que se habría escrito."""
    anio, lista, n = pizarron._ubicar(nota["id"])
    n["creada"] = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat(timespec="seconds")
    storage.set(pizarron._clave(anio), lista)


def _dm(d) -> str:
    return f"{d.day}/{d.month}"


# ── Una bibliotecaria ───────────────────────────────────────────────────────
def test_crear_un_aviso(bibliotecaria: Page):
    page = bibliotecaria
    _abrir(page)
    expect(_bloque(page, "Toda la semana")).to_contain_text("Nada anotado para toda la semana")

    _nueva(page, "Llegó la donación de la escuela 5")
    nota = _nota(page, "Llegó la donación de la escuela 5")
    expect(_bloque(page, "Toda la semana")).to_contain_text("Llegó la donación de la escuela 5")
    expect(nota.locator(".piz-chips")).to_contain_text("Aviso")
    expect(nota.locator(".piz-pie")).to_contain_text(servidor.USUARIO_KOHA)
    # Una nota propia se puede editar y borrar; un aviso no se tilda.
    expect(nota.get_by_role("button", name="Editar")).to_be_visible()
    expect(nota.get_by_role("button", name="Borrar")).to_be_visible()
    expect(nota.get_by_role("button", name="✓ Hecha")).to_have_count(0)

    # Queda guardada: al volver a entrar sigue ahí.
    page.reload()
    _abrir(page)
    expect(_nota(page, "Llegó la donación de la escuela 5")).to_be_visible()


def test_nota_vacia_no_se_guarda(bibliotecaria: Page):
    page = bibliotecaria
    _abrir(page)
    page.locator("#pizNueva").click()
    page.locator("#pizTexto").fill("   ")
    page.locator("#pizGuardar").click()
    expect(page.locator("#pizMsg .msg.error")).to_have_text("La nota no puede estar vacía.")
    expect(page.locator("#pizForm")).to_be_visible()
    page.locator("#pizCancelar").click()
    expect(page.locator("#pizForm")).to_be_hidden()
    expect(page.locator("#pizContenido .postit")).to_have_count(0)


def test_editar_una_nota(bibliotecaria: Page):
    page = bibliotecaria
    _abrir(page)
    _nueva(page, "Reunión el jueves")
    _nota(page, "Reunión el jueves").get_by_role("button", name="Editar").click()
    expect(page.locator("#pizForm h4")).to_have_text("Editar nota")
    expect(page.locator("#pizTexto")).to_have_value("Reunión el jueves")
    page.locator("#pizTexto").fill("Reunión el viernes a las 18")
    page.locator("#pizTipo").select_option("recordatorio")
    page.locator("#pizGuardar").click()

    expect(page.locator("#pizForm")).to_be_hidden()
    expect(_nota(page, "Reunión el jueves")).to_have_count(0)
    nota = _nota(page, "Reunión el viernes a las 18")
    expect(nota.locator(".piz-chips")).to_contain_text("Recordatorio")
    expect(nota.locator(".piz-pie")).to_contain_text("editada")


def test_marcar_una_tarea_como_hecha_y_desmarcarla(bibliotecaria: Page):
    page = bibliotecaria
    _abrir(page)
    _nueva(page, "Ordenar el estante de poesía", tipo="tarea")
    nota = _nota(page, "Ordenar el estante de poesía")
    expect(nota.locator(".piz-chips")).to_contain_text("Tarea")
    nota.get_by_role("button", name="✓ Hecha").click()

    # Pasa a "hechas", plegada, y ya no está en "Toda la semana".
    hechas = page.locator("#pizContenido details.piz-bloque")
    expect(hechas.locator("summary")).to_contain_text("Tareas hechas esta semana (1)")
    expect(_bloque(page, "Toda la semana")).not_to_contain_text("Ordenar el estante")
    hechas.locator("summary").click()
    expect(nota).to_have_class(re.compile(r"\bhecha\b"))
    expect(nota.locator(".piz-hecha")).to_contain_text(f"Hecha por {servidor.USUARIO_KOHA}")

    nota.get_by_role("button", name="Desmarcar").click()
    expect(hechas).to_have_count(0)
    expect(_bloque(page, "Toda la semana")).to_contain_text("Ordenar el estante de poesía")
    expect(_nota(page, "Ordenar el estante").get_by_role("button", name="✓ Hecha")).to_be_visible()


def test_responder_y_borrar_la_respuesta(bibliotecaria: Page):
    page = bibliotecaria
    _abrir(page)
    _nueva(page, "¿Quién abre el sábado?")
    _responder(page, "¿Quién abre el sábado?", "Yo me encargo")
    nota = _nota(page, "¿Quién abre el sábado?")
    expect(nota.locator(".piz-resp")).to_have_text(f"{servidor.USUARIO_KOHA}: Yo me encargo×")

    nota.locator(".piz-resp .piz-x").click()
    expect(nota.locator(".piz-resp")).to_have_count(0)
    # Una respuesta vacía ni se manda.
    nota.get_by_role("button", name="Responder").click()
    nota.locator(".piz-responder input").press("Enter")
    expect(nota.locator(".piz-resp")).to_have_count(0)


def test_borrar_una_nota(bibliotecaria: Page):
    page = bibliotecaria
    _abrir(page)
    _nueva(page, "Nota que sobra")
    _nueva(page, "Nota que queda")
    _nota(page, "Nota que sobra").get_by_role("button", name="Borrar").click()
    expect(page.locator(".modal h3")).to_have_text("Borrar nota")

    page.locator("#ucNo").click()                        # primero, se arrepiente
    expect(_nota(page, "Nota que sobra")).to_be_visible()

    _nota(page, "Nota que sobra").get_by_role("button", name="Borrar").click()
    page.locator("#ucYes").click()
    expect(_nota(page, "Nota que sobra")).to_have_count(0)
    expect(_nota(page, "Nota que queda")).to_be_visible()


def test_navegar_semanas(bibliotecaria: Page):
    page = bibliotecaria
    lunes = pizarron.lunes(pizarron.hoy())
    pizarron.crear({"texto": "Lo que pasó la semana pasada",
                    "semana": (lunes - timedelta(days=7)).isoformat()},
                   servidor.USUARIO_KOHA, servidor.USUARIO_KOHA)
    _abrir(page)
    expect(page.locator("#pizHoy")).to_be_disabled()
    _nueva(page, "Llamar al plomero", tipo="tarea")
    _nueva(page, "Hoy cerramos temprano")
    titulo_actual = page.locator("#pizTitulo").inner_text()

    # La semana pasada: se mira, no se toca.
    page.locator("#pizPrev").click()
    expect(page.locator("#pizSub")).to_have_text("Semana pasada · solo lectura")
    expect(page.locator("#pizTitulo")).not_to_have_text(titulo_actual)
    expect(page.locator("#pizNueva")).to_be_hidden()
    expect(page.locator("#pizHoy")).to_be_enabled()
    vieja = _nota(page, "Lo que pasó la semana pasada")
    expect(vieja).to_be_visible()
    expect(vieja.locator(".piz-acciones")).to_have_count(0)   # ni editar, ni responder
    expect(_nota(page, "Llamar al plomero")).to_have_count(0)

    # La que viene: la tarea sin hacer pasa sola; el aviso se queda en su semana.
    page.locator("#pizNext").click()
    expect(page.locator("#pizSub")).to_have_text("Esta semana")
    page.locator("#pizNext").click()
    expect(page.locator("#pizSub")).to_have_text("Semana próxima")
    expect(page.locator("#pizNueva")).to_be_visible()
    tarea = _nota(page, "Llamar al plomero")
    expect(tarea).to_contain_text(f"Viene de la semana del {_dm(lunes)}")
    expect(_nota(page, "Hoy cerramos temprano")).to_have_count(0)

    page.locator("#pizHoy").click()
    expect(page.locator("#pizSub")).to_have_text("Esta semana")
    expect(page.locator("#pizTitulo")).to_have_text(titulo_actual)
    expect(_nota(page, "Hoy cerramos temprano")).to_be_visible()


def test_una_tarea_hecha_no_pasa_a_la_semana_siguiente(bibliotecaria: Page):
    page = bibliotecaria
    _abrir(page)
    _nueva(page, "Pagar la luz", tipo="tarea")
    _nota(page, "Pagar la luz").get_by_role("button", name="✓ Hecha").click()
    expect(page.locator("#pizContenido details.piz-bloque")).to_be_visible()
    page.locator("#pizNext").click()
    expect(page.locator("#pizSub")).to_have_text("Semana próxima")
    expect(page.locator("#pizContenido")).not_to_contain_text("Pagar la luz")


def test_buscar_en_el_pizarron(bibliotecaria: Page):
    page = bibliotecaria
    lunes = pizarron.lunes(pizarron.hoy())
    vieja = pizarron.crear({"texto": "Se rompió la impresora",
                            "semana": (lunes - timedelta(days=14)).isoformat()},
                           servidor.USUARIO_KOHA, servidor.USUARIO_KOHA)
    _escrita_hace(vieja, dias=14)
    pizarron.responder(vieja["id"], "Hay que llamar al técnico de la fotocopiadora",
                       OTRA, OTRA)
    _abrir(page)
    _nueva(page, "Comprar tóner para la fotocopiadora")
    _nueva(page, "Regar las plantas")

    buscador = page.locator("#pizQ")
    buscador.fill("FOTOCOPIADORA")                       # sin distinguir mayúsculas
    buscador.press("Enter")
    expect(page.locator("#pizSub")).to_have_text(
        "Búsqueda: “FOTOCOPIADORA” · 2 nota(s) de los últimos años")
    expect(page.locator("#pizContenido .postit")).to_have_count(2)
    # La vieja aparece por lo que dice su respuesta; las más nuevas, primero.
    textos = page.locator("#pizContenido .piz-texto").all_inner_texts()
    assert textos == ["Comprar tóner para la fotocopiadora", "Se rompió la impresora"]
    expect(page.locator("#pizNueva")).to_be_hidden()
    expect(_nota(page, "Se rompió la impresora")).to_contain_text(
        f"Semana del {_dm(lunes - timedelta(days=14))}")

    # Desde un resultado se va a su semana.
    _nota(page, "Se rompió la impresora").get_by_role("button", name="Ir a esa semana").click()
    expect(page.locator("#pizSub")).to_have_text("Semana pasada · solo lectura")
    expect(buscador).to_have_value("")
    expect(_nota(page, "Se rompió la impresora")).to_be_visible()

    buscador.fill("bicicleta")
    buscador.press("Enter")
    expect(page.locator("#pizContenido")).to_have_text("Ninguna nota del pizarrón tiene ese texto.")

    # Borrar la búsqueda vuelve a la semana.
    buscador.fill("")
    buscador.press("Enter")
    expect(page.locator("#pizContenido .postit")).to_have_count(1)
    expect(page.locator("#pizSub")).not_to_contain_text("Búsqueda")


# ── Dos bibliotecarias ──────────────────────────────────────────────────────
def test_la_otra_ve_la_novedad_en_el_menu(bibliotecaria: Page, browser, base_url):
    page = bibliotecaria
    _abrir(page)
    _nueva(page, "Mañana viene el camión de libros")

    with _otra_bibliotecaria(browser, base_url) as otra:
        # Entra, arranca en Inicio, y el menú le avisa.
        expect(_badge(otra)).to_have_text("1")
        expect(otra.locator('#tabs button[data-tab="pizarron"]')).to_have_attribute(
            "title", "1 novedad(es) en el pizarrón desde tu última visita")

        # Abrir el pizarrón cuenta como visita: el aviso se va y no vuelve.
        _abrir(otra)
        expect(_nota(otra, "Mañana viene el camión de libros")).to_be_visible()
        expect(_badge(otra)).to_have_count(0)
        otra.reload()
        expect(otra.locator("#appView")).to_be_visible()
        expect(otra.locator("#tabs button.active")).to_have_count(1)
        expect(_badge(otra)).to_have_count(0)
        assert _novedades(otra) == 0

        # Lo que escribe una misma no es novedad para ella.
        _abrir(otra)
        _responder(otra, "Mañana viene el camión de libros", "Lo recibo yo")
        _hace_un_rato(servidor.USUARIO_KOHA)
        otra.reload()
        expect(otra.locator("#tabs button.active")).to_have_count(1)
        expect(_badge(otra)).to_have_count(0)
        assert _novedades(otra) == 0

    # Y la respuesta de la otra, para la primera sí es novedad.
    ir_a(page, "envios")
    page.reload()
    expect(page.locator("#tabs button.active")).to_have_count(1)
    expect(_badge(page)).to_have_text("1")
    assert _novedades(page) == 1


def test_la_nota_de_otra_se_responde_y_se_tilda_pero_no_se_edita(bibliotecaria: Page, browser,
                                                                  base_url):
    page = bibliotecaria
    _abrir(page)
    _nueva(page, "Revisar las devoluciones del buzón", tipo="tarea")
    _responder(page, "Revisar las devoluciones del buzón", "Empiezo por la A")

    with _otra_bibliotecaria(browser, base_url) as otra:
        _abrir(otra)
        nota = _nota(otra, "Revisar las devoluciones del buzón")
        expect(nota.get_by_role("button", name="Editar")).to_have_count(0)
        expect(nota.get_by_role("button", name="Borrar")).to_have_count(0)
        expect(nota.locator(".piz-resp .piz-x")).to_have_count(0)     # la respuesta es ajena
        expect(nota.get_by_role("button", name="Responder")).to_be_visible()

        # Tampoco por la API.
        nota_id = next(n["id"] for n in pizarron._leer(pizarron.lunes(pizarron.hoy()).year))
        resp_id = pizarron._leer(pizarron.lunes(pizarron.hoy()).year)[0]["respuestas"][0]["id"]
        token = otra.evaluate("localStorage.getItem('token')")
        cab = {"Authorization": f"Bearer {token}"}
        r = otra.request.put(f"/api/pizarron/{nota_id}", data={"texto": "cambiada"}, headers=cab)
        assert r.status == 403
        assert r.json()["detail"] == "Solo quien escribió la nota puede editarla."
        r = otra.request.delete(f"/api/pizarron/{nota_id}", headers=cab)
        assert r.status == 403
        r = otra.request.delete(f"/api/pizarron/{nota_id}/respuestas/{resp_id}", headers=cab)
        assert r.status == 403
        assert r.json()["detail"] == "Solo quien escribió la respuesta puede borrarla."

        # Tildar y responder, sí: es trabajo de todo el equipo.
        _responder(otra, "Revisar las devoluciones del buzón", "Yo sigo desde la M")
        mia = nota.locator(".piz-resp", has_text="Yo sigo desde la M")
        expect(mia.locator(".piz-x")).to_be_visible()
        nota.get_by_role("button", name="✓ Hecha").click()
        otra.locator("#pizContenido details.piz-bloque summary").click()
        expect(nota.locator(".piz-hecha")).to_contain_text(f"Hecha por {OTRA}")

    # La autora ve todo lo que hizo la otra, y la nota sigue siendo suya.
    page.reload()
    _abrir(page)
    page.locator("#pizContenido details.piz-bloque summary").click()
    nota = _nota(page, "Revisar las devoluciones del buzón")
    expect(nota.locator(".piz-hecha")).to_contain_text(f"Hecha por {OTRA}")
    expect(nota.locator(".piz-resp")).to_have_count(2)
    expect(nota.locator(".piz-resp", has_text="Yo sigo desde la M").locator(".piz-x")).to_have_count(0)
    expect(nota.get_by_role("button", name="Editar")).to_be_visible()


def test_nota_dirigida_a_alguien(bibliotecaria: Page, browser, base_url):
    page = bibliotecaria
    _abrir(page)
    _nueva(page, "Llamar a la editorial", para=OTRA)
    expect(_nota(page, "Llamar a la editorial").locator(".piz-para")).to_have_text(f"Para {OTRA}")

    with _otra_bibliotecaria(browser, base_url) as otra:
        _abrir(otra)
        para = _nota(otra, "Llamar a la editorial").locator(".piz-para")
        expect(para).to_have_text("Para vos")
        expect(para).to_have_class(re.compile(r"\bmia\b"))
        # Ahora que las dos pasaron por el pizarrón, se sugieren al escribir "Para".
        otra.locator("#pizPrev").click()
        otra.locator("#pizHoy").click()
        expect(otra.locator("#pizSub")).to_have_text("Esta semana")
        otra.locator("#pizNueva").click()
        sugeridas = otra.locator("#pizPersonas option").evaluate_all(
            "os => os.map(o => o.value)")
        assert {servidor.USUARIO_KOHA, OTRA} <= set(sugeridas)
