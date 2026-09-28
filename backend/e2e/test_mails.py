"""Envío de mails a socios: lo que se escribe en la pantalla es lo que le llega a cada uno.

La prueba de fondo arma un mensaje con TODAS las etiquetas que ofrece la pantalla y
revisa, en el mail ya armado que la app le mandaría al socio, que cada una salió con
su dato. Así se ataja lo que pasó con {{meses_debe}}, que salía 0.
"""
from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from app import etiquetas

from . import datos
from .ayudas import ir_a


def _todas_las_etiquetas() -> list[str]:
    return [x["etiqueta"] for g in etiquetas.catalogo()["socio"] for x in g["items"]]


def _cuerpo_con_todas() -> str:
    """Una línea por etiqueta: `nombre=[{{nombre}}]`, fácil de leer de vuelta."""
    return "\n".join(f"{e}=[{{{{{e}}}}}]" for e in _todas_las_etiquetas())


def _leer(texto: str) -> dict[str, str]:
    """De vuelta: {etiqueta: lo que salió}. Soporta valores de varias líneas."""
    return {m.group(1): m.group(2) for m in re.finditer(r"^(\w+)=\[(.*?)\]$", texto, re.S | re.M)}


def _vence(delta: int) -> str:
    d = datos.HOY.fromordinal(datos.HOY.toordinal() + delta)
    return d.strftime("%d/%m/%Y")


def _buscar_y_agregar(page: Page, termino: str, carnet: str) -> None:
    page.locator("#mailSearch").fill(termino)
    page.locator("#mailSearchBtn").click()
    fila = page.locator("#mailSearchResults .recip", has_text=f"({carnet})")
    fila.get_by_role("button", name="+ Agregar").click()
    expect(page.locator("#recipList")).to_contain_text(f"({carnet})")


def _enviar_de_verdad(page: Page) -> None:
    page.locator('input[name="mailMode"][value="real"]').check()
    page.locator("#mailSendBtn").click()
    page.locator("#ucYes").click()
    expect(page.locator("#mailResult")).to_contain_text("enviados")


def _escribir(page: Page, asunto: str, cuerpo: str) -> None:
    page.locator("#mailSubject").fill(asunto)
    page.locator("#mailBody").fill(cuerpo)


def test_cada_etiqueta_llega_con_su_dato(bibliotecaria: Page, bandeja):
    page = bibliotecaria
    ir_a(page, "mails")
    _buscar_y_agregar(page, "Pérez", "100")
    # En el asunto, con espacios dentro de las llaves: también tiene que andar.
    _escribir(page, "Hola {{ nombre }}, debés {{ meses_debe }} meses", _cuerpo_con_todas())
    # Espera a que la ficha complete los préstamos del socio.
    expect(page.locator("#mailPreview")).to_contain_text("Rayuela")
    _enviar_de_verdad(page)

    assert len(bandeja) == 1
    m = bandeja[0]
    assert m["to"] == "ana@example.org"
    debe, impagos = datos.deuda_ana()
    assert m["subject"] == f"Hola Ana, debés {debe} meses"

    salio = _leer(m["plain"])
    esperado = {
        "nombre": "Ana", "apellido": "Pérez", "carnet": "100", "email": "ana@example.org",
        "vencidos": "• Rayuela",
        "activos": "ninguno",
        "prestamos": f"• Rayuela (vence {_vence(-10)})",
        "cantidad_vencidos": "1", "cantidad_activos": "0", "cantidad_prestamos": "1",
        "meses_debe": debe, "meses_impagos": impagos,
        "ultimo_mes_pago": datos.ULTIMO_PAGO_ANA,
    }
    # Si se agrega una etiqueta al catálogo, este test obliga a decir qué tiene que salir.
    assert set(esperado) == set(_todas_las_etiquetas())
    assert salio == esperado
    assert "{{" not in m["plain"] and "{{" not in m["html"]


def test_la_vista_previa_coincide_con_lo_que_se_manda(bibliotecaria: Page, bandeja):
    page = bibliotecaria
    ir_a(page, "mails")
    _buscar_y_agregar(page, "Pérez", "100")
    _escribir(page, "Cuota de {{nombre}}", _cuerpo_con_todas())
    expect(page.locator("#mailPreview")).to_contain_text("Rayuela")
    previa = page.locator("#mailPreview").inner_text()

    _enviar_de_verdad(page)
    enviado = bandeja[0]
    assert previa.startswith("Asunto: Cuota de Ana")
    assert _leer(previa) == _leer(enviado["plain"])


