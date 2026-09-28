"""Préstamos, socios, notas e Inicio: lo que la bibliotecaria mira todos los días.

Los datos salen de `datos.py`: Ana (100) con "Rayuela" vencido hace 10 días, Bruno
(200) con "Ficciones" en préstamo, Carla (300) con "Operación masacre" vencido hace 4.
Para las notas se suman acá unas cuantas más (una de cuotas, un reclamo y una novedad
vieja de Bruno), y la consulta a Koha respeta el período y el texto buscado, como la
de verdad: si no, buscar "kindle" traería todas y la prueba no probaría nada.
"""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from . import datos
from .ayudas import ir_a

NOTA_CUOTAS_BRUNO = "Cuotas hasta JUNIO/2026"
NOTA_RECLAMO_CARLA = "Reclamé libros x wp"
NOTA_VIEJA_BRUNO = "Perdió el kindle, dice que lo repone"      # hace 100 días


@pytest.fixture(autouse=True)
def _mas_notas(monkeypatch):
    """Más notas en Koha, y una consulta de notas que filtra por fecha y por texto."""
    notas = datos.NOTAS + [
        datos._nota(2, "200", NOTA_CUOTAS_BRUNO, -3),
        datos._nota(3, "300", NOTA_RECLAMO_CARLA, -2),
        datos._nota(4, "200", NOTA_VIEJA_BRUNO, -100),
    ]
    monkeypatch.setattr(datos, "NOTAS", notas)
    sql_original = datos.sql

    def sql(consulta: str) -> list[dict]:
        c = " ".join(consulta.split()).lower()
        if "from messages" not in c or "b.cardnumber = '" in c:
            return sql_original(consulta)
        filas = list(notas)
        if m := re.search(r"m\.message_date >= '(\d{4}-\d{2}-\d{2})", c):
            filas = [n for n in filas if n["fecha"][:10] >= m.group(1)]
        if m := re.search(r"m\.message like '%(.*?)%'", c):
            filas = [n for n in filas
                     if m.group(1) in bytes.fromhex(n["texto_hex"]).decode().lower()]
        return sorted(filas, key=lambda n: n["fecha"], reverse=True)

    monkeypatch.setattr(datos, "sql", sql)


def _kpi(page: Page, contenedor: str, etiqueta: str):
    """El número grande de la tarjeta de KPI que lleva esa etiqueta (exacta)."""
    return (page.locator(f"{contenedor} .kpi")
            .filter(has=page.get_by_text(etiqueta, exact=True)).locator(".n"))


def _filas(page: Page):
    return page.locator("#tableWrap tbody tr")


def _columna(page: Page, n: int):
    return page.locator(f"#tableWrap tbody tr td:nth-child({n})")


def _vence(delta: int) -> str:
    return datos.HOY.fromordinal(datos.HOY.toordinal() + delta).isoformat()


# ── Préstamos ───────────────────────────────────────────────────────────────
def test_prestamos_todos_con_su_estado(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "loans")
    expect(page.locator("#pageTitle")).to_have_text("Préstamos")
    expect(page.locator('#loansSubtabs button[data-sub="todos"]')).to_have_class(
        re.compile(r"\bactive\b"))
    expect(_filas(page)).to_have_count(3)
    expect(page.locator("#rowCount")).to_have_text("3 resultado(s).")
    # De entrada, los más atrasados primero.
    expect(_columna(page, 4)).to_have_text(["Rayuela", "Operación masacre", "Ficciones"])

    ana = _filas(page).filter(has_text="Rayuela")
    expect(ana).to_contain_text("Pérez")
    expect(ana).to_contain_text("Ana")
    expect(ana.locator("td").nth(5)).to_have_text(_vence(-10))
    expect(ana.locator(".badge")).to_have_text("Vencido")
    bruno = _filas(page).filter(has_text="Ficciones")
    expect(bruno.locator("td").nth(5)).to_have_text(_vence(3))
    expect(bruno.locator(".badge")).to_have_text("En préstamo")
    expect(_filas(page).filter(has_text="Operación masacre").locator(".badge")).to_have_text(
        "Vencido")


