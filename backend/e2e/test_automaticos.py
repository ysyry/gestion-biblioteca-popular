"""Envíos automáticos: los reportes a socios y el resumen interno de las bibliotecarias.

Acá estuvo el {{meses_debe}} que salía 0: la planilla de cuotas solo se leía con la
casilla "Deuda de cuota" tildada. Estas pruebas usan las etiquetas de cuota SIN tildarla.
"""
from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from app import etiquetas

from . import datos
from .ayudas import ir_a


def _etiquetas(juego: str) -> list[str]:
    return [x["etiqueta"] for g in etiquetas.catalogo()[juego] for x in g["items"]]


def _cuerpo_con_todas(juego: str) -> str:
    return "\n".join(f"{e}=[{{{{{e}}}}}]" for e in _etiquetas(juego))


def _leer(texto: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in re.finditer(r"^(\w+)=\[(.*?)\]$", texto, re.S | re.M)}


def _vence(delta: int) -> str:
    return datos.HOY.fromordinal(datos.HOY.toordinal() + delta).strftime("%d/%m/%Y")


def _abrir(page: Page, nombre: str) -> None:
    ir_a(page, "auto")
    page.locator("#autoTabs button", has_text=nombre).click()
    expect(page.locator("#a_nombre")).to_have_value(nombre)


def _escribir(page: Page, asunto: str, cuerpo: str, pie: str = "") -> None:
    page.locator("#a_subject").fill(asunto)
    page.locator("#a_body").fill(cuerpo)
    page.locator("#a_footer").fill(pie)


def _enviar_ahora(page: Page) -> None:
    page.locator("#a_run").click()
    page.locator("#ucYes").click()
    expect(page.locator("#a_result .msg")).to_be_visible()
    expect(page.locator("#a_result .msg.error")).to_have_count(0)


def test_a_socios_cada_etiqueta_llega_con_su_dato(bibliotecaria: Page, bandeja):
    page = bibliotecaria
    _abrir(page, "Recordatorio a socios")
    expect(page.locator("#a_incc")).not_to_be_checked()      # la casilla de cuota, sin tildar
    _escribir(page, "{{nombre}} debe {{ meses_debe }}", _cuerpo_con_todas("socio"))
    _enviar_ahora(page)

    por_socio = {m["to"]: m for m in bandeja}
    assert set(por_socio) == {"ana@example.org", "bruno@example.org"}   # Carla no tiene mail
    debe, impagos = datos.deuda_ana()

    ana = por_socio["ana@example.org"]
    assert ana["subject"] == f"Ana debe {debe}"
    esperado = {
        "nombre": "Ana", "apellido": "Pérez", "carnet": "100", "email": "ana@example.org",
        "vencidos": "• Rayuela", "activos": "ninguno",
        "prestamos": f"• Rayuela (vence {_vence(-10)})",
        "cantidad_vencidos": "1", "cantidad_activos": "0", "cantidad_prestamos": "1",
        "meses_debe": debe, "meses_impagos": impagos, "ultimo_mes_pago": datos.ULTIMO_PAGO_ANA,
    }
    assert set(esperado) == set(_etiquetas("socio"))
    assert _leer(ana["plain"]) == esperado

    bruno = _leer(por_socio["bruno@example.org"]["plain"])
    assert bruno["activos"] == f"• Ficciones (vence {_vence(3)})"
    assert bruno["meses_debe"] == "0" and bruno["meses_impagos"] == "—"
    for m in bandeja:
        assert "{{" not in m["plain"] and "{{" not in m["html"]


def test_a_socios_la_vista_previa_muestra_lo_que_llega(bibliotecaria: Page, bandeja):
    page = bibliotecaria
    _abrir(page, "Recordatorio a socios")
    _escribir(page, "Hola {{nombre}}", "Debés {{meses_debe}} meses ({{meses_impagos}}).")
    page.locator("#a_preview").click()
    resultado = page.locator("#a_result")
    expect(resultado).to_contain_text("Llegaría a 3 socio(s)")
    expect(resultado).to_contain_text("sin email: 1")
    muestra = resultado.locator(".mailmock").inner_text()
    assert re.search(r"Debés \d+ meses", muestra)
    assert "{{" not in muestra
    assert bandeja == []                                       # ver no manda nada


