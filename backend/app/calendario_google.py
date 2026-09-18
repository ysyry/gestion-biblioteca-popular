"""Publica las reservas aprobadas en el Google Calendar de la biblioteca.

La app es la fuente de verdad y Google es la vidriera: lo que se aprueba, se cancela o
se reprograma acá se copia allá, nunca al revés. Si alguien toca el evento en Google,
la próxima vez que la reserva cambie en la app se pisa con lo de la app.

Cada fecha de una reserva es un evento en Google, con un id armado a partir de la
solicitud (`bpob<id>n<número>`). Publicar dos veces lo mismo no duplica nada: la
segunda vez actualiza. Por eso se puede reintentar sin miedo cuando Google falla, y
una aprobación nunca se pierde por un error de Google: la reserva queda aprobada en
la app y marcada "pendiente de publicar" hasta que salga.

A qué calendario (variable RESERVAS_CALENDARIO, por defecto "Bayer Band"):
  · el nombre de uno de los calendarios de la agenda (CALENDAR_<n>_NAME): el id se
    saca de su dirección iCal;
  · o directamente el id del calendario (algo@gmail.com, …@group.calendar.google.com).

Qué hace falta en Google: compartir ese calendario con la cuenta de servicio (la misma
de las cuotas, ver `cuenta_google.py`) con el permiso "Hacer cambios en los eventos".
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from urllib.parse import quote

from . import agenda, cuenta_google, espacios, solicitudes

logger = logging.getLogger("calendario_google")

_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
_API = "https://www.googleapis.com/calendar/v3/calendars/{cal}/events"
_TIMEOUT = 20
TZ = os.getenv("APP_TZ", "America/Argentina/Buenos_Aires")

# Una sola sincronización a la vez: si se aprueba y enseguida se cancela, la segunda
# espera a la primera y encuentra a Google como lo dejó, en vez de pisarse con ella.
_TURNO = asyncio.Lock()


class ErrorGoogle(RuntimeError):
    """Google contestó que no. El mensaje se muestra tal cual en la bandeja."""


# ── Configuración ───────────────────────────────────────────────────────────
def nombre_calendario() -> str:
    return os.getenv("RESERVAS_CALENDARIO", "Bayer Band").strip()


def calendario_id() -> str:
    """El id del calendario destino, o "" si no se encuentra."""
    destino = nombre_calendario()
    return destino if "@" in destino else agenda.calendario_id(destino)


def configurado() -> bool:
    return bool(calendario_id()) and cuenta_google.configurada()


def estado() -> dict:
    """Para mostrar en la app a qué calendario va lo aprobado y si está listo."""
    return {"configurado": configurado(), "calendario": nombre_calendario(),
            "cuenta": cuenta_google.email() if cuenta_google.configurada() else ""}


# ── Armado de los eventos ───────────────────────────────────────────────────
def evento_id(sid: str, n: int) -> str:
    """Id fijo del evento n de una solicitud. Google acepta solo 0-9 y a-v."""
    base = sid if re.fullmatch(r"[0-9a-v]+", sid or "") else hashlib.sha1(sid.encode()).hexdigest()
    return f"bpob{base}n{n:03d}"


def _hora(iso: str) -> dict:
    return {"dateTime": f"{iso[:16]}:00", "timeZone": TZ}


def armar_evento(s: dict, n: int) -> dict:
    f = s["fechas"][n]
    q = s.get("solicitante") or {}
    responsable = q.get("subcomision") or q.get("nombre") or q.get("usuario") or ""
    lugar = espacios.nombre_de(s.get("espacio_id", ""))
    renglones = [s.get("descripcion") or "", ""]
    if responsable:
        renglones.append(f"Responsable: {responsable}")
    if s.get("personas"):
        renglones.append(f"Personas estimadas: {s['personas']}")
    if s.get("necesidades"):
        renglones.append("Necesita: " + ", ".join(s["necesidades"]))
    renglones.append("Abierta al público" if s.get("abierta_publico") else "Actividad cerrada")
    renglones += ["", "Reserva aprobada en la app de gestión de la biblioteca. "
                      "Si hay que cambiarla, se cambia en la app, no acá."]
    return {
        "id": evento_id(s["id"], n),
        "summary": s.get("titulo", ""),
        "location": lugar,
        "description": "\n".join(renglones).strip(),
        "start": _hora(f["inicio"]),
        "end": _hora(f["fin"]),
        "status": "confirmed",
        "extendedProperties": {"private": {"solicitud_id": s["id"]}},
    }


# ── Hablar con Google ───────────────────────────────────────────────────────
def _sesion():
    from google.auth.transport.requests import AuthorizedSession

    return AuthorizedSession(cuenta_google.credenciales(_SCOPES))


def _detalle(r) -> str:
    try:
        texto = (r.json().get("error") or {}).get("message") or r.text
    except Exception:  # noqa: BLE001
        texto = r.text
    if r.status_code == 403 and ("has not been used" in str(texto) or "disabled" in str(texto)):
        return ("Falta activar la Google Calendar API en el proyecto de la cuenta de servicio "
                "(console.cloud.google.com → APIs y servicios → Google Calendar API → Habilitar).")
    if r.status_code in (403, 404):
        return (f"Google no deja escribir en el calendario '{nombre_calendario()}' "
                f"({r.status_code}). Hay que compartirlo con {cuenta_google.email() or 'la cuenta de servicio'} "
                "con el permiso “Hacer cambios en los eventos”.")
    return f"Google Calendar respondió {r.status_code}: {str(texto)[:200]}"


def _guardar_evento(ses, base: str, ev: dict) -> None:
    """Crea o actualiza el evento. Sirve también para revivir uno que se había borrado."""
    url = f"{base}/{ev['id']}"
    r = ses.put(url, json=ev, timeout=_TIMEOUT)
    if r.status_code == 404:                    # todavía no existe: se crea
        r = ses.post(base, json=ev, timeout=_TIMEOUT)
        if r.status_code == 409:                # justo lo creó otro: se actualiza
            r = ses.put(url, json=ev, timeout=_TIMEOUT)
    if r.status_code >= 400:
        raise ErrorGoogle(_detalle(r))


def _borrar_evento(ses, base: str, eid: str) -> None:
    r = ses.delete(f"{base}/{eid}", timeout=_TIMEOUT)
    if r.status_code not in (200, 204, 404, 410):    # 404/410: ya no estaba
        raise ErrorGoogle(_detalle(r))


def aplicar(s: dict, *, ses=None) -> int:
    """Deja Google como dice la solicitud. Devuelve cuántos eventos quedaron publicados.

    Aprobada: un evento por fecha, y se borran los que sobran de una versión anterior
    con más fechas. Cualquier otro estado: se borra todo lo que se haya publicado.
    """
    ses = ses or _sesion()
    base = _API.format(cal=quote(calendario_id(), safe=""))
    previos = int(s.get("google_eventos") or 0)
    quedan = len(s.get("fechas", [])) if s.get("estado") == solicitudes.APROBADA else 0
    for n in range(quedan):
        _guardar_evento(ses, base, armar_evento(s, n))
    for n in range(quedan, previos):
        _borrar_evento(ses, base, evento_id(s["id"], n))
    return quedan


async def sincronizar(sid: str) -> None:
    """Publica o retira una solicitud en Google. Nunca levanta: anota el error y listo."""
    if not configurado():
        return
    async with _TURNO:
        s = solicitudes.obtener(sid)
        if s is None or not s.get("google_pendiente"):
            return
        publicada = solicitudes.huella(s)
        if s.get("estado") == solicitudes.APROBADA:
            solicitudes.reservar_eventos_google(sid, len(s.get("fechas", [])))
            s = solicitudes.obtener(sid) or s
        try:
            n = await asyncio.to_thread(aplicar, s)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Google Calendar: la solicitud %s no se pudo sincronizar: %s", sid, exc)
            solicitudes.marcar_google(sid, publicada, error=str(exc) or exc.__class__.__name__)
            return
        solicitudes.marcar_google(sid, publicada, eventos=n)
        logger.info("Google Calendar: solicitud %s sincronizada (%d eventos)", sid, n)


async def reintentar_pendientes() -> None:
    """Pasada periódica: lo que quedó sin publicar (Google caído, un corte) se reintenta."""
    for sid in solicitudes.pendientes_google():
        await sincronizar(sid)
