"""Pruebas de punta a punta: un navegador de verdad recorriendo la app.

Correr (desde backend/):
    pip install -r requirements-dev.txt && playwright install chromium
    pytest e2e                 # sin ventana
    pytest e2e --headed        # mirando el navegador

Cada test arranca con la app vacía (ver `servidor.reiniciar`). Un error de JavaScript
en la página hace fallar el test aunque la pantalla parezca andar.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from e2e import servidor  # noqa: E402  (fija el entorno antes de importar la app)

import pytest  # noqa: E402
from playwright.sync_api import Page, expect  # noqa: E402

from e2e.ayudas import entrar  # noqa: E402

expect.set_options(timeout=10_000)


@pytest.fixture(scope="session")
def base_url():
    url = servidor.arrancar()
    yield url
    servidor.frenar()


@pytest.fixture(autouse=True)
def _app_vacia():
    servidor.reiniciar()
    yield


@pytest.fixture
def bandeja() -> list[dict]:
    """Los mails que la app mandó en este test (lo que le llegaría a cada uno)."""
    return servidor.BANDEJA


@pytest.fixture
def page(page: Page):
    """La página de Playwright, pero vigilando errores de JavaScript."""
    errores: list[str] = []
    page.on("pageerror", lambda e: errores.append(str(e)))
    page.on("console", lambda m: errores.append(m.text) if m.type == "error"
            and "Failed to load resource" not in m.text else None)
    yield page
    assert not errores, "Errores de JavaScript en la página:\n" + "\n".join(errores)


@pytest.fixture
def bibliotecaria(page: Page) -> Page:
    """Adentro como bibliotecaria (usuario de Koha)."""
    entrar(page, servidor.USUARIO_KOHA, servidor.CLAVE_KOHA)
    return page