def test_a_socios_excluir_a_uno(bibliotecaria: Page, bandeja):
    page = bibliotecaria
    _abrir(page, "Recordatorio a socios")
    _escribir(page, "Hola {{nombre}}", "Hola {{nombre}}")
    page.locator("#a_dest").click()
    fila = page.locator("#a_result .recip", has_text="(200)")
    fila.get_by_role("button", name="Excluir").click()
    expect(fila.get_by_role("button", name="Incluir")).to_be_visible()
    page.locator("#a_save").click()
    expect(page.locator("#a_status")).to_contain_text("Guardado")

    _enviar_ahora(page)
    assert [m["to"] for m in bandeja] == ["ana@example.org"]


def test_a_socios_prueba_manda_una_muestra(bibliotecaria: Page, bandeja):
    page = bibliotecaria
    _abrir(page, "Recordatorio a socios")
    _escribir(page, "Hola {{nombre}}", "Hola {{nombre}}")
    page.locator("#a_testto").fill("yo@example.org")
    page.locator("#a_test").click()
    expect(page.locator("#a_result")).to_contain_text("Prueba (1 muestra) enviada a yo@example.org")
    assert len(bandeja) == 1 and bandeja[0]["to"] == "yo@example.org"
    assert bandeja[0]["plain"].startswith("Hola ")
    assert "{{" not in bandeja[0]["plain"]


def test_resumen_interno_cada_etiqueta_llega_con_su_dato(bibliotecaria: Page, bandeja):
    page = bibliotecaria
    _abrir(page, "Resumen interno")
    expect(page.locator("#a_incc")).not_to_be_checked()
    page.locator("#a_to").fill("equipo@example.org")
    _escribir(page, "Resumen del {{fecha}}", _cuerpo_con_todas("interno"))
    _enviar_ahora(page)
    expect(page.locator("#a_result")).to_contain_text("Enviado a equipo@example.org")

    assert len(bandeja) == 1
    m = bandeja[0]
    assert m["to"] == "equipo@example.org"
    salio = _leer(m["plain"])
    assert set(salio) == set(_etiquetas("interno"))
    assert salio["fecha"] == datos.HOY.strftime("%d/%m/%Y")
    assert m["subject"] == f"Resumen del {salio['fecha']}"
    assert salio["total_vencidos"] == "2"                      # Ana y Carla
    assert "Rayuela" in salio["lista_vencidos"] and "Operación masacre" in salio["lista_vencidos"]
    assert salio["total_socios_deben"] == "2"
    assert salio["total_activos"] == "1" and "Ficciones" in salio["lista_activos"]
    # Sin la casilla de cuota tildada, igual tiene que calcularla (antes: "ninguno" y 0).
    assert salio["total_deudores_cuota"] == "2"                # Ana y Carla; Diego es baja
    assert "Pérez" in salio["lista_cuotas"] and "López" in salio["lista_cuotas"]
    assert "Díaz" not in salio["lista_cuotas"]
    assert f"último pago: {datos.ULTIMO_PAGO_ANA}" in salio["lista_cuotas"]
    assert "{{" not in m["plain"]


def test_crear_y_borrar_un_reporte(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "auto")
    page.locator("#autoAdd").click()
    page.locator("#nrNombre").fill("Cuotas mensual")
    page.locator('input[name="nrTipo"][value="socios"]').check()
    page.locator("#nrOk").click()
    expect(page.locator("#a_nombre")).to_have_value("Cuotas mensual")
    expect(page.locator("#autoTabs")).to_contain_text("Cuotas mensual")

    # Lo que se cambia queda guardado al volver a entrar.
    page.locator("#a_subject").fill("Asunto guardado")
    page.locator("#a_subject").blur()
    expect(page.locator("#a_status")).to_contain_text("Guardado")
    page.reload()
    _abrir(page, "Cuotas mensual")
    expect(page.locator("#a_subject")).to_have_value("Asunto guardado")

    page.locator("#a_del").click()
    page.locator("#ucYes").click()
    expect(page.locator("#autoTabs")).not_to_contain_text("Cuotas mensual")


def test_lo_enviado_queda_en_el_historial_del_reporte(bibliotecaria: Page, bandeja):
    page = bibliotecaria
    _abrir(page, "Recordatorio a socios")
    _escribir(page, "Hola {{nombre}}", "Hola {{nombre}}")
    _enviar_ahora(page)
    hist = page.locator("#a_history")
    expect(hist.locator(".hist-item")).to_have_count(1)
    expect(hist).to_contain_text("Enviados: 2")
    hist.locator(".hist-toggle").click()
    expect(hist.locator(".hist-d")).to_have_count(3)
    expect(hist).to_contain_text("sin email")