def test_prestamos_subpestanias(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "loans")
    expect(_filas(page)).to_have_count(3)

    page.locator('#loansSubtabs button[data-sub="vencidos"]').click()
    expect(page.locator("#tableWrap thead")).to_contain_text("Teléfono")
    expect(page.locator("#tableWrap thead")).to_contain_text("Atraso")
    expect(_columna(page, 5)).to_have_text(["Rayuela", "Operación masacre"])
    expect(_columna(page, 7)).to_have_text(["10 días", "4 días"])
    expect(_columna(page, 4)).to_have_text(["11-1111-1111", "11-3333-3333"])
    expect(page.locator("#rowCount")).to_have_text("2 resultado(s).")

    page.locator('#loansSubtabs button[data-sub="aldia"]').click()
    expect(page.locator('#loansSubtabs button[data-sub="aldia"]')).to_have_class(
        re.compile(r"\bactive\b"))
    expect(_filas(page)).to_have_count(1)
    expect(_filas(page)).to_contain_text("Gómez")
    expect(_filas(page)).to_contain_text("Ficciones")
    expect(page.locator("#tableWrap thead")).not_to_contain_text("Estado")

    page.locator('#loansSubtabs button[data-sub="todos"]').click()
    expect(_filas(page)).to_have_count(3)


def test_prestamos_filtro_de_la_lista(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "loans")
    expect(_filas(page)).to_have_count(3)
    filtro = page.locator("#listFilterInput")

    filtro.fill("rayu")
    expect(_filas(page)).to_have_count(1)
    expect(_filas(page)).to_contain_text("Pérez")
    expect(page.locator("#rowCount")).to_have_text("1 de 3 resultado(s).")

    filtro.fill("GÓMEZ")                    # sin importar mayúsculas
    expect(_filas(page)).to_have_count(1)
    expect(_filas(page)).to_contain_text("Ficciones")

    filtro.fill("nadie se llama así")
    expect(page.locator("#tableWrap")).to_have_text("Sin resultados.")
    expect(page.locator("#rowCount")).to_have_text("0 de 3 resultado(s).")

    filtro.fill("")
    expect(_filas(page)).to_have_count(3)

    # Al cambiar de subpestaña el filtro se vacía.
    filtro.fill("rayu")
    expect(_filas(page)).to_have_count(1)
    page.locator('#loansSubtabs button[data-sub="vencidos"]').click()
    expect(filtro).to_have_value("")
    expect(_filas(page)).to_have_count(2)


def test_prestamos_orden_por_columna(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "loans")
    expect(_filas(page)).to_have_count(3)
    cabecera = page.locator('#tableWrap th[data-key="surname"]')

    cabecera.click()
    expect(page.locator('#tableWrap th[data-key="surname"]')).to_have_text("Apellido ↑")
    expect(_columna(page, 2)).to_contain_text(["Gómez", "López", "Pérez"])

    page.locator('#tableWrap th[data-key="surname"]').click()
    expect(page.locator('#tableWrap th[data-key="surname"]')).to_have_text("Apellido ↓")
    expect(_columna(page, 2)).to_contain_text(["Pérez", "López", "Gómez"])

    page.locator('#tableWrap th[data-key="date_due"]').click()
    expect(_columna(page, 4)).to_have_text(["Rayuela", "Operación masacre", "Ficciones"])


def test_prestamos_icono_de_nota_y_su_aviso(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "loans")
    # Ana avisó algo hace un día: su apellido lleva 📝. Bruno y Carla no (la de Bruno
    # es de cuotas y la vieja pasó los 60 días; la de Carla es un reclamo).
    icono = page.locator("#tableWrap button.nota-ico")
    expect(icono).to_have_count(1)
    expect(icono).to_have_attribute("data-aviso", "100")
    expect(icono).to_have_attribute("title", datos.NOTA_ANA)

    icono.click()
    modal = page.locator(".modal", has_text="Novedades sin resolver")
    expect(modal).to_contain_text("Carnet 100")
    expect(modal).to_contain_text(datos.NOTA_ANA)
    modal.locator("#avFicha").click()
    expect(modal).to_have_count(0)
    expect(page.locator("#fichaView h2")).to_have_text("Ana Pérez")


