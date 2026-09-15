"""Pizarrón semanal de las bibliotecarias: avisos, tareas y recordatorios del equipo.

Lo ven **solo las bibliotecarias** (decisión tomada: ni la comisión directiva ni las
subcomisiones). Es para lo que no es de un socio en particular; las notas de cada
socio viven en Koha (ver `notas.py`).

Cómo se arma una semana. Cada nota pertenece a la semana (lunes a domingo) en que se
escribió, o a la del día que se le puso. Al mirar una semana se ven:
  · **Fijadas**: las fijadas de esa semana o de antes. Quedan arriba hasta que se desfijan.
  · **Toda la semana** y **un grupo por día**: las notas de esa semana.
  · **Tareas que vienen de antes**: una tarea no hecha **pasa sola** a las semanas
    siguientes hasta que alguien la tilda. No se copia: se calcula al mirar, así no
    hay duplicados y el histórico queda como fue.
  · **Hechas**: las tareas que se tildaron durante esa semana.
Avisos y recordatorios quedan en su semana.

Guardado: `storage`, una clave por año (`pizarron_2026`) según la semana de la nota,
para que la lista no crezca sin fin. Al mirar una semana se leen ese año y el anterior
(las tareas pendientes y las fijadas pueden venir de diciembre).
"""
from __future__ import annotations

import logging
import os
import secrets
import threading
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import storage

logger = logging.getLogger("pizarron")

_LOCK = threading.Lock()
TZ = ZoneInfo(os.getenv("APP_TZ", "America/Argentina/Buenos_Aires"))
CLAVE_VISTO = "pizarron_visto"

AVISO, TAREA, RECORDATORIO = "aviso", "tarea", "recordatorio"
TIPOS = {AVISO: "Aviso", TAREA: "Tarea", RECORDATORIO: "Recordatorio"}
COLORES = ("amarillo", "rosa", "verde", "celeste", "lila")

MAX_TEXTO = 2000
MAX_RESPUESTA = 500


class ErrorPizarron(ValueError):
    """Error de validación o de permiso, con un mensaje para mostrar tal cual."""


class SinPermiso(ErrorPizarron):
    pass


class NoEncontrada(ErrorPizarron):
    pass


# ── Fechas ──────────────────────────────────────────────────────────────────
def hoy() -> date:
    return datetime.now(TZ).date()


def ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def lunes(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _inicio_utc(d: date) -> datetime:
    """Medianoche (hora de la biblioteca) del día `d`, en UTC: para comparar con los tildes."""
    return datetime.combine(d, time.min, tzinfo=TZ).astimezone(timezone.utc)


def _instante(iso: str | None) -> datetime | None:
    try:
        dt = datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return None
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _fecha(valor, campo: str) -> date:
    try:
        return valor if isinstance(valor, date) else date.fromisoformat(str(valor)[:10])
    except ValueError as exc:
        raise ErrorPizarron(f"Fecha inválida en '{campo}' (AAAA-MM-DD).") from exc


# ── Guardado ────────────────────────────────────────────────────────────────
def _clave(anio: int) -> str:
    return f"pizarron_{anio}"


def _leer(anio: int) -> list[dict]:
    return storage.get(_clave(anio)) or []


def _mismo(a: str | None, b: str | None) -> bool:
    """Koha no distingue mayúsculas en el usuario: "Laura" y "laura" son la misma persona."""
    return (a or "").strip().casefold() == (b or "").strip().casefold()


def _ubicar(nota_id: str) -> tuple[int, list[dict], dict]:
    """(año, lista de ese año, nota). El id empieza con el año de su semana."""
    anio_txt, _, _ = str(nota_id).partition("-")
    if not anio_txt.isdigit():
        raise NoEncontrada("Nota no encontrada.")
    anio = int(anio_txt)
    lista = _leer(anio)
    for n in lista:
        if n.get("id") == nota_id:
            return anio, lista, n
    raise NoEncontrada("Nota no encontrada (puede que la hayan borrado).")


# ── Validación ──────────────────────────────────────────────────────────────
def _limpiar(datos: dict, semana: date) -> dict:
    """Campos editables, validados. `semana` es el lunes de la nota (no cambia)."""
    texto = (datos.get("texto") or "").strip()
    if not texto:
        raise ErrorPizarron("La nota no puede estar vacía.")
    if len(texto) > MAX_TEXTO:
        raise ErrorPizarron(f"La nota es muy larga (máximo {MAX_TEXTO} caracteres).")

    tipo = datos.get("tipo") or AVISO
    if tipo not in TIPOS:
        raise ErrorPizarron(f"Tipo desconocido: '{tipo}'.")

    dia = datos.get("dia") or None
    if dia:
        d = _fecha(dia, "dia")
        if lunes(d) != semana:
            raise ErrorPizarron("El día tiene que caer dentro de la semana de la nota.")
        dia = d.isoformat()

    color = datos.get("color") or COLORES[0]
    if color not in COLORES:
        raise ErrorPizarron(f"Color desconocido: '{color}'.")

    socio = datos.get("socio") or None
    if socio:
        card = str(socio.get("cardnumber") or "").strip()
        socio = {"cardnumber": card, "nombre": str(socio.get("nombre") or "").strip()[:120]} if card else None

    return {
        "texto": texto,
        "tipo": tipo,
        "dia": dia,
        "para": str(datos.get("para") or "").strip()[:80],
        "socio": socio,
        "color": color,
        "fijada": bool(datos.get("fijada")),
    }


# ── Operaciones ─────────────────────────────────────────────────────────────
def crear(datos: dict, usuario: str, nombre: str) -> dict:
    """Crea una nota. La semana sale del día elegido, de `semana` o de hoy."""
    base = datos.get("dia") or datos.get("semana") or hoy()
    semana = lunes(_fecha(base, "semana"))
    nota = {
        "id": f"{semana.year}-{secrets.token_hex(5)}",
        "semana": semana.isoformat(),
        **_limpiar(datos, semana),
        "autor": {"usuario": usuario, "nombre": nombre or usuario},
        "creada": ahora_iso(),
        "editada": None,
        "hecha": None,
        "respuestas": [],
    }
    with _LOCK:
        lista = _leer(semana.year)
        lista.append(nota)
        storage.set(_clave(semana.year), lista)
    logger.info("Pizarrón: %s dejó una nota (%s) en la semana %s", usuario, nota["tipo"], nota["semana"])
    return nota


def editar(nota_id: str, datos: dict, usuario: str) -> dict:
    with _LOCK:
        anio, lista, n = _ubicar(nota_id)
        if not _mismo(n["autor"]["usuario"], usuario):
            raise SinPermiso("Solo quien escribió la nota puede editarla.")
        cambios = _limpiar({**n, **datos}, date.fromisoformat(n["semana"]))
        if n["tipo"] == TAREA and cambios["tipo"] != TAREA:
            n["hecha"] = None              # dejó de ser tarea: el tilde no aplica
        n.update(cambios)
        n["editada"] = ahora_iso()
        storage.set(_clave(anio), lista)
    return n


def borrar(nota_id: str, usuario: str) -> None:
    with _LOCK:
        anio, lista, n = _ubicar(nota_id)
        if not _mismo(n["autor"]["usuario"], usuario):
            raise SinPermiso("Solo quien escribió la nota puede borrarla.")
        storage.set(_clave(anio), [x for x in lista if x["id"] != nota_id])
    logger.info("Pizarrón: %s borró la nota %s", usuario, nota_id)


def marcar_hecha(nota_id: str, hecha: bool, usuario: str, nombre: str) -> dict:
    """Cualquiera del equipo puede tildar una tarea (o destildarla)."""
    with _LOCK:
        anio, lista, n = _ubicar(nota_id)
        if n["tipo"] != TAREA:
            raise ErrorPizarron("Solo las tareas se marcan como hechas.")
        n["hecha"] = {"usuario": usuario, "nombre": nombre or usuario, "cuando": ahora_iso()} if hecha else None
        storage.set(_clave(anio), lista)
    return n


def responder(nota_id: str, texto: str, usuario: str, nombre: str) -> dict:
    texto = (texto or "").strip()
    if not texto:
        raise ErrorPizarron("La respuesta no puede estar vacía.")
    if len(texto) > MAX_RESPUESTA:
        raise ErrorPizarron(f"La respuesta es muy larga (máximo {MAX_RESPUESTA} caracteres).")
    with _LOCK:
        anio, lista, n = _ubicar(nota_id)
        n.setdefault("respuestas", []).append({
            "id": secrets.token_hex(4), "texto": texto,
            "autor": {"usuario": usuario, "nombre": nombre or usuario}, "cuando": ahora_iso(),
        })
        storage.set(_clave(anio), lista)
    return n


def borrar_respuesta(nota_id: str, respuesta_id: str, usuario: str) -> dict:
    with _LOCK:
        anio, lista, n = _ubicar(nota_id)
        r = next((r for r in n.get("respuestas", []) if r["id"] == respuesta_id), None)
        if r is None:
            raise NoEncontrada("Respuesta no encontrada.")
        if not _mismo(r["autor"]["usuario"], usuario):
            raise SinPermiso("Solo quien escribió la respuesta puede borrarla.")
        n["respuestas"] = [x for x in n["respuestas"] if x["id"] != respuesta_id]
        storage.set(_clave(anio), lista)
    return n


# ── Armar la semana ─────────────────────────────────────────────────────────
def _para_ver(n: dict, usuario: str, nombre: str) -> dict:
    """La nota con lo que la pantalla necesita saber de quien mira."""
    para = (n.get("para") or "").strip()
    return {
        **n,
        "tipo_etiqueta": TIPOS.get(n["tipo"], n["tipo"]),
        "puede_editar": _mismo(n["autor"]["usuario"], usuario),
        "para_mi": bool(para) and (_mismo(para, usuario) or _mismo(para, nombre)),
    }


def semana(fecha_ref, usuario: str, nombre: str = "") -> dict:
    """Todo lo que se ve en el pizarrón la semana que contiene `fecha_ref`."""
    inicio = lunes(_fecha(fecha_ref or hoy(), "semana"))
    fin = inicio + timedelta(days=7)
    desde_utc, hasta_utc = _inicio_utc(inicio), _inicio_utc(fin)

    notas = _leer(inicio.year - 1) + _leer(inicio.year)
    fijadas, general, hechas = [], [], []
    por_dia: dict[str, list] = {}

    for n in notas:
        sem = date.fromisoformat(n["semana"])
        if sem > inicio:
            continue
        ver = _para_ver(n, usuario, nombre)
        if n["tipo"] == TAREA:
            tilde = _instante((n.get("hecha") or {}).get("cuando"))
            if tilde is not None and tilde < desde_utc:
                continue                                   # se hizo antes de esta semana
            if tilde is not None and tilde < hasta_utc:
                hechas.append(ver)                         # se tildó esta semana
                continue
            if sem < inicio:
                ver["viene_de"] = n["semana"]              # pendiente arrastrada
        elif sem < inicio and not n.get("fijada"):
            continue                                       # avisos y recordatorios: solo su semana

        if n.get("fijada"):
            fijadas.append(ver)
        elif sem == inicio and n.get("dia"):
            por_dia.setdefault(n["dia"], []).append(ver)
        else:
            general.append(ver)

    orden = lambda x: x.get("creada") or ""
    return {
        "semana": inicio.isoformat(),
        "hasta": (fin - timedelta(days=1)).isoformat(),
        "actual": lunes(hoy()).isoformat(),
        "hoy": hoy().isoformat(),
        "fijadas": sorted(fijadas, key=orden),
        # Primero lo que viene arrastrado (lo más viejo arriba), después lo de la semana.
        "general": sorted(general, key=lambda x: (x.get("viene_de") is None, x.get("viene_de") or "", orden(x))),
        "dias": [{"fecha": d, "items": sorted(por_dia[d], key=orden)} for d in sorted(por_dia)],
        "hechas": sorted(hechas, key=lambda x: x["hecha"]["cuando"]),
        "tipos": TIPOS,
        "colores": COLORES,
        "personas": personas(),
    }


def buscar(texto: str, usuario: str, nombre: str = "", anios: int = 3) -> list[dict]:
    """Notas (y respuestas) que contienen el texto, de los últimos años. Más nuevas primero."""
    q = (texto or "").strip().casefold()
    if not q:
        return []
    actual = hoy().year
    out = []
    for anio in range(actual, actual - anios, -1):
        for n in _leer(anio):
            en_respuestas = any(q in r["texto"].casefold() for r in n.get("respuestas", []))
            if q in n["texto"].casefold() or en_respuestas or q in (n.get("para") or "").casefold():
                out.append(_para_ver(n, usuario, nombre))
    return sorted(out, key=lambda x: x.get("creada") or "", reverse=True)[:200]


def personas() -> list[str]:
    """Nombres de quienes usan el pizarrón, para sugerir a quién va dirigida una nota."""
    return sorted({(v.get("nombre") or k) for k, v in (storage.get(CLAVE_VISTO) or {}).items()},
                  key=str.casefold)


# ── "Notas nuevas desde tu última visita" ───────────────────────────────────
def registrar_visita(usuario: str, nombre: str) -> None:
    with _LOCK:
        visto = storage.get(CLAVE_VISTO) or {}
        visto[usuario.strip().casefold()] = {"nombre": nombre or usuario, "cuando": ahora_iso()}
        storage.set(CLAVE_VISTO, visto)


def novedades(usuario: str) -> int:
    """Notas y respuestas de otras personas desde la última visita de `usuario`.

    Quien nunca entró ve las de la semana en curso, no las de todo el año.
    """
    visto = (storage.get(CLAVE_VISTO) or {}).get(usuario.strip().casefold())
    desde = _instante((visto or {}).get("cuando")) or _inicio_utc(lunes(hoy()))
    anio = hoy().year
    total = 0
    for n in _leer(anio - 1) + _leer(anio):
        if not _mismo(n["autor"]["usuario"], usuario) and (_instante(n.get("creada")) or desde) > desde:
            total += 1
        total += sum(1 for r in n.get("respuestas", [])
                     if not _mismo(r["autor"]["usuario"], usuario)
                     and (_instante(r.get("cuando")) or desde) > desde)
    return total
