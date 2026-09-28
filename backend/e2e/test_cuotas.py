"""Cuotas societarias y el cruce Koha–planilla (en la sección Socios).

La planilla de `datos.py`: Ana pagó hasta tres meses antes del actual, Bruno está al
día, Carla nunca pagó y Diego (de baja en Koha) pagó solo tres meses de 2025. Los
pagos que se cargan desde la app se guardan aparte y se superponen a la planilla.
"""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from app import cuotas

from . import datos
from .ayudas import ir_a

MES = datos.HOY.month
DEBE = {"100": MES - datos.PAGOS_ANA, "200": 0, "300": MES, "400": MES}
ULTIMO = {"100": (f"{datos.MESES[datos.PAGOS_ANA - 1]} 2026" if datos.PAGOS_ANA
                  else "Dic 2025"),
          "200": f"{datos.MESES[MES - 1]} 2026", "300": "sin pagos", "400": "Mar 2025"}


def _kpi(page: Page, contenedor: str, etiqueta: str):
    return (page.locator(f"{contenedor} .kpi")
            .filter(has=page.get_by_text(etiqueta, exact=True)).locator(".n"))


def _fila(page: Page, carnet: str):
    return page.locator("#cuBody tr", has=page.locator("td:nth-child(2)", has_text=re.compile(
        rf"^{carnet}$")))


def _mes(page: Page, carnet: str, i: int):
    """La marca del mes i (0 = enero) en la fila del socio."""
    return _fila(page, carnet).locator("td.mes").nth(i).locator(".mcell")


def _nombres(page: Page):
    return page.locator("#cuBody tr td:first-child")


def _estado_badge(debe: int) -> str:
    return f"debe {debe}" if debe else "al día"


def _abrir_cuotas(page: Page) -> None:
    ir_a(page, "cuotas")
    expect(page.locator("#pageTitle")).to_have_text("Cuotas societarias")
    expect(page.locator("#cuBody tr")).to_have_count(4)


# ── La tabla ────────────────────────────────────────────────────────────────
def test_cuotas_tabla_por_socio(bibliotecaria: Page):
    page = bibliotecaria
    _abrir_cuotas(page)
    expect(page.locator("#cuAnio")).to_have_value("2026")
    expect(_kpi(page, "#cuotasContent", "Socios en la planilla")).to_have_text("4")
    expect(_kpi(page, "#cuotasContent", "25% al día")).to_have_text("1")
    expect(_kpi(page, "#cuotasContent", "En deuda")).to_have_text("3")
    expect(page.locator("#cuInfo")).to_have_text("1–4 de 4")

    expect(_nombres(page)).to_have_text(
        ["Pérez, Ana", "Gómez, Bruno", "López, Carla", "Díaz, Diego"])
    for carnet, debe in DEBE.items():
        fila = _fila(page, carnet)
        expect(fila.locator(".badge")).to_have_text(_estado_badge(debe))
        expect(fila.locator("td").last).to_have_text(ULTIMO[carnet])
        expect(fila).to_have_class("deuda" if debe else "")

    # Mes por mes: Ana pagó hasta PAGOS_ANA, debe hasta el mes actual, lo que sigue no venció.
    for i in range(12):
        clase = "pago" if i < datos.PAGOS_ANA else "debe" if i < MES else "fut"
        expect(_mes(page, "100", i)).to_have_class(f"mcell {clase}")
    expect(_mes(page, "200", MES - 1)).to_have_text("P")
    expect(_mes(page, "300", 0)).to_have_attribute("title", "Ene: debe")


def test_cuotas_filtros_y_busqueda(bibliotecaria: Page):
    page = bibliotecaria
    _abrir_cuotas(page)

    page.locator('#cuFiltro button[data-f="deuda"]').click()
    expect(_nombres(page)).to_have_text(["Pérez, Ana", "López, Carla", "Díaz, Diego"])
    page.locator('#cuFiltro button[data-f="aldia"]').click()
    expect(_nombres(page)).to_have_text(["Gómez, Bruno"])
    expect(page.locator("#cuInfo")).to_have_text("1–1 de 1")
    page.locator('#cuFiltro button[data-f="todos"]').click()
    expect(page.locator("#cuBody tr")).to_have_count(4)

    page.locator("#cuBuscar").fill("lóp")
    expect(_nombres(page)).to_have_text(["López, Carla"])
    page.locator("#cuBuscar").fill("400")                 # también por matrícula
    expect(_nombres(page)).to_have_text(["Díaz, Diego"])
    page.locator("#cuBuscar").fill("Zamudio")
    expect(page.locator("#cuBody")).to_have_text("Sin resultados.")
    expect(page.locator("#cuInfo")).to_have_text("0")

    # La búsqueda se mantiene al cambiar el filtro.
    page.locator("#cuBuscar").fill("o")
    page.locator('#cuFiltro button[data-f="aldia"]').click()
    expect(page.locator("#cuBuscar")).to_have_value("o")
    expect(_nombres(page)).to_have_text(["Gómez, Bruno"])