# ── Socios ──────────────────────────────────────────────────────────────────
def _buscar_socio(page: Page, termino: str) -> None:
    page.locator("#memberQuery").fill(termino)
    page.locator("#memberSearchBtn").click()


def test_socios_buscar_por_apellido_y_por_carnet(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "members")
    expect(page.locator("#tableWrap")).to_contain_text("Buscá un socio")

    _buscar_socio(page, "Gómez")
    expect(_filas(page)).to_have_count(1)
    expect(_filas(page)).to_contain_text("Bruno")
    expect(_filas(page)).to_contain_text("bruno@example.org")
    expect(_filas(page)).to_contain_text("11-2222-2222")
    expect(page.locator("#rowCount")).to_have_text("1 resultado(s).")

    page.locator("#memberQuery").fill("300")
    page.locator("#memberQuery").press("Enter")        # también con Enter
    expect(_filas(page)).to_have_count(1)
    expect(_filas(page)).to_contain_text("López")
    expect(_filas(page)).to_contain_text("Carla")

    _buscar_socio(page, "z")                            # los cuatro tienen una z
    expect(_filas(page)).to_have_count(4)
    fila_diego = _filas(page).filter(has_text="Díaz")
    expect(fila_diego).to_contain_text("Baja")

    _buscar_socio(page, "Zamudio")
    expect(page.locator("#tableWrap")).to_have_text("Sin resultados.")


def test_socios_ficha_completa_y_volver(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "members")
    _buscar_socio(page, "Pérez")
    _filas(page).filter(has_text="Ana").click()

    ficha = page.locator("#fichaView")
    expect(ficha.locator("h2")).to_have_text("Ana Pérez")
    datos_ficha = ficha.locator(".ficha-head .datos")
    expect(datos_ficha).to_contain_text("Carnet 100 · Categoría Activo")
    expect(datos_ficha).to_contain_text("ana@example.org · 11-1111-1111")
    expect(datos_ficha).to_contain_text("Calle Falsa 123, Buenos Aires")
    expect(datos_ficha).to_contain_text("Socio desde 2020-03-01")
    expect(page.locator("#listView")).to_be_hidden()

    vigentes = ficha.locator(".seccion", has=page.locator("h3", has_text="Préstamos vigentes"))
    expect(vigentes.locator(".count")).to_have_text("1")
    expect(vigentes.locator("tbody tr")).to_have_count(1)
    expect(vigentes.locator("tbody tr")).to_contain_text("B-001")
    expect(vigentes.locator("tbody tr")).to_contain_text("Rayuela")
    expect(vigentes.locator("tbody tr")).to_contain_text(_vence(-10))

    historial = ficha.locator(".seccion", has=page.locator("h3", has_text="Historial"))
    expect(historial.locator(".count")).to_have_text("1")
    expect(historial.locator("tbody tr")).to_contain_text("El Aleph")
    expect(historial.locator("tbody tr")).to_contain_text("Borges")
    expect(historial.locator("tbody tr")).to_contain_text("2026-01-24")

    notas = ficha.locator(".seccion", has=page.locator("h3", has_text="Notas"))
    expect(notas.locator(".count")).to_have_text("1")
    expect(notas.locator(".notas-destacadas")).to_contain_text("Novedad reciente")
    expect(notas.locator(".notas-destacadas")).to_contain_text(datos.NOTA_ANA)

    ficha.get_by_role("button", name="← Volver a la lista").click()
    expect(ficha).to_be_hidden()
    expect(page.locator("#listView")).to_be_visible()
    expect(_filas(page)).to_have_count(1)                # la búsqueda sigue ahí
    expect(page.locator("#memberQuery")).to_have_value("Pérez")


