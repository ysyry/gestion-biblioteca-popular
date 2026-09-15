"""Espacios de la biblioteca que se pueden reservar (sala, patio, salón…).

Se guardan en `storage` bajo la clave `espacios` y se editan desde la app: la lista
real la define la biblioteca, no el código. La primera vez se siembra una lista de
arranque (tomada de la variable ESPACIOS si está, o unos pocos nombres de ejemplo)
para que el módulo sea usable desde el minuto cero; después se corrige desde la app.
"""
from __future__ import annotations

import logging
import os
import secrets
import threading
from datetime import datetime, timezone

from . import storage

logger = logging.getLogger("espacios")

CLAVE = "espacios"
# Reentrante a propósito: `crear`/`actualizar` toman el lock y adentro llaman a
# `_leer()`, que la primera vez siembra la lista y necesita el lock también.
_LOCK = threading.RLock()

# Nombres de arranque, solo hasta que la biblioteca cargue los suyos.
_DE_EJEMPLO = ["Sala principal", "Sala infantil", "Patio", "Salón de talleres"]


class ErrorEspacio(ValueError):
    """Dato inválido al crear o editar un espacio (se traduce a HTTP 400)."""


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _nuevo(nombre: str, capacidad: int = 0, notas: str = "", ejemplo: bool = False) -> dict:
    return {
        "id": secrets.token_hex(6),
        "nombre": nombre.strip(),
        "capacidad": max(int(capacidad or 0), 0),
        "notas": notas.strip(),
        "activo": True,
        # Marca los sembrados automáticamente, para que la app pueda avisar
        # "esto es de ejemplo, corregilo" en vez de hacerlos pasar por reales.
        "de_ejemplo": ejemplo,
        "creado": _ahora(),
    }


def _semilla() -> list[dict]:
    de_env = [n.strip() for n in os.getenv("ESPACIOS", "").split(",") if n.strip()]
    nombres = de_env or _DE_EJEMPLO
    return [_nuevo(n, ejemplo=not de_env) for n in nombres]


def _leer() -> list[dict]:
    datos = storage.get(CLAVE)
    if isinstance(datos, list):
        return datos
    with _LOCK:                      # primera vez: sembramos y guardamos
        datos = storage.get(CLAVE)
        if isinstance(datos, list):
            return datos
        inicial = _semilla()
        storage.set(CLAVE, inicial)
        logger.info("Espacios sembrados (%d).", len(inicial))
        return inicial


def listar(incluir_inactivos: bool = False) -> list[dict]:
    espacios = _leer()
    if not incluir_inactivos:
        espacios = [e for e in espacios if e.get("activo")]
    return sorted(espacios, key=lambda e: e.get("nombre", "").lower())


def obtener(eid: str) -> dict | None:
    return next((e for e in _leer() if e.get("id") == eid), None)


def nombre_de(eid: str) -> str:
    e = obtener(eid)
    return e["nombre"] if e else "(espacio borrado)"


def exigir(eid: str) -> dict:
    """Devuelve el espacio o corta: reservar en un espacio que no existe no va."""
    e = obtener(eid)
    if e is None:
        raise ErrorEspacio("Ese espacio no existe.")
    if not e.get("activo"):
        raise ErrorEspacio(f"El espacio «{e['nombre']}» está dado de baja.")
    return e


def crear(nombre: str, capacidad: int = 0, notas: str = "") -> dict:
    if not (nombre or "").strip():
        raise ErrorEspacio("Falta el nombre del espacio.")
    with _LOCK:
        lista = _leer()
        if any(e.get("nombre", "").strip().lower() == nombre.strip().lower() for e in lista):
            raise ErrorEspacio(f"Ya existe un espacio llamado «{nombre.strip()}».")
        e = _nuevo(nombre, capacidad, notas)
        lista.append(e)
        storage.set(CLAVE, lista)
    return e


_EDITABLES = {"nombre", "capacidad", "notas", "activo"}


def actualizar(eid: str, cambios: dict) -> dict:
    cambios = {k: v for k, v in (cambios or {}).items() if k in _EDITABLES}
    with _LOCK:
        lista = _leer()
        e = next((x for x in lista if x.get("id") == eid), None)
        if e is None:
            raise ErrorEspacio("Ese espacio no existe.")
        if "nombre" in cambios:
            nombre = (cambios["nombre"] or "").strip()
            if not nombre:
                raise ErrorEspacio("Falta el nombre del espacio.")
            if any(o.get("id") != eid and o.get("nombre", "").strip().lower() == nombre.lower()
                   for o in lista):
                raise ErrorEspacio(f"Ya existe un espacio llamado «{nombre}».")
            cambios["nombre"] = nombre
        if "capacidad" in cambios:
            cambios["capacidad"] = max(int(cambios["capacidad"] or 0), 0)
        e.update(cambios)
        e["de_ejemplo"] = False       # si lo editaron, ya no es de ejemplo
        storage.set(CLAVE, lista)
    return e


def borrar(eid: str) -> None:
    """Baja definitiva. Si el espacio tiene reservas, conviene desactivarlo."""
    with _LOCK:
        lista = _leer()
        quedan = [e for e in lista if e.get("id") != eid]
        if len(quedan) == len(lista):
            raise ErrorEspacio("Ese espacio no existe.")
        storage.set(CLAVE, quedan)