def test_cuotas_orden_por_columna(bibliotecaria: Page):
    page = bibliotecaria
    _abrir_cuotas(page)

    page.locator('th[data-cu="socio"]').click()
    expect(page.locator('th[data-cu="socio"]')).to_have_text("Socio ↑")
    expect(_nombres(page)).to_have_text(
        ["Díaz, Diego", "Gómez, Bruno", "López, Carla", "Pérez, Ana"])
    page.locator('th[data-cu="socio"]').click()
    expect(page.locator('th[data-cu="socio"]')).to_have_text("Socio ↓")
    expect(_nombres(page)).to_have_text(
        ["Pérez, Ana", "López, Carla", "Gómez, Bruno", "Díaz, Diego"])

    page.locator('th[data-cu="matricula"]').click()
    expect(_nombres(page)).to_have_text(
        ["Pérez, Ana", "Gómez, Bruno", "López, Carla", "Díaz, Diego"])

    # Por estado: al día primero, después el que menos debe.
    page.locator('th[data-cu="estado"]').click()
    expect(_nombres(page).first).to_have_text("Gómez, Bruno")
    if DEBE["100"] < MES:
        expect(_nombres(page).nth(1)).to_have_text("Pérez, Ana")

    # Por último pago: el que nunca pagó, primero.
    page.locator('th[data-cu="ultimo"]').click()
    expect(_nombres(page).first).to_have_text("López, Carla")
    expect(_nombres(page).last).to_have_text("Gómez, Bruno")


@pytest.fixture
def con_alvarez(monkeypatch):
    """Una socia más cuyo apellido empieza con tilde (Álvarez), al día."""
    def filas():
        r = [""] * 53
        r[1], r[2], r[3], r[4] = "500", "Álvarez", "Eva", "Activo"
        for m in range(12):
            r[35 + m] = "P"
        return datos.filas_planilla() + [r]

    monkeypatch.setattr(cuotas, "_read_rows", filas)


def test_cuotas_orden_alfabetico_con_tildes(con_alvarez, bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "cuotas")
    expect(page.locator("#cuBody tr")).to_have_count(5)
    page.locator('th[data-cu="socio"]').click()
    expect(_nombres(page)).to_have_text(
        ["Álvarez, Eva", "Díaz, Diego", "Gómez, Bruno", "López, Carla", "Pérez, Ana"],
        timeout=2_000)


def test_cuotas_otro_anio(bibliotecaria: Page):
    page = bibliotecaria
    _abrir_cuotas(page)
    page.locator("#cuAnio").select_option("2025")
    expect(page.locator("#cuAnio")).to_have_value("2025")
    # 2025 ya cerró: Ana y Bruno pagaron los doce meses.
    expect(_kpi(page, "#cuotasContent", "50% al día")).to_have_text("2")
    expect(_fila(page, "100").locator(".badge")).to_have_text("al día")
    expect(_fila(page, "300").locator(".badge")).to_have_text("debe 12")
    expect(_fila(page, "400").locator(".badge")).to_have_text("debe 9")
    expect(_fila(page, "400").locator("td.mes .mcell.pago")).to_have_count(3)
    expect(_fila(page, "100").locator("td.mes .mcell.pago")).to_have_count(12)


# ── Cargar un pago desde la app ─────────────────────────────────────────────
def test_cargar_un_pago_y_que_quede(bibliotecaria: Page):
    page = bibliotecaria
    _abrir_cuotas(page)
    i = datos.PAGOS_ANA                                   # primer mes que Ana debe
    celda = _fila(page, "100").locator("td.mes").nth(i)
    expect(celda.locator(".mcell")).to_have_class("mcell debe")

    celda.click()
    expect(_mes(page, "100", i)).to_have_class("mcell pago ov")
    expect(_mes(page, "100", i)).to_have_attribute(
        "title", f"{datos.MESES[i]}: pagó (cargado en la app por biblio)")
    expect(_fila(page, "100").locator(".badge")).to_have_text(_estado_badge(DEBE["100"] - 1))
    expect(_fila(page, "100").locator("td").last).to_have_text(f"{datos.MESES[i]} 2026")

    # Sigue ahí después de recargar la página.
    page.reload()
    expect(page.locator("#appView")).to_be_visible()
    _abrir_cuotas(page)
    expect(_mes(page, "100", i)).to_have_class("mcell pago ov")
    expect(_fila(page, "100").locator(".badge")).to_have_text(_estado_badge(DEBE["100"] - 1))

    # Quitarlo pide confirmación; "Cancelar" no toca nada.
    _fila(page, "100").locator("td.mes").nth(i).click()
    modal = page.locator(".modal", has_text="Quitar pago")
    expect(modal).to_contain_text(f"{datos.MESES[i]} 2026")
    expect(modal).to_contain_text("Pérez, Ana")
    page.locator("#ucNo").click()
    expect(modal).to_have_count(0)
    expect(_mes(page, "100", i)).to_have_class("mcell pago ov")

    _fila(page, "100").locator("td.mes").nth(i).click()
    page.locator("#ucYes").click()
    expect(_mes(page, "100", i)).to_have_class("mcell debe")
    expect(_fila(page, "100").locator(".badge")).to_have_text(_estado_badge(DEBE["100"]))


