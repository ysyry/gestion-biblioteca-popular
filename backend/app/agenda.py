"""Agenda de actividades: lee uno o varios Google Calendars de la biblioteca (solo lectura).

Fuente: URLs secretas en formato iCal (Google Calendar → Configuración → "Integrar
calendario" → "Dirección secreta en formato iCal"). No requiere OAuth ni hacer
público el calendario.

Soporta VARIOS calendarios (talleres, Bayer Experimental, Bayer Band, vencimientos…).
Formato recomendado: una pareja de variables por calendario, numeradas:
    CALENDAR_1_NAME=Talleres
    CALENDAR_1_URL=https://...ics
    CALENDAR_2_NAME=Bayer Experimental
    CALENDAR_2_URL=https://...ics
Compatibilidad: también valen CALENDAR_ICS_URLS (formato 'Nombre|url ; Nombre|url')
y CALENDAR_ICS_URL (una sola url).

Maneja eventos recurrentes (ej. cineclub semanal) expandiéndolos en el rango pedido.
"""
from __future__ import annotations

import datetime as dt
import logging
import os

import httpx

logger = logging.getLogger("agenda")

# Paleta para asignar color a cada calendario (en orden).
_PALETTE = ["#F5821F", "#861E92", "#15AA6C", "#C7002A", "#13235B", "#FBB016"]


def _raw_pairs() -> list[tuple[str, str]]:
    """Lee las parejas (nombre, url) priorizando el formato numerado."""
    pairs: list[tuple[str, str]] = []
    # 1) Formato numerado CALENDAR_<n>_URL / CALENDAR_<n>_NAME
    for n in range(1, 21):
        url = os.getenv(f"CALENDAR_{n}_URL", "").strip()
        if url:
            nombre = os.getenv(f"CALENDAR_{n}_NAME", f"Calendario {n}").strip()
            pairs.append((nombre, url))
    if pairs:
        return pairs
    # 2) Formato 'Nombre|url ; Nombre|url'
    raw = os.getenv("CALENDAR_ICS_URLS", "").strip()
    if raw:
        for i, chunk in enumerate(s for s in raw.split(";") if s.strip()):
            nombre, _, url = chunk.partition("|") if "|" in chunk else (f"Calendario {i+1}", "", chunk)
            if url.strip():
                pairs.append((nombre.strip(), url.strip()))
        return pairs
    # 3) Una sola URL
    url = os.getenv("CALENDAR_ICS_URL", "").strip()
    if url:
        pairs.append(("Actividades", url))
    return pairs


def _parse_sources() -> list[dict]:
    """Devuelve [{'nombre','url','color'}] desde las variables de entorno."""
    return [{"nombre": nombre, "url": url, "color": _PALETTE[i % len(_PALETTE)]}
            for i, (nombre, url) in enumerate(_raw_pairs())]


def configured() -> bool:
    return bool(_parse_sources())


def calendars() -> list[dict]:
    """Lista de calendarios configurados (nombre + color, sin URL) para el frontend."""
    return [{"nombre": s["nombre"], "color": s["color"]} for s in _parse_sources()]


def _iso(v) -> str | None:
    return v.isoformat() if v else None


def parse_ics(text: str, d1: dt.date, d2: dt.date, cal: dict) -> list[dict]:
    """Parsea un iCal y devuelve sus eventos entre d1 y d2 (expande recurrencias)."""
    import icalendar
    import recurring_ical_events

    calendar = icalendar.Calendar.from_ical(text)
    out: list[dict] = []
    for e in recurring_ical_events.of(calendar).between(d1, d2):
        start = e.get("DTSTART").dt if e.get("DTSTART") else None
        end = e.get("DTEND").dt if e.get("DTEND") else None
        out.append({
            "titulo": str(e.get("SUMMARY") or "(sin título)"),
            "lugar": str(e.get("LOCATION") or ""),
            "descripcion": str(e.get("DESCRIPTION") or ""),
            "inicio": _iso(start),
            "fin": _iso(end),
            "todo_el_dia": not isinstance(start, dt.datetime),
            "calendario": cal["nombre"],
            "color": cal["color"],
        })
    return out


async def events(d1: dt.date, d2: dt.date) -> list[dict]:
    """Descarga TODOS los calendarios configurados y devuelve sus eventos unidos."""
    sources = _parse_sources()
    if not sources:
        return []
    todos: list[dict] = []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        for cal in sources:
            try:
                r = await c.get(cal["url"])
                r.raise_for_status()
                todos.extend(parse_ics(r.text, d1, d2, cal))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Calendario '%s' no se pudo leer: %s", cal["nombre"], exc)
    todos.sort(key=lambda x: x["inicio"] or "")
    return todos
