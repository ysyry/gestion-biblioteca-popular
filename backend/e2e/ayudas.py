"""Atajos para los tests de punta a punta: entrar, moverse por el menú, dar de alta."""
from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from app import usuarios


def crear_usuario(rol: str, usuario: str | None = None, subcomision: str = "",
                  clave: str = "clave-e2e") -> dict:
    """Da de alta un usuario propio de la app (comisión, subcomisión…)."""
    usuario = usuario or f"{rol}1"
    u, _ = usuarios.crear(usuario=usuario, nombre=f"{rol.title()} de Prueba", rol=rol,
                          password=clave, subcomision=subcomision)
    return {**u, "clave": clave}


def entrar(page: Page, usuario: str, clave: str) -> None:
    page.goto("/")
    page.locator("#u").fill(usuario)
    page.locator("#p").fill(clave)
    page.locator("#loginBtn").click()
    expect(page.locator("#appView")).to_be_visible()
    expect(page.locator("#userLabel")).not_to_be_empty()


def ir_a(page: Page, seccion: str) -> None:
    """Abre una sección del menú (despliega su grupo si hace falta)."""
    boton = page.locator(f'#tabs button[data-tab="{seccion}"]')
    if not boton.is_visible():
        grupo = page.locator("#tabs .tab-grupo",
                             has=page.locator(f'button[data-tab="{seccion}"]'))
        grupo.locator(".grupo-cab").click()
    boton.click()
    expect(boton).to_have_class(re.compile(r"\bactive\b"))