def test_pago_de_la_planilla_no_se_toca(bibliotecaria: Page):
    page = bibliotecaria
    _abrir_cuotas(page)
    enero_bruno = _fila(page, "200").locator("td.mes").first
    expect(enero_bruno).not_to_have_class(re.compile(r"\bclk\b"))
    enero_bruno.click()
    expect(page.locator(".modal")).to_have_count(0)
    expect(enero_bruno.locator(".mcell")).to_have_class("mcell pago")


def test_ponerse_al_dia_cambia_los_totales_y_el_cruce(bibliotecaria: Page):
    page = bibliotecaria
    _abrir_cuotas(page)
    for i in range(datos.PAGOS_ANA, MES):
        _fila(page, "100").locator("td.mes").nth(i).click()
        expect(_mes(page, "100", i)).to_have_class("mcell pago ov")
    expect(_fila(page, "100").locator(".badge")).to_have_text("al día")
    expect(_kpi(page, "#cuotasContent", "50% al día")).to_have_text("2")
    expect(_kpi(page, "#cuotasContent", "En deuda")).to_have_text("2")

    # El filtro activo se respeta al volver a dibujar.
    page.locator('#cuFiltro button[data-f="deuda"]').click()
    expect(_nombres(page)).to_have_text(["López, Carla", "Díaz, Diego"])

    # En el cruce de Socios, Ana pasa de "retira y debe" a "al día y retira".
    ir_a(page, "members")
    expect(page.locator('.matrix .cell[data-k="aldia_ret"] .n')).to_have_text("2")
    expect(page.locator('.matrix .cell[data-k="deuda_ret"] .n')).to_have_text("1")


# ── Cruce Koha–planilla (sección Socios) ────────────────────────────────────
def test_cruce_koha_planilla(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "members")
    cruce = page.locator("#cruceSection")
    expect(cruce.locator("h3")).to_have_text("Cruce con la planilla de cuotas (2026)")
    # Diego está de baja en Koha: no cuenta.
    expect(_kpi(page, "#cruceSection", "Socios activos en Koha")).to_have_text("3")
    expect(_kpi(page, "#cruceSection", "En la planilla de cuotas")).to_have_text("4")
    expect(_kpi(page, "#cruceSection", "Coinciden (en ambos)")).to_have_text("3")
    expect(_kpi(page, "#cruceSection", "En Koha, fuera de la planilla")).to_have_text("0")

    # Los tres retiran libros; con deuda desde 1 mes, Ana y Carla deben.
    celdas = {k: cruce.locator(f'.matrix .cell[data-k="{k}"] .n')
              for k in ("aldia_ret", "aldia_no", "deuda_ret", "deuda_no")}
    for k, n in {"aldia_ret": 1, "aldia_no": 0, "deuda_ret": 2, "deuda_no": 0}.items():
        expect(celdas[k]).to_have_text(str(n))

    cruce.locator('.matrix .cell[data-k="deuda_ret"]').click()
    detalle = page.locator("#cruceDetail")
    expect(detalle).to_contain_text("Retiran y deben cuota (2)")
    ana = detalle.locator(".recip", has_text="(100)")
    expect(ana).to_contain_text("Pérez, Ana")
    expect(ana).to_contain_text(f"debe {DEBE['100']}")
    carla = detalle.locator(".recip", has_text="(300)")
    expect(carla).to_contain_text(f"debe {DEBE['300']}")
    expect(carla.locator(".noemail")).to_have_text("sin email")

    cruce.locator('.matrix .cell[data-k="aldia_no"]').click()
    expect(detalle).to_contain_text("Nadie en este grupo.")

    # Subiendo el umbral, quien debe menos meses pasa a "al día".
    umbral = max(DEBE["100"], DEBE["300"])
    if umbral in (2, 3, 6):
        cruce.locator("#cruceUmbral").select_option(str(umbral))
        con_deuda = sum(DEBE[c] >= umbral for c in ("100", "300"))
        expect(celdas["deuda_ret"]).to_have_text(str(con_deuda))
        expect(celdas["aldia_ret"]).to_have_text(str(3 - con_deuda))

    expect(cruce.get_by_text("Retiran pero no están en la planilla (0)")).to_be_visible()


def test_cruce_agrega_deudores_a_mails(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "members")
    page.locator("#cruceSection .pill", has_text="Ver todos con deuda").click()
    lista = page.locator("#cruceDetail2")
    expect(lista).to_contain_text("Todos con deuda de cuota (2)")
    lista.locator(".pill", has_text="+ Agregar a Mails").click()

    expect(page.locator("#pageTitle")).to_have_text("Envío de mails")
    # Carla no tiene mail: solo entra Ana.
    expect(page.locator("#panelMsg")).to_contain_text("Se agregaron 1 destinatario(s) con email")
    expect(page.locator("#recipList")).to_contain_text("(100)")
    expect(page.locator("#recipList")).not_to_contain_text("(300)")
    expect(page.locator("#recipCount")).to_have_text("1")