def test_socios_ficha_sin_historial_ni_notas(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "members")
    _buscar_socio(page, "Gómez")
    _filas(page).first.click()

    ficha = page.locator("#fichaView")
    expect(ficha.locator("h2")).to_have_text("Bruno Gómez")
    vigentes = ficha.locator(".seccion", has=page.locator("h3", has_text="Préstamos vigentes"))
    expect(vigentes).to_contain_text("Ficciones")
    historial = ficha.locator(".seccion", has=page.locator("h3", has_text="Historial"))
    expect(historial.locator(".count")).to_have_text("0")
    expect(historial).to_contain_text("Sin registros.")
    # Bruno tiene dos notas: la de cuotas va plegada aparte; la novedad vieja, a la vista
    # pero sin destacar (ya no es reciente).
    notas = ficha.locator(".seccion", has=page.locator("h3", has_text="Notas"))
    expect(notas.locator(".count")).to_have_text("2")
    expect(notas.locator(".notas-destacadas")).to_have_count(0)
    expect(notas).to_contain_text(NOTA_VIEJA_BRUNO)
    expect(notas.locator("details summary")).to_have_text("Registros de cuotas (1)")
    expect(notas.locator("details")).not_to_have_attribute("open", "")


def test_socios_ficha_de_quien_no_tiene_notas(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "members")
    _buscar_socio(page, "Díaz")
    _filas(page).first.click()
    ficha = page.locator("#fichaView")
    expect(ficha.locator("h2")).to_have_text("Diego Díaz")
    expect(ficha).to_contain_text("Categoría Baja")
    expect(ficha).to_contain_text("Este socio no tiene notas en Koha.")
    expect(ficha.locator(".seccion", has=page.locator("h3", has_text="Préstamos vigentes"))
           .locator(".count")).to_have_text("0")


# ── Notas de socios ─────────────────────────────────────────────────────────
def _notas(page: Page):
    return page.locator("#notasLista .nota")


def test_notas_lista_por_tipo_y_periodo(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "notas")
    expect(page.locator("#pageTitle")).to_have_text("Notas de socios")
    # De entrada: novedades de los últimos 30 días → solo la de Ana.
    expect(_notas(page)).to_have_count(1)
    expect(_notas(page)).to_contain_text("Pérez, Ana (100)")
    expect(_notas(page)).to_contain_text(datos.NOTA_ANA)
    expect(_notas(page).locator(".badge")).to_have_text("Novedad")
    tipos = page.locator("#notasTipo button")
    expect(tipos).to_have_text(["Novedades (1)", "Reclamos (1)", "Cuotas (1)", "Todas (3)"])
    expect(page.locator("#notasPie")).to_contain_text("1 nota(s)")
    expect(page.locator("#notasPie")).to_contain_text("1 novedad(es) sin resolver")

    page.locator('#notasTipo button[data-tipo="reclamo"]').click()
    expect(_notas(page)).to_have_count(1)
    expect(_notas(page)).to_contain_text("López, Carla (300)")
    expect(_notas(page)).to_contain_text(NOTA_RECLAMO_CARLA)
    expect(_notas(page).get_by_role("button")).to_have_count(0)   # un reclamo no se "resuelve"

    page.locator('#notasTipo button[data-tipo=""]').click()
    expect(_notas(page)).to_have_count(3)
    # De la más nueva a la más vieja.
    expect(_notas(page).locator(".texto")).to_have_text(
        [datos.NOTA_ANA, NOTA_RECLAMO_CARLA, NOTA_CUOTAS_BRUNO])

    page.locator("#notasDias").select_option("365")
    expect(_notas(page)).to_have_count(4)
    expect(tipos).to_have_text(["Novedades (2)", "Reclamos (1)", "Cuotas (1)", "Todas (4)"])
    expect(_notas(page).last).to_contain_text(NOTA_VIEJA_BRUNO)


