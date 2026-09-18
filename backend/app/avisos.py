"""Avisos por mail a quien pidió un espacio, cuando se resuelve su pedido.

Se avisa cuando se aprueba (con lo que quedó, si se cambió algo), se rechaza, se
devuelve con observaciones o se cancela. Siempre que lo haga otra persona: si alguien
cancela lo suyo, no hace falta avisarle.

El mail va a la dirección cargada en el usuario de la app. Las bibliotecarias entran
con Koha y no tienen usuario acá: a ellas no se les avisa (son quienes resuelven).
Cada aviso queda anotado en la solicitud y en el historial de envíos (origen "avisos").
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from . import espacios, historial, mail, solicitudes, usuarios
from .config import settings

logger = logging.getLogger("avisos")

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]


def _cuando(inicio: str, fin: str) -> str:
    """'martes 10 de marzo, de 18:00 a 20:00'."""
    d = datetime.fromisoformat(inicio)
    return f"{_DIAS[d.weekday()]} {d.day} de {_MESES[d.month - 1]}, de {inicio[11:16]} a {fin[11:16]}"


def _dia(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    return f"{d.day} de {_MESES[d.month - 1]}"


def _reserva(s: dict) -> str:
    """Dónde y cuándo, en renglones."""
    renglones = [f"· Espacio: {espacios.nombre_de(s.get('espacio_id', ''))}",
                 f"· Cuándo: {_cuando(s['inicio'], s['fin'])}"]
    n = len(s.get("fechas", []))
    if n > 1:
        regla = solicitudes.REPETICIONES.get(s.get("repeticion"), "").lower()
        renglones.append(f"· Se repite: {regla}, {n} fechas hasta el {_dia(s['fechas'][-1]['inicio'])}")
    return "\n".join(renglones)


def armar(s: dict, que: str) -> tuple[str, str]:
    """Asunto y cuerpo del aviso. `que`: aprobar, rechazar, observar o cancelar."""
    titulo = s.get("titulo", "")
    motivo = ((s.get("resolucion") or {}).get("motivo") or "").strip()
    saludo = "Hola {{nombre}},\n\n"
    if settings.app_public_url:
        cierre = f"\n\nPodés ver tus solicitudes en {settings.app_public_url.rstrip('/')}"
    else:
        cierre = "\n\nPodés ver tus solicitudes en la app de la biblioteca."

    if que == "aprobar":
        cuerpo = f"Tu pedido de espacio para “{titulo}” fue aprobado. Quedó así:\n\n{_reserva(s)}"
        orig = s.get("pedido_original")
        if orig:
            cuerpo += ("\n\nOjo: no es exactamente lo que habías pedido. Habías pedido "
                       f"{espacios.nombre_de(orig.get('espacio_id', ''))}, el "
                       f"{_cuando(orig['inicio'], orig['fin'])}.")
        if motivo:
            cuerpo += f"\n\nComentario de la biblioteca: {motivo}"
        cuerpo += "\n\nYa figura en el calendario de la biblioteca."
        return f"Aprobado: {titulo}", saludo + cuerpo + cierre

    if que == "rechazar":
        cuerpo = (f"Tu pedido de espacio para “{titulo}” no se pudo aprobar.\n\n"
                  f"Motivo: {motivo}\n\n"
                  "Si querés, podés hacer un pedido nuevo con otra fecha u otro espacio.")
        return f"No se pudo aprobar: {titulo}", saludo + cuerpo + cierre

    if que == "observar":
        cuerpo = (f"La biblioteca revisó tu pedido de espacio para “{titulo}” y pide algunos "
                  f"cambios antes de aprobarlo:\n\n{motivo}\n\n"
                  "Entrá a la app, editá la solicitud y se vuelve a revisar.")
        return f"Tu pedido necesita cambios: {titulo}", saludo + cuerpo + cierre

    if que == "cancelar":
        razon = ((s.get("cancelacion") or {}).get("motivo") or "").strip()
        cuerpo = f"La reserva de “{titulo}” fue cancelada.\n\n{_reserva(s)}"
        if razon:
            cuerpo += f"\n\nMotivo: {razon}"
        return f"Reserva cancelada: {titulo}", saludo + cuerpo + cierre

    raise ValueError(f"Aviso desconocido: '{que}'.")


async def avisar(sid: str, que: str, *, por: str, por_uid: str | None) -> None:
    """Manda el aviso de lo que pasó con la solicitud. Nunca levanta: anota y sigue."""
    s = solicitudes.obtener(sid)
    if s is None:
        return
    q = s.get("solicitante") or {}
    if not q.get("uid") or q.get("uid") == por_uid:
        return                          # lo pidió una bibliotecaria, o lo hizo la misma persona

    u = usuarios.obtener(q["uid"]) or {}
    email = (u.get("email") or "").strip()
    if not email:
        solicitudes.anotar_aviso(sid, {"que": que, "a": "", "estado": "sin_email",
                                       "detalle": "El usuario no tiene mail cargado."})
        return

    asunto, cuerpo = armar(s, que)
    nombre = (u.get("nombre") or q.get("nombre") or "").split(" ")[0]
    dests = [{"email": email, "vars": {"nombre": nombre}}]
    run_id = historial.new_run_id()
    comun = {"run_id": run_id, "origen": "avisos", "titulo": asunto, "tipo": "solicitud",
             "trigger": "resolucion", "usuario": por, "dry_run": settings.mail_dry_run,
             "subject_tpl": asunto, "body_tpl": cuerpo}
    try:
        res = await mail.send_campaign(asunto, cuerpo, dests, dry_run=settings.mail_dry_run)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Aviso de la solicitud %s no enviado: %s", sid, exc)
        historial.record(**comun, dests=[], ok=False, error=str(exc))
        solicitudes.anotar_aviso(sid, {"que": que, "a": email, "estado": "error", "detalle": str(exc)})
        return
    historial.record(**comun, dests=historial.destinatarios(dests, res.get("resultados", [])))
    r = (res.get("resultados") or [{}])[0]
    solicitudes.anotar_aviso(sid, {"que": que, "a": email, "estado": r.get("status", ""),
                                   "detalle": r.get("detail", "")})
