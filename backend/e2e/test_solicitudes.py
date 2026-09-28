"""Solicitudes de espacio: una subcomisión pide, la biblioteca resuelve, a quien pidió le llega.

El circuito entero con dos personas a la vez, cada una en su navegador:

  · Marta (subcomisión Cultura) pide la Sala principal, corrige, cancela.
  · La bibliotecaria (o alguien de comisión) aprueba, rechaza, pide cambios o mueve
    fecha y espacio al aprobar.
  · A Marta le llega un mail con lo que quedó, y el aviso queda en el historial de envíos.

Los espacios arrancan con "Sala principal" y "Patio" (variable ESPACIOS en servidor.py).
Google Calendar está apagado: lo aprobado se ve solo en el calendario de la app.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable

import pytest
from playwright.sync_api import Browser, Page, expect

from app import espacios, historial, solicitudes, usuarios

from . import datos, servidor
from .ayudas import crear_usuario, entrar, ir_a

# La actividad de las pruebas: dentro de dos semanas, de 18 a 20.
FECHA = datos.HOY + dt.timedelta(days=14)
DIA = FECHA.isoformat()
MAIL_MARTA = "marta@example.org"

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]


def _cuando(dia: dt.date, desde: str, hasta: str) -> str:
    """Cómo dice el mail la fecha: 'lunes 12 de octubre, de 18:00 a 20:00'."""
    return f"{_DIAS[dia.weekday()]} {dia.day} de {_MESES[dia.month - 1]}, de {desde} a {hasta}"


# ── Gente ───────────────────────────────────────────────────────────────────────
def _marta(email: str = MAIL_MARTA) -> dict:
    """Usuaria de la subcomisión Cultura, con mail cargado (a donde van los avisos)."""
    u = crear_usuario("subcomision", usuario="marta", subcomision="Cultura")
    usuarios.actualizar(u["id"], {"nombre": "Marta Ríos", "email": email})
    return {**u, "nombre": "Marta Ríos", "email": email}


@pytest.fixture
def abrir(browser: Browser, base_url: str):
    """Abre otro navegador (otra persona, otra sesión) ya adentro de la app.

    Igual que `page`, falla si la página tira errores de JavaScript.
    """
    contextos, errores = [], []

    def _abrir(usuario: str, clave: str) -> Page:
        ctx = browser.new_context(base_url=base_url)
        contextos.append(ctx)
        p = ctx.new_page()
        p.on("pageerror", lambda e: errores.append(str(e)))
        p.on("console", lambda m: errores.append(m.text) if m.type == "error"
             and "Failed to load resource" not in m.text else None)
        entrar(p, usuario, clave)
        return p

    yield _abrir
    for ctx in contextos:
        ctx.close()
    assert not errores, "Errores de JavaScript en la página:\n" + "\n".join(errores)


@pytest.fixture
def marta(abrir: Callable[[str, str], Page]) -> Page:
    """Marta, adentro y parada en Solicitudes."""
    u = _marta()
    p = abrir(u["usuario"], u["clave"])
    ir_a(p, "solicitudes")
    return p


# ── Datos cargados por atrás (lo que ya estaba antes de que empiece la prueba) ──
def _espacio(nombre: str) -> str:
    return next(e["id"] for e in espacios.listar(incluir_inactivos=True) if e["nombre"] == nombre)


def _sembrar(titulo: str, *, espacio: str = "Sala principal", dia: str = DIA,
             desde: str = "18:00", hasta: str = "20:00", quien: dict | None = None,
             aprobada: bool = False) -> dict:
    quien = quien or {"uid": None, "usuario": "otra", "nombre": "Otra Persona",
                      "subcomision": "Prensa"}
    s = solicitudes.crear({"titulo": titulo, "espacio_id": _espacio(espacio),
                           "inicio": f"{dia}T{desde}", "fin": f"{dia}T{hasta}"}, quien)
    if aprobada:
        s = solicitudes.resolver(s["id"], "aprobar", por="biblio")
    return s


def _de(u: dict) -> dict:
    return {"uid": u["id"], "usuario": u["usuario"], "nombre": u["nombre"],
            "subcomision": u.get("subcomision", "")}


# ── Pantalla ────────────────────────────────────────────────────────────────────
def _tarjeta(page: Page, titulo: str):
    return page.locator("#solLista .sol-card", has_text=titulo)


def _llenar(page: Page, titulo: str, *, espacio: str = "Sala principal", dia: str = DIA,
            desde: str = "18:00", hasta: str = "20:00") -> None:
    page.locator("#sfTitulo").fill(titulo)
    page.locator("#sfEspacio").select_option(label=espacio)
    page.locator("#sfInicio").fill(f"{dia}T{desde}")
    page.locator("#sfFin").fill(f"{dia}T{hasta}")


def _pedir(page: Page, titulo: str, **kw) -> None:
    """Pide un espacio desde el formulario y espera la confirmación."""
    page.locator("#solNueva").click()
    _llenar(page, titulo, **kw)
    page.locator("#sfGuardar").click()
    expect(page.locator("#solMsg")).to_contain_text("tu solicitud quedó enviada")
    expect(_tarjeta(page, titulo)).to_be_visible()


def _refrescar(page: Page) -> None:
    page.locator("#solRef").click()
    expect(page.locator("#solLista .loader, #solLista .loadbar")).to_have_count(0)


def _abrir_resolver(page: Page, titulo: str):
    _tarjeta(page, titulo).get_by_role("button", name="Resolver").click()
    modal = page.locator(".modal", has_text=f"Resolver: {titulo}")
    expect(modal).to_be_visible()
    return modal


def _esperar_mails(page: Page, bandeja: list[dict], cantidad: int = 1) -> list[dict]:
    """Los avisos salen en segundo plano, después de responder: se espera un ratito."""
    for _ in range(50):
        if len(bandeja) >= cantidad:
            break
        page.wait_for_timeout(100)
    assert len(bandeja) == cantidad, f"Se esperaban {cantidad} mails y llegaron {len(bandeja)}"
    return bandeja


def _refrescar_hasta_ver(page: Page, texto: str) -> None:
    """El aviso se anota en la solicitud al rato: se vuelve a mirar hasta que aparece."""
    for _ in range(20):
        _refrescar(page)
        if page.locator("#solLista", has_text=texto).count():
            return
        page.wait_for_timeout(150)
    expect(page.locator("#solLista")).to_contain_text(texto)


def _hora(hhmm: str) -> re.Pattern:
    """Una hora como la muestra la pantalla: '18:00' o, según el navegador, '06:00 p. m.'.

    (Que salga en formato de 12 horas tiene su propia prueba, más abajo.)
    """
    h, m = map(int, hhmm.split(":"))
    doce = f"{(h % 12) or 12:02d}:{m:02d}\\s*{'p' if h >= 12 else 'a'}\\.\\s*m\\."
    return re.compile(f"{hhmm}|{doce}")


def _sin_etiquetas(m: dict) -> None:
    assert "{{" not in m["subject"] and "{{" not in m["plain"] and "{{" not in m["html"]


# ═══ La subcomisión pide ════════════════════════════════════════════════════════
def test_subcomision_pide_un_espacio_y_lo_ve_pendiente(marta: Page, bandeja):
    page = marta
    expect(page.locator("#solLista")).to_contain_text("Todavía no hay solicitudes")
    expect(page.locator("#solEspacios")).to_be_hidden()      # los espacios no son cosa suya

    page.locator("#solNueva").click()
    expect(page.locator("#solForm")).to_contain_text("Solicitar un espacio")
    opciones = page.locator("#sfEspacio option").all_inner_texts()
    assert opciones == ["Patio", "Sala principal"]

    _llenar(page, "Taller de radio")
    page.locator("#sfDesc").fill("Para adolescentes del barrio")
    page.locator("#sfPersonas").fill("15")
    page.locator("#solForm label.chk", has_text="Proyector").locator("input").check()
    page.locator("#sfNecOtro").fill("Alargue")
    page.locator("#sfPublica").check()
    expect(page.locator("#sfConflictos")).to_contain_text("El espacio está libre en ese horario.")
    page.locator("#sfGuardar").click()

    expect(page.locator("#solMsg")).to_contain_text(
        "Listo: tu solicitud quedó enviada. Te van a avisar cuando la resuelvan.")
    expect(page.locator("#solForm")).to_be_hidden()
    t = _tarjeta(page, "Taller de radio")
    expect(t.locator(".badge")).to_have_text("Pendiente")
    expect(t.locator(".sol-cuando")).to_contain_text("Sala principal")
    expect(t.locator(".sol-cuando")).to_contain_text(_hora("18:00"))
    expect(t.locator(".sol-cuando")).to_contain_text(_hora("20:00"))
    expect(t).to_contain_text("Marta Ríos · Cultura")
    expect(t).to_contain_text("Para adolescentes del barrio")
    expect(t).to_contain_text("15 personas")
    expect(t).to_contain_text("Abierta al público")
    expect(t).to_contain_text("Gratuita")
    expect(t.locator(".sol-nec .tag")).to_have_text(["Proyector", "Alargue"])
    # Puede corregirla o darla de baja, pero no resolverla.
    expect(t.get_by_role("button", name="Editar")).to_be_visible()
    expect(t.get_by_role("button", name="Cancelar")).to_be_visible()
    expect(t.get_by_role("button", name="Resolver")).to_have_count(0)

    guardada = solicitudes.listar()[0]
    assert guardada["solicitante"]["subcomision"] == "Cultura"
    assert (guardada["inicio"], guardada["fin"]) == (f"{DIA}T18:00", f"{DIA}T20:00")
    assert bandeja == []                                     # pedir no manda mails


def test_el_formulario_no_deja_mandar_un_pedido_incompleto(marta: Page):
    page = marta
    page.locator("#solNueva").click()

    # Sin decir qué actividad es.
    _llenar(page, "")
    page.locator("#sfGuardar").click()
    expect(page.locator("#solMsg .msg.error")).to_have_text("Falta decir qué actividad es.")

    # Termina antes de empezar: ya lo avisa mientras se completa, y no deja mandarlo.
    _llenar(page, "Cine debate", desde="20:00", hasta="18:00")
    expect(page.locator("#sfConflictos")).to_contain_text(
        "La hora de fin tiene que ser posterior a la de inicio.")
    page.locator("#sfGuardar").click()
    expect(page.locator("#solMsg .msg.error")).to_have_text(
        "La hora de fin tiene que ser posterior a la de inicio.")

    # Se repite, pero sin decir hasta cuándo.
    _llenar(page, "Cine debate")
    expect(page.locator("#sfHastaBox")).to_be_hidden()
    page.locator("#sfRep").select_option(label="Todas las semanas")
    expect(page.locator("#sfHastaBox")).to_be_visible()
    page.locator("#sfGuardar").click()
    expect(page.locator("#solMsg .msg.error")).to_have_text(
        "Si la actividad se repite, hay que decir hasta cuándo.")

    # Con el "hasta", cuenta las fechas; y ahí sí sale.
    hasta = FECHA + dt.timedelta(days=21)
    page.locator("#sfHasta").fill(hasta.isoformat())
    expect(page.locator("#sfConflictos")).to_contain_text(
        "Son 4 fechas y el espacio está libre en todas.")
    expect(page.locator("#solForm")).to_be_visible()          # los errores no cerraron nada
    assert solicitudes.listar() == []
    page.locator("#sfGuardar").click()
    t = _tarjeta(page, "Cine debate")
    expect(t).to_contain_text("4 fechas")
    expect(t).to_contain_text("Todas las semanas")
    assert len(solicitudes.listar()[0]["fechas"]) == 4


def test_avisa_si_se_pisa_con_otra_reserva_aprobada(marta: Page):
    _sembrar("Asamblea de Prensa", desde="17:00", hasta="19:00", aprobada=True)
    _sembrar("Algo todavía sin aprobar", desde="18:00", hasta="20:00")   # no ocupa
    page = marta
    page.locator("#solNueva").click()

    _llenar(page, "Taller de radio")
    aviso = page.locator("#sfConflictos")
    expect(aviso).to_contain_text("Ojo, se pisa con otra reserva:")
    expect(aviso).to_contain_text("Asamblea de Prensa")
    expect(aviso).to_contain_text("(Otra Persona)")
    expect(aviso).to_contain_text("Podés mandarla igual")
    expect(aviso).not_to_contain_text("sin aprobar")

    # En otro espacio, libre.
    page.locator("#sfEspacio").select_option(label="Patio")
    expect(aviso).to_contain_text("El espacio está libre en ese horario.")

    # Pegada a la otra (arranca cuando la otra termina) no es pisarse.
    page.locator("#sfEspacio").select_option(label="Sala principal")
    page.locator("#sfInicio").fill(f"{DIA}T19:00")
    page.locator("#sfFin").fill(f"{DIA}T21:00")
    expect(aviso).to_contain_text("El espacio está libre en ese horario.")

    # No bloquea: con el cruce a la vista, se puede mandar igual.
    page.locator("#sfInicio").fill(f"{DIA}T18:00")
    expect(aviso).to_contain_text("Asamblea de Prensa")
    page.locator("#sfGuardar").click()
    expect(_tarjeta(page, "Taller de radio").locator(".badge")).to_have_text("Pendiente")


def test_subcomision_corrige_su_pedido_y_despues_lo_cancela(marta: Page, bandeja):
    page = marta
    _pedir(page, "Taller de radio")

    _tarjeta(page, "Taller de radio").get_by_role("button", name="Editar").click()
    expect(page.locator("#solForm")).to_contain_text("Editar la solicitud")
    expect(page.locator("#sfTitulo")).to_have_value("Taller de radio")
    expect(page.locator("#sfInicio")).to_have_value(f"{DIA}T18:00")
    expect(page.locator("#sfEspacio")).to_have_value(_espacio("Sala principal"))
    page.locator("#sfTitulo").fill("Taller de radio comunitaria")
    page.locator("#sfEspacio").select_option(label="Patio")
    page.locator("#sfFin").fill(f"{DIA}T21:30")
    page.locator("#sfGuardar").click()

    expect(page.locator("#solMsg")).to_contain_text("Listo: la solicitud quedó actualizada.")
    t = _tarjeta(page, "Taller de radio comunitaria")
    expect(t.locator(".sol-cuando")).to_contain_text("Patio")
    expect(t.locator(".sol-cuando")).to_contain_text(_hora("21:30"))
    expect(page.locator("#solLista .sol-card")).to_have_count(1)   # se editó, no se duplicó

    t.get_by_role("button", name="Cancelar").click()
    confirmar = page.locator(".modal", has_text="Cancelar la reserva")
    expect(confirmar).to_contain_text("Taller de radio comunitaria")
    expect(confirmar.locator("#ucCampo")).to_have_count(0)    # es suya: nadie a quien avisarle
    page.locator("#ucYes").click()

    expect(t.locator(".badge")).to_have_text("Cancelada")
    expect(t.get_by_role("button")).to_have_count(0)           # ni editar ni cancelar de nuevo
    page.wait_for_timeout(300)
    assert bandeja == []                                       # cancelar lo propio no avisa


def test_cada_subcomision_ve_solo_sus_pedidos(abrir, bibliotecaria: Page):
    m = _marta()
    luis = crear_usuario("subcomision", usuario="luis", subcomision="Cultura")
    _sembrar("Muestra de Cultura", quien=_de(m))
    _sembrar("Radio abierta de Prensa")                        # de otra subcomisión

    marta = abrir(m["usuario"], m["clave"])
    ir_a(marta, "solicitudes")
    expect(marta.locator("#solLista .sol-card")).to_have_count(1)
    expect(_tarjeta(marta, "Muestra de Cultura")).to_be_visible()
    expect(marta.locator("#solLista")).not_to_contain_text("Prensa")

    # Otro de Cultura ve lo de su subcomisión, aunque lo haya pedido Marta.
    otro = abrir(luis["usuario"], luis["clave"])
    ir_a(otro, "solicitudes")
    expect(otro.locator("#solLista .sol-card")).to_have_count(1)
    expect(_tarjeta(otro, "Muestra de Cultura")).to_be_visible()

    # La biblioteca ve todo y puede resolver.
    ir_a(bibliotecaria, "solicitudes")
    expect(bibliotecaria.locator("#solLista .sol-card")).to_have_count(2)
    expect(_tarjeta(bibliotecaria, "Radio abierta de Prensa")
           .get_by_role("button", name="Resolver")).to_be_visible()
    expect(bibliotecaria.locator("#solEspacios")).to_be_visible()

    # Ni por la API se puede mirar la de otra subcomisión.
    ajena = next(s for s in solicitudes.listar() if "Prensa" in s["titulo"])
    token = marta.evaluate("localStorage.getItem('token')")
    r = marta.request.get(f"/api/solicitudes/{ajena['id']}",
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status == 404


# ═══ La biblioteca resuelve ═════════════════════════════════════════════════════
def test_aprobar_se_ve_en_los_dos_lados_y_le_llega_el_mail(marta: Page, bibliotecaria: Page,
                                                           bandeja):
    _pedir(marta, "Taller de radio")

    ir_a(bibliotecaria, "solicitudes")
    modal = _abrir_resolver(bibliotecaria, "Taller de radio")
    expect(modal).to_contain_text("Pidió Marta Ríos · Cultura")
    expect(modal.locator("#rvInicio")).to_have_value(f"{DIA}T18:00")
    expect(modal.locator("#rvFin")).to_have_value(f"{DIA}T20:00")
    modal.locator("#rvOk").click()
    expect(bibliotecaria.locator("#solMsg")).to_contain_text("Aprobada. Ya aparece en el calendario.")
    expect(modal).to_be_hidden()
    t = _tarjeta(bibliotecaria, "Taller de radio")
    expect(t.locator(".badge")).to_have_text("Aprobada")
    expect(t.get_by_role("button", name="Resolver")).to_have_count(0)
    expect(t.get_by_role("button", name="Editar")).to_have_count(0)

    (m,) = _esperar_mails(bibliotecaria, bandeja)
    assert m["to"] == MAIL_MARTA
    assert m["subject"] == "Aprobado: Taller de radio"
    assert m["plain"].startswith("Hola Marta,\n")
    assert "Tu pedido de espacio para “Taller de radio” fue aprobado." in m["plain"]
    assert "· Espacio: Sala principal" in m["plain"]
    assert f"· Cuándo: {_cuando(FECHA, '18:00', '20:00')}" in m["plain"]
    assert "Ya figura en el calendario de la biblioteca." in m["plain"]
    assert "no es exactamente lo que habías pedido" not in m["plain"]
    assert servidor.URL in m["plain"]
    _sin_etiquetas(m)

    # Quien aprobó ve que se le avisó.
    _refrescar_hasta_ver(bibliotecaria, f"Se le avisó la aprobación por mail a {MAIL_MARTA}")

    # Marta, en su navegador, la ve aprobada; los datos de gestión no son para ella.
    _refrescar(marta)
    t = _tarjeta(marta, "Taller de radio")
    expect(t.locator(".badge")).to_have_text("Aprobada")
    expect(t.get_by_role("button", name="Editar")).to_have_count(0)
    expect(t.get_by_role("button", name="Cancelar")).to_be_visible()
    expect(t).not_to_contain_text("Se le avisó")


def test_rechazar_exige_motivo_y_el_motivo_llega(abrir, marta: Page, bandeja):
    _pedir(marta, "Peña folklórica")
    c = crear_usuario("comision", usuario="comi")            # la comisión también resuelve
    comision = abrir(c["usuario"], c["clave"])
    ir_a(comision, "solicitudes")

    modal = _abrir_resolver(comision, "Peña folklórica")
    modal.locator("#rvRech").click()
    expect(modal.locator("#rvMsg .msg.error")).to_have_text(
        "Para rechazar o pedir cambios hay que escribir el motivo.")
    assert solicitudes.listar()[0]["estado"] == "pendiente"

    motivo = "Ese sábado la sala está en obra"
    modal.locator("#rvMotivo").fill(motivo)
    modal.locator("#rvRech").click()
    expect(comision.locator("#solMsg")).to_contain_text("Rechazada.")
    t = _tarjeta(comision, "Peña folklórica")
    expect(t.locator(".badge")).to_have_text("Rechazada")
    expect(t.get_by_role("button")).to_have_count(0)          # rechazada: no hay nada más que hacer

    (m,) = _esperar_mails(comision, bandeja)
    assert m["to"] == MAIL_MARTA
    assert m["subject"] == "No se pudo aprobar: Peña folklórica"
    assert "Tu pedido de espacio para “Peña folklórica” no se pudo aprobar." in m["plain"]
    assert f"Motivo: {motivo}" in m["plain"]
    _sin_etiquetas(m)

    _refrescar(marta)
    t = _tarjeta(marta, "Peña folklórica")
    expect(t.locator(".badge")).to_have_text("Rechazada")
    expect(t.locator(".sol-motivo")).to_have_text(f"Rechazada: {motivo}")


def test_reprogramar_al_aprobar_muestra_lo_que_se_movio(marta: Page, bibliotecaria: Page,
                                                        bandeja):
    _sembrar("Asamblea de Prensa", espacio="Patio", desde="10:00", hasta="12:00", aprobada=True)
    _pedir(marta, "Taller de radio")
    otro_dia = FECHA + dt.timedelta(days=2)

    ir_a(bibliotecaria, "solicitudes")
    modal = _abrir_resolver(bibliotecaria, "Taller de radio")
    # Primero lo pone arriba de otra reserva: se lo marca.
    modal.locator("#rvEspacio").select_option(label="Patio")
    modal.locator("#rvInicio").fill(f"{DIA}T11:00")
    expect(modal.locator("#rvAviso")).to_contain_text("Se pisa con: Asamblea de Prensa")
    # Y lo acomoda en otro día y horario.
    modal.locator("#rvInicio").fill(f"{otro_dia.isoformat()}T19:00")
    modal.locator("#rvFin").fill(f"{otro_dia.isoformat()}T21:00")
    expect(modal.locator("#rvAviso")).to_be_empty()
    modal.locator("#rvMotivo").fill("La sala principal esa semana está ocupada")
    modal.locator("#rvOk").click()
    expect(bibliotecaria.locator("#solMsg")).to_contain_text("Aprobada.")

    (m,) = _esperar_mails(bibliotecaria, bandeja)
    assert m["subject"] == "Aprobado: Taller de radio"
    assert "· Espacio: Patio" in m["plain"]
    assert f"· Cuándo: {_cuando(otro_dia, '19:00', '21:00')}" in m["plain"]
    assert ("Ojo: no es exactamente lo que habías pedido. Habías pedido Sala principal, el "
            f"{_cuando(FECHA, '18:00', '20:00')}.") in m["plain"]
    assert "Comentario de la biblioteca: La sala principal esa semana está ocupada" in m["plain"]
    _sin_etiquetas(m)

    _refrescar(marta)
    t = _tarjeta(marta, "Taller de radio")
    expect(t.locator(".badge")).to_have_text("Aprobada")
    expect(t.locator(".sol-cuando")).to_contain_text("Patio")
    expect(t.locator(".sol-cuando")).to_contain_text(_hora("19:00"))
    expect(t.locator(".sol-cambio")).to_contain_text("Se había pedido:")
    expect(t.locator(".sol-cambio")).to_contain_text(_hora("18:00"))
    expect(t.locator(".sol-cambio")).to_contain_text("otro espacio")


def test_pedir_cambios_y_la_subcomision_corrige(marta: Page, bibliotecaria: Page, bandeja):
    _pedir(marta, "Taller de radio")
    ir_a(bibliotecaria, "solicitudes")
    modal = _abrir_resolver(bibliotecaria, "Taller de radio")
    modal.locator("#rvMotivo").fill("¿Pueden empezar a las 19? Antes hay apoyo escolar")
    modal.locator("#rvObs").click()
    expect(bibliotecaria.locator("#solMsg")).to_contain_text("Devuelta con observaciones.")

    (m,) = _esperar_mails(bibliotecaria, bandeja)
    assert m["subject"] == "Tu pedido necesita cambios: Taller de radio"
    assert "¿Pueden empezar a las 19? Antes hay apoyo escolar" in m["plain"]
    _sin_etiquetas(m)

    _refrescar(marta)
    t = _tarjeta(marta, "Taller de radio")
    expect(t.locator(".badge")).to_have_text("Con observaciones")
    expect(t.locator(".sol-motivo")).to_contain_text("Antes hay apoyo escolar")
    t.get_by_role("button", name="Editar").click()
    page = marta
    page.locator("#sfInicio").fill(f"{DIA}T19:00")
    page.locator("#sfFin").fill(f"{DIA}T21:00")
    page.locator("#sfGuardar").click()
    expect(page.locator("#solMsg")).to_contain_text("la solicitud quedó actualizada")
    expect(t.locator(".badge")).to_have_text("Pendiente")      # vuelve a la cola

    _refrescar(bibliotecaria)
    t = _tarjeta(bibliotecaria, "Taller de radio")
    expect(t.locator(".badge")).to_have_text("Pendiente")
    expect(t.locator(".sol-cuando")).to_contain_text(_hora("19:00"))
    expect(t.get_by_role("button", name="Resolver")).to_be_visible()


def test_la_biblioteca_cancela_una_reserva_aprobada_y_avisa(marta: Page, bibliotecaria: Page,
                                                            bandeja):
    _pedir(marta, "Taller de radio")
    ir_a(bibliotecaria, "solicitudes")
    _abrir_resolver(bibliotecaria, "Taller de radio").locator("#rvOk").click()
    _esperar_mails(bibliotecaria, bandeja, 1)

    t = _tarjeta(bibliotecaria, "Taller de radio")
    t.get_by_role("button", name="Cancelar").click()
    confirmar = bibliotecaria.locator(".modal", has_text="Cancelar la reserva")
    expect(confirmar).to_contain_text("sale del calendario")
    expect(confirmar).to_contain_text("A quien la pidió le llega un aviso por mail.")
    confirmar.locator("#ucCampo").fill("Se inundó la sala")
    confirmar.locator("#ucYes").click()
    expect(t.locator(".badge")).to_have_text("Cancelada")

    aviso = _esperar_mails(bibliotecaria, bandeja, 2)[1]
    assert aviso["to"] == MAIL_MARTA
    assert aviso["subject"] == "Reserva cancelada: Taller de radio"
    assert "La reserva de “Taller de radio” fue cancelada." in aviso["plain"]
    assert "· Espacio: Sala principal" in aviso["plain"]
    assert f"· Cuándo: {_cuando(FECHA, '18:00', '20:00')}" in aviso["plain"]
    assert "Motivo: Se inundó la sala" in aviso["plain"]
    _sin_etiquetas(aviso)

    _refrescar(marta)
    expect(_tarjeta(marta, "Taller de radio").locator(".badge")).to_have_text("Cancelada")


def test_sin_mail_cargado_se_avisa_a_quien_resuelve(abrir, bibliotecaria: Page, bandeja):
    u = crear_usuario("subcomision", usuario="sinmail", subcomision="Cultura")
    _sembrar("Huerta comunitaria", quien=_de(u))
    ir_a(bibliotecaria, "solicitudes")
    _abrir_resolver(bibliotecaria, "Huerta comunitaria").locator("#rvOk").click()
    expect(bibliotecaria.locator("#solMsg")).to_contain_text("Aprobada.")
    _refrescar_hasta_ver(bibliotecaria,
                         "No se le pudo avisar la aprobación: su usuario no tiene mail cargado.")
    assert bandeja == []


# ═══ Espacios ═══════════════════════════════════════════════════════════════════
def _abrir_espacios(page: Page):
    page.locator("#solEspacios").click()
    modal = page.locator(".modal", has_text="Espacios de la biblioteca")
    expect(modal).to_be_visible()
    return modal


def test_espacios_se_agregan_y_se_borran_y_eso_se_ve_al_pedir(abrir, bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "solicitudes")
    modal = _abrir_espacios(page)
    expect(modal.locator("#epLista tr")).to_have_count(2)
    expect(modal.locator("#epLista")).to_contain_text("Sala principal")
    expect(modal.locator("#epLista")).to_contain_text("Patio")

    modal.locator("#epAdd").click()                           # sin nombre
    expect(modal.locator("#epMsg .msg.error")).to_have_text("Falta el nombre del espacio.")
    modal.locator("#epNombre").fill("patio")                  # repetido (sin importar mayúsculas)
    modal.locator("#epAdd").click()
    expect(modal.locator("#epMsg .msg.error")).to_contain_text("Ya existe un espacio llamado")

    modal.locator("#epNombre").fill("Salón de talleres")
    modal.locator("#epCap").fill("30")
    modal.locator("#epAdd").click()
    fila = modal.locator("#epLista tr", has_text="Salón de talleres")
    expect(fila).to_contain_text("30 pers.")
    expect(modal.locator("#epNombre")).to_have_value("")
    expect(modal.locator("#epMsg .msg.error")).to_have_count(0)

    modal.locator("#epLista tr", has_text="Patio").get_by_role("button", name="Borrar").click()
    page.locator(".modal", has_text="Borrar espacio").locator("#ucYes").click()
    expect(modal.locator("#epLista tr", has_text="Patio")).to_have_count(0)
    expect(modal.locator("#epLista tr")).to_have_count(2)
    modal.locator("#epNo").click()
    expect(modal).to_be_hidden()

    page.locator("#solNueva").click()
    assert page.locator("#sfEspacio option").all_inner_texts() == [
        "Sala principal", "Salón de talleres (hasta 30)"]

    # Lo mismo le aparece a una subcomisión.
    m = _marta()
    marta = abrir(m["usuario"], m["clave"])
    ir_a(marta, "solicitudes")
    marta.locator("#solNueva").click()
    assert marta.locator("#sfEspacio option").all_inner_texts() == [
        "Sala principal", "Salón de talleres (hasta 30)"]


def test_una_subcomision_no_puede_tocar_los_espacios(marta: Page):
    token = marta.evaluate("localStorage.getItem('token')")
    cab = {"Authorization": f"Bearer {token}"}
    r = marta.request.post("/api/espacios", data={"nombre": "Mi salita"}, headers=cab)
    assert r.status == 403
    r = marta.request.delete(f"/api/espacios/{_espacio('Patio')}", headers=cab)
    assert r.status == 403
    assert [e["nombre"] for e in espacios.listar()] == ["Patio", "Sala principal"]


def test_espacio_renombrado_o_dado_de_baja(marta: Page):
    """La pantalla no tiene cómo renombrar ni desactivar (solo agregar y borrar): se hace
    por la API, y se mira que el formulario y las tarjetas lo reflejen."""
    patio, sala = _espacio("Patio"), _espacio("Sala principal")
    espacios.actualizar(sala, {"nombre": "Sala Osvaldo Bayer"})
    espacios.actualizar(patio, {"activo": False})

    page = marta
    _refrescar(page)
    page.locator("#solNueva").click()
    assert page.locator("#sfEspacio option").all_inner_texts() == ["Sala Osvaldo Bayer"]
    _llenar(page, "Taller de radio", espacio="Sala Osvaldo Bayer")
    page.locator("#sfGuardar").click()
    expect(_tarjeta(page, "Taller de radio").locator(".sol-cuando")).to_contain_text(
        "Sala Osvaldo Bayer")

    # Pedir un espacio dado de baja no pasa, aunque se fuerce desde la API.
    token = page.evaluate("localStorage.getItem('token')")
    r = page.request.post("/api/solicitudes", headers={"Authorization": f"Bearer {token}"},
                          data={"titulo": "Algo", "espacio_id": patio,
                                "inicio": f"{DIA}T10:00", "fin": f"{DIA}T11:00"})
    assert r.status == 400
    assert "dado de baja" in r.json()["detail"]


def test_editar_un_pedido_en_un_espacio_dado_de_baja_no_lo_muda_solo(marta: Page):
    page = marta
    _pedir(page, "Taller de radio", espacio="Patio")
    espacios.actualizar(_espacio("Patio"), {"activo": False})
    _refrescar(page)

    _tarjeta(page, "Taller de radio").get_by_role("button", name="Editar").click()
    page.locator("#sfTitulo").fill("Taller de radio (corregido)")
    page.locator("#sfGuardar").click()
    expect(page.locator("#solMsg .msg")).to_be_visible()
    # O avisa que ese espacio ya no está, o lo deja donde estaba; mudarlo callado, no.
    s = solicitudes.listar()[0]
    assert s["espacio_id"] == _espacio("Patio") or \
        page.locator("#solMsg .msg.error").count() == 1


# ═══ Calendario ═════════════════════════════════════════════════════════════════
def test_el_calendario_muestra_las_reservas_aprobadas(marta: Page):
    hoy = datos.HOY.isoformat()
    _sembrar("Asamblea de Prensa", dia=hoy, desde="18:00", hasta="20:00", aprobada=True)
    _sembrar("Todavía pendiente", dia=hoy, espacio="Patio")
    cancelada = _sembrar("Se canceló", dia=hoy, desde="10:00", hasta="11:00", aprobada=True)
    solicitudes.cancelar(cancelada["id"], por="biblio")

    page = marta
    ir_a(page, "agenda")
    cont = page.locator("#agendaContent")
    expect(cont.locator(".subtabs button")).to_have_text(["Semana", "Mes"])
    expect(cont.locator(".subtabs button.active")).to_have_text("Semana")
    expect(cont.locator(".cal-chip-btn")).to_have_text(["Reservas de espacio"])

    # Semana: la de hoy, con la reserva aprobada y nada más.
    hoy_sec = cont.locator(".day-sec", has=page.locator(".day-head.today"))
    expect(hoy_sec.locator(".ev2")).to_have_count(1)
    expect(hoy_sec.locator(".ev2")).to_contain_text("Asamblea de Prensa")
    expect(hoy_sec.locator(".ev2")).to_contain_text("Sala principal")
    expect(hoy_sec.locator(".ev2 .eh")).to_contain_text(_hora("18:00"))
    expect(cont).not_to_contain_text("Todavía pendiente")
    expect(cont).not_to_contain_text("Se canceló")

    # Mes: el chip en el día de hoy, y al tocarlo, el detalle.
    cont.locator(".subtabs button", has_text="Mes").click()
    expect(cont.locator(".subtabs button.active")).to_have_text("Mes")
    celda = cont.locator(f'.cal-cell[data-day="{hoy}"]')
    expect(celda.locator(".cal-chip")).to_have_text(["Asamblea de Prensa"])
    celda.click()
    expect(cont.locator("#agDayDetail .ev2")).to_contain_text("Asamblea de Prensa")

    # Ir y volver de mes deja todo como estaba.
    rango = cont.locator(".ag-range").inner_text()
    cont.locator("#agNext").click()
    expect(cont.locator(".ag-range")).not_to_have_text(rango)
    cont.locator("#agToday").click()
    expect(cont.locator(".ag-range")).to_have_text(rango)
    expect(cont.locator(f'.cal-cell[data-day="{hoy}"] .cal-chip')).to_have_count(1)

    # Apagando "Reservas de espacio", se va.
    cont.locator(".cal-chip-btn", has_text="Reservas de espacio").click()
    expect(cont.locator(f'.cal-cell[data-day="{hoy}"] .cal-chip')).to_have_count(0)
    cont.locator(".cal-chip-btn", has_text="Reservas de espacio").click()

    cont.locator(".subtabs button", has_text="Semana").click()
    expect(cont.locator(".day-sec", has=page.locator(".day-head.today")).locator(".ev2")) \
        .to_contain_text("Asamblea de Prensa")

    # Desde el calendario se puede pedir un espacio.
    cont.locator("#agPedir").click()
    expect(page.locator("#solicitudesView")).to_be_visible()
    expect(page.locator("#solForm")).to_contain_text("Solicitar un espacio")


def test_las_horas_se_ven_en_formato_de_24_horas(bibliotecaria: Page):
    hoy = datos.HOY.isoformat()
    _sembrar("Asamblea de Prensa", dia=hoy, desde="18:00", hasta="20:00", aprobada=True)
    page = bibliotecaria
    ir_a(page, "solicitudes")
    expect(_tarjeta(page, "Asamblea de Prensa").locator(".sol-cuando")).to_contain_text(
        "18:00 a 20:00", timeout=2_000)
    ir_a(page, "agenda")
    hoy_sec = page.locator("#agendaContent .day-sec", has=page.locator(".day-head.today"))
    expect(hoy_sec.locator(".ev2 .eh")).to_have_text("18:00 h", timeout=2_000)


def test_lo_que_aprueba_la_biblioteca_aparece_en_el_calendario_de_quien_pidio(
        marta: Page, bibliotecaria: Page):
    hoy = datos.HOY.isoformat()
    _pedir(marta, "Taller de radio", dia=hoy)
    ir_a(marta, "agenda")
    expect(marta.locator("#agendaContent")).not_to_contain_text("Taller de radio")

    ir_a(bibliotecaria, "solicitudes")
    _abrir_resolver(bibliotecaria, "Taller de radio").locator("#rvOk").click()
    expect(bibliotecaria.locator("#solMsg")).to_contain_text("Aprobada.")

    ir_a(marta, "solicitudes")
    ir_a(marta, "agenda")
    hoy_sec = marta.locator("#agendaContent .day-sec", has=marta.locator(".day-head.today"))
    expect(hoy_sec.locator(".ev2")).to_contain_text("Taller de radio")


# ═══ Historial de envíos ════════════════════════════════════════════════════════
def test_los_avisos_quedan_en_el_historial_de_envios(marta: Page, bibliotecaria: Page, bandeja):
    _pedir(marta, "Taller de radio")
    ir_a(bibliotecaria, "solicitudes")
    _abrir_resolver(bibliotecaria, "Taller de radio").locator("#rvOk").click()
    _esperar_mails(bibliotecaria, bandeja)
    page = bibliotecaria
    for _ in range(20):                       # el historial se graba justo después del envío
        if historial.listar(origen="avisos"):
            break
        page.wait_for_timeout(100)

    ir_a(page, "envios")
    page.locator('button[data-origen="avisos"]').click()
    fila = page.locator("#enviosLista .hist-item", has_text="Aprobado: Taller de radio")
    expect(fila).to_have_count(1)
    expect(fila.locator(".auto-badge")).to_have_text(["Aviso de solicitud", "Al resolver"])
    expect(fila).to_contain_text("por biblio")
    expect(fila).to_contain_text("Enviados: 1")
    fila.locator(".hist-toggle").click()
    expect(fila.locator(".hist-dest")).to_contain_text(MAIL_MARTA)

    # Filtrando por mails escritos a mano, no aparece.
    page.locator('button[data-origen="manual"]').click()
    expect(page.locator("#enviosLista")).not_to_contain_text("Aprobado: Taller de radio")