def test_notas_buscar_recorre_todo_el_historial(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "notas")
    expect(_notas(page)).to_have_count(1)

    page.locator("#notasQ").fill("kindle")
    page.locator("#notasBuscar").click()
    expect(_notas(page)).to_have_count(1)
    expect(_notas(page)).to_contain_text("Gómez, Bruno (200)")
    expect(_notas(page)).to_contain_text(NOTA_VIEJA_BRUNO)
    expect(page.locator("#notasPie")).to_contain_text("con “kindle” en todo el historial")
    expect(page.locator("#notasDias")).to_be_disabled()

    page.locator("#notasQ").fill("pingüino")
    page.locator("#notasQ").press("Enter")
    expect(page.locator("#notasLista")).to_have_text("Ninguna nota tiene ese texto.")

    # Vaciar el buscador vuelve al período elegido.
    page.locator("#notasQ").fill("")
    page.locator("#notasBuscar").click()
    expect(_notas(page)).to_have_count(1)
    expect(_notas(page)).to_contain_text(datos.NOTA_ANA)
    expect(page.locator("#notasDias")).to_be_enabled()


def test_notas_marcar_resuelta_y_reabrir(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "notas")
    nota = page.locator('#notasLista .nota[data-nota-id="1"]')
    nota.get_by_role("button", name="Marcar resuelta").click()
    expect(nota).to_have_class(re.compile(r"\bresuelta\b"))
    expect(nota.locator(".nota-marca")).to_contain_text("✓ Resuelta por biblio")
    expect(nota.get_by_role("button")).to_have_text("Reabrir")

    # Queda guardado: al volver a entrar sigue resuelta…
    page.reload()
    expect(page.locator("#appView")).to_be_visible()
    ir_a(page, "notas")
    expect(nota.locator(".nota-marca")).to_contain_text("Resuelta por biblio")
    # …y ya no cuenta como pendiente.
    page.locator("#notasPendientes").check()
    expect(page.locator("#notasLista")).to_have_text(
        "No hay novedades sin resolver en este período.")
    page.locator("#notasPendientes").uncheck()

    # Tampoco aparece el 📝 en Préstamos.
    ir_a(page, "loans")
    expect(_filas(page)).to_have_count(3)
    expect(page.locator("#tableWrap button.nota-ico")).to_have_count(0)

    ir_a(page, "notas")
    nota.get_by_role("button", name="Reabrir").click()
    expect(nota).not_to_have_class(re.compile(r"\bresuelta\b"))
    expect(nota.locator(".nota-marca")).to_have_count(0)
    expect(nota.get_by_role("button")).to_have_text("Marcar resuelta")
    ir_a(page, "loans")
    expect(page.locator("#tableWrap button.nota-ico")).to_have_count(1)


def test_notas_abren_la_ficha_y_se_resuelven_ahi(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "notas")
    _notas(page).locator(".quien").click()
    ficha = page.locator("#fichaView")
    expect(ficha.locator("h2")).to_have_text("Ana Pérez")
    expect(page.locator("#notasView")).to_be_hidden()

    nota = ficha.locator('.nota[data-nota-id="1"]')
    expect(nota).to_contain_text(datos.NOTA_ANA)
    expect(nota.locator(".quien")).to_have_count(0)          # en la ficha no repite el socio
    nota.get_by_role("button", name="Marcar resuelta").click()
    expect(nota.locator(".nota-marca")).to_contain_text("Resuelta por biblio")

    ficha.get_by_role("button", name="← Volver a las notas").click()
    expect(page.locator("#notasView")).to_be_visible()
    expect(ficha).to_be_hidden()
    # La lista de notas se vuelve a leer al entrar: ya figura resuelta.
    ir_a(page, "notas")
    expect(page.locator('#notasLista .nota[data-nota-id="1"] .nota-marca')).to_contain_text(
        "Resuelta por biblio")


