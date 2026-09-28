"""Lo mínimo: se entra, y cada sección del menú abre sin errores."""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from .ayudas import crear_usuario, entrar, ir_a


def test_login_equivocado_avisa(page: Page):
    page.goto("/")
    page.locator("#u").fill("biblio")
    page.locator("#p").fill("mal")
    page.locator("#loginBtn").click()
    expect(page.locator("#loginMsg")).to_contain_text("incorrect")
    expect(page.locator("#appView")).to_be_hidden()


def _secciones(page: Page) -> list[str]:
    return page.locator("#tabs button[data-tab]").evaluate_all(
        "bs => bs.map(b => b.dataset.tab)")


def _recorrer(page: Page):
    secciones = _secciones(page)
    assert secciones, "El menú quedó vacío"
    for s in secciones:
        ir_a(page, s)
        expect(page.locator("#pageTitle")).not_to_be_empty()
        # Ninguna sección se queda mostrando un error del servidor.
        expect(page.locator("#panelMsg .msg.error")).to_have_count(0)
    return secciones


def test_bibliotecaria_recorre_todo_el_menu(bibliotecaria: Page):
    secciones = _recorrer(bibliotecaria)
    for s in ("mails", "auto", "envios", "loans", "members", "cuotas", "usuarios"):
        assert s in secciones


@pytest.mark.parametrize("rol,subcomision", [("comision", ""), ("subcomision", "Cultura")])
def test_usuarios_de_la_app_recorren_su_menu(page: Page, rol, subcomision):
    u = crear_usuario(rol, subcomision=subcomision)
    entrar(page, u["usuario"], u["clave"])
    _recorrer(page)


def test_salir_vuelve_al_login(bibliotecaria: Page):
    bibliotecaria.locator("#logoutBtn").click()
    expect(bibliotecaria.locator("#loginView")).to_be_visible()
    bibliotecaria.reload()
    expect(bibliotecaria.locator("#loginView")).to_be_visible()
    expect(bibliotecaria.locator("#appView")).to_be_hidden()


def test_titulo(bibliotecaria: Page):
    expect(bibliotecaria).to_have_title(re.compile(r"\S"))