def test_atajo_vencidos_y_socio_sin_mail(bibliotecaria: Page, bandeja):
    page = bibliotecaria
    ir_a(page, "mails")
    page.locator("#addOverdue").click()
    lista = page.locator("#recipList")
    expect(lista).to_contain_text("(100)")
    expect(lista).to_contain_text("(300)")
    expect(lista).not_to_contain_text("(200)")       # Bruno no tiene vencidos
    expect(page.locator("#recipCount")).to_have_text("2")
    expect(lista).to_contain_text("1 con email · 1 sin email")

    _escribir(page, "Devolución", "Hola {{nombre}}, tenés para devolver:\n{{vencidos}}")
    _enviar_de_verdad(page)

    assert [m["to"] for m in bandeja] == ["ana@example.org"]
    assert "Hola Ana" in bandeja[0]["plain"] and "Rayuela" in bandeja[0]["plain"]
    expect(page.locator("#mailResult")).to_contain_text("socio sin email")


def test_atajo_prestamos_activos(bibliotecaria: Page, bandeja):
    page = bibliotecaria
    ir_a(page, "mails")
    page.locator("#addUpcoming").click()
    expect(page.locator("#recipList")).to_contain_text("(200)")
    expect(page.locator("#recipCount")).to_have_text("1")
    _escribir(page, "Recordatorio", "{{nombre}}: {{activos}}")
    _enviar_de_verdad(page)
    assert bandeja[0]["plain"] == f"Bruno: • Ficciones (vence {_vence(3)})"


def test_mensaje_personalizado_solo_para_un_socio(bibliotecaria: Page, bandeja):
    page = bibliotecaria
    ir_a(page, "mails")
    _buscar_y_agregar(page, "Pérez", "100")
    _buscar_y_agregar(page, "Gómez", "200")
    _escribir(page, "Novedades para {{nombre}}", "Hola {{nombre}}, debés {{meses_debe}} meses.")

    page.locator(".recip", has_text="(100)").get_by_role("button", name="Editar").click()
    page.locator("#mBody").fill("Ana, te esperamos el sábado. Debés {{meses_debe}} meses.")
    page.locator("#mSave").click()
    expect(page.locator(".recip", has_text="(100)")).to_contain_text("mensaje personalizado")

    _enviar_de_verdad(page)
    por_socio = {m["to"]: m for m in bandeja}
    debe, _ = datos.deuda_ana()
    assert por_socio["ana@example.org"]["plain"] == f"Ana, te esperamos el sábado. Debés {debe} meses."
    assert por_socio["ana@example.org"]["subject"] == "Novedades para Ana"
    assert por_socio["bruno@example.org"]["plain"] == "Hola Bruno, debés 0 meses."


def test_prueba_manda_una_sola_muestra_a_la_direccion_indicada(bibliotecaria: Page, bandeja):
    page = bibliotecaria
    ir_a(page, "mails")
    page.locator("#addOverdue").click()
    expect(page.locator("#recipCount")).to_have_text("2")
    _escribir(page, "Prueba", "Hola {{nombre}} ({{carnet}})")
    page.locator("#mailTestTo").fill("yo@example.org")
    page.locator("#mailSendBtn").click()
    expect(page.locator("#mailResult")).to_contain_text("PRUEBA")

    assert len(bandeja) == 1
    assert bandeja[0]["to"] == "yo@example.org"
    assert bandeja[0]["plain"] == "Hola Ana (100)"


def test_tocar_una_etiqueta_la_inserta_en_el_cuerpo(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "mails")
    page.locator("#mailBody").fill("Hola ")
    page.locator('#mailVars code[data-v="nombre"]').click()
    expect(page.locator("#mailBody")).to_have_value("Hola {{nombre}}")
    # Están todas las del catálogo, y la ayuda las explica.
    for e in _todas_las_etiquetas():
        expect(page.locator(f'#mailVars code[data-v="{e}"]')).to_have_count(1)
    page.locator('#mailVars .help-btn').click()
    expect(page.locator(".help-list")).to_contain_text("{{ultimo_mes_pago}}")
    page.locator("#ayudaOk").click()


def test_sin_asunto_no_se_manda(bibliotecaria: Page, bandeja):
    page = bibliotecaria
    ir_a(page, "mails")
    _buscar_y_agregar(page, "Pérez", "100")
    _escribir(page, "", "Hola")
    page.locator("#mailTestTo").fill("yo@example.org")
    page.locator("#mailSendBtn").click()
    expect(page.locator("#mailResult")).to_contain_text("Completá asunto y cuerpo")
    assert bandeja == []


def test_lo_enviado_queda_en_el_historial(bibliotecaria: Page, bandeja):
    page = bibliotecaria
    ir_a(page, "mails")
    _buscar_y_agregar(page, "Pérez", "100")
    _escribir(page, "Aviso del historial", "Hola {{nombre}}")
    _enviar_de_verdad(page)

    ir_a(page, "envios")
    expect(page.locator("#enviosView")).to_contain_text("Aviso del historial")