# ── Inicio y panel estratégico ──────────────────────────────────────────────
def test_inicio_con_los_prestamos(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "stats")
    expect(page.locator("#pageTitle")).to_have_text("Inicio")
    for etiqueta, valor in [("Préstamos activos", "3"), ("Vencidos", "2"), ("En préstamo", "1"),
                            ("% vencidos", "66.7%"), ("Socios con préstamos", "3"),
                            ("Socios que deben", "2")]:
        expect(_kpi(page, "#statsContent", etiqueta)).to_have_text(valor)
    # El catálogo viene vacío en las pruebas: se muestra en cero, sin error.
    expect(page.locator("#catalogSection h2")).to_have_text("Catálogo")
    expect(_kpi(page, "#catalogSection", "Ejemplares inventariados")).to_have_text("0")
    expect(_kpi(page, "#catalogSection", "% sin circular")).to_have_text("0%")
    expect(page.locator("#statsContent .msg.error")).to_have_count(0)


def test_inicio_con_catalogo(bibliotecaria: Page, monkeypatch):
    sql_con_notas = datos.sql

    def sql(consulta: str) -> list[dict]:
        if "AS ejemplares" in consulta:
            return [{"ejemplares": "1200", "titulos": "950", "sin_circular": "300",
                     "perdidos": "7", "danados": "4", "retirados": "6"}]
        if "i.issues AS count" in consulta:
            return [{"label": "Rayuela", "count": "41"}]
        return sql_con_notas(consulta)

    page = bibliotecaria
    ir_a(page, "stats")
    expect(_kpi(page, "#catalogSection", "Ejemplares inventariados")).to_have_text("0")
    # Llegan datos nuevos de Koha: "Actualizar" los vuelve a leer (el catálogo va cacheado).
    monkeypatch.setattr(datos, "sql", sql)
    page.get_by_role("button", name="↻ Actualizar").click()
    for etiqueta, valor in [("Ejemplares inventariados", "1.200"), ("Títulos", "950"),
                            ("Nunca circularon", "300"), ("% sin circular", "25%"),
                            ("Perdidos", "7"), ("Dañados / retirados", "10")]:
        expect(_kpi(page, "#catalogSection", etiqueta)).to_have_text(valor)


def test_estrategia_sin_datos_no_rompe(bibliotecaria: Page):
    page = bibliotecaria
    ir_a(page, "estrategia")
    expect(page.locator("#pageTitle")).to_have_text("Panel estratégico")
    contenido = page.locator("#estrategiaContent")
    expect(contenido.locator("h2")).to_have_text(
        [re.compile("Crecimiento de la biblioteca"), "Socios",
         "Acervo: crecimiento y antigüedad", "Estacionalidad"])
    expect(contenido.locator(".kpi")).to_have_count(4)
    expect(contenido.locator(".msg.error")).to_have_count(0)


def test_estrategia_con_numeros(bibliotecaria: Page, monkeypatch):
    sql_con_notas = datos.sql

    def sql(consulta: str) -> list[dict]:
        if "AS activos12" in consulta:
            return [{"total": "1500", "activos12": "600", "nunca": "300"}]
        if "AS total_items" in consulta:
            return [{"total_items": "1000", "nuevos12": "50", "ult5": "400"}]
        return sql_con_notas(consulta)

    monkeypatch.setattr(datos, "sql", sql)
    page = bibliotecaria
    ir_a(page, "estrategia")
    c = "#estrategiaContent"
    expect(_kpi(page, c, "Socios registrados")).to_have_text("1.500")
    expect(_kpi(page, c, "40% activos (último año)")).to_have_text("600")
    expect(_kpi(page, c, "Dormidos (sin sacar +1 año)")).to_have_text("600")
    expect(_kpi(page, c, "Nunca sacaron")).to_have_text("300")
    expect(page.locator(c)).to_contain_text("600 socios dormidos = oportunidad de reactivación")
