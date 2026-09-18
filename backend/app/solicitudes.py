"""Solicitudes de espacio y fecha, y las reservas que salen de aprobarlas.

Cómo funciona, en criollo: una subcomisión pide un espacio para una actividad; una
bibliotecaria o alguien de comisión directiva lo mira y resuelve. Al resolver puede
**cambiar fecha, horario o espacio**, porque en la práctica casi nunca se aprueba
exactamente lo que se pidió; lo que se cambió queda a la vista del solicitante.

Estados:
    pendiente ──aprobar──> aprobada ──cancelar/reprogramar──> cancelada
        │  └──observar──> observaciones ──(el solicitante edita)──> pendiente
        └──rechazar──> rechazada

Una solicitud puede repetirse (semanal, quincenal, mensual). La repetición se expande
en `fechas`: cada ocurrencia concreta con su inicio y fin. Todo lo que mira el
calendario y los cruces trabaja sobre esa lista, no sobre la regla.

Lo aprobado se publica además en el Google Calendar de la biblioteca (ver
`calendario_google.py`). Acá solo se lleva la cuenta: `google_pendiente` dice que lo que
hay en Google ya no coincide con la solicitud y hay que sincronizar.

Se guarda en `storage` bajo la clave `solicitudes`.
"""
from __future__ import annotations

import logging
import secrets
import threading
from datetime import date, datetime, timedelta, timezone

from . import espacios, storage

logger = logging.getLogger("solicitudes")

CLAVE = "solicitudes"
_LOCK = threading.Lock()

PENDIENTE, APROBADA, OBSERVACIONES, RECHAZADA, CANCELADA = (
    "pendiente", "aprobada", "observaciones", "rechazada", "cancelada")
ESTADOS = (PENDIENTE, APROBADA, OBSERVACIONES, RECHAZADA, CANCELADA)

ETIQUETAS = {
    PENDIENTE: "Pendiente",
    APROBADA: "Aprobada",
    OBSERVACIONES: "Con observaciones",
    RECHAZADA: "Rechazada",
    CANCELADA: "Cancelada",
}

UNICA, SEMANAL, QUINCENAL, MENSUAL = "unica", "semanal", "quincenal", "mensual"
REPETICIONES = {
    UNICA: "Una sola vez",
    SEMANAL: "Todas las semanas",
    QUINCENAL: "Cada quince días",
    MENSUAL: "Una vez por mes",
}

# Tope de ocurrencias de una repetición: evita que un "hasta" mal puesto genere miles.
MAX_OCURRENCIAS = 200

# Estados en los que la reserva ocupa el espacio de verdad.
_OCUPAN = (APROBADA,)


class ErrorSolicitud(ValueError):
    """Dato o transición inválida (se traduce a HTTP 400)."""


# ── Fechas ──────────────────────────────────────────────────────────────────
def _dt(valor: str, campo: str) -> datetime:
    """Interpreta 'YYYY-MM-DDTHH:MM' (hora local de la biblioteca, sin zona)."""
    try:
        return datetime.fromisoformat(str(valor).strip().replace(" ", "T")[:16])
    except (TypeError, ValueError) as exc:
        raise ErrorSolicitud(f"La {campo} no tiene un formato válido.") from exc


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="minutes")


def _mismo_dia_del_mes(base: datetime, meses: int) -> datetime:
    """Suma meses conservando el día; si ese día no existe, usa el último del mes."""
    mes = base.month - 1 + meses
    anio, mes = base.year + mes // 12, mes % 12 + 1
    dia = min(base.day, [31, 29 if (anio % 4 == 0 and (anio % 100 or anio % 400 == 0)) else 28,
                         31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mes - 1])
    return base.replace(year=anio, month=mes, day=dia)


def ocurrencias(inicio: str, fin: str, repeticion: str = UNICA, hasta: str | None = None) -> list[dict]:
    """Expande la repetición en fechas concretas: [{'inicio','fin'}, …].

    `hasta` es una fecha (YYYY-MM-DD) inclusive. Sin repetición, devuelve una sola.
    """
    d1, d2 = _dt(inicio, "fecha de inicio"), _dt(fin, "fecha de fin")
    if d2 <= d1:
        raise ErrorSolicitud("La hora de fin tiene que ser posterior a la de inicio.")
    if repeticion not in REPETICIONES:
        raise ErrorSolicitud(f"Repetición desconocida: '{repeticion}'.")
    if repeticion == UNICA:
        return [{"inicio": _iso(d1), "fin": _iso(d2)}]

    if not hasta:
        raise ErrorSolicitud("Si la actividad se repite, hay que decir hasta cuándo.")
    try:
        tope = date.fromisoformat(str(hasta).strip()[:10])
    except ValueError as exc:
        raise ErrorSolicitud("El 'hasta cuándo' no tiene un formato válido.") from exc
    if tope < d1.date():
        raise ErrorSolicitud("El 'hasta cuándo' no puede ser anterior a la primera fecha.")

    duracion = d2 - d1
    salida: list[dict] = []
    i = 0
    while len(salida) < MAX_OCURRENCIAS:
        if repeticion == SEMANAL:
            act = d1 + timedelta(weeks=i)
        elif repeticion == QUINCENAL:
            act = d1 + timedelta(weeks=2 * i)
        else:
            act = _mismo_dia_del_mes(d1, i)
        if act.date() > tope:
            break
        salida.append({"inicio": _iso(act), "fin": _iso(act + duracion)})
        i += 1
    return salida


def _se_pisan(a: dict, b: dict) -> bool:
    """Dos franjas se superponen si cada una empieza antes de que termine la otra.

    Pegadas no es pisarse: si una termina 18:00 y la otra empieza 18:00, conviven.
    """
    return a["inicio"] < b["fin"] and b["inicio"] < a["fin"]


# ── Almacenamiento ──────────────────────────────────────────────────────────
def _leer() -> list[dict]:
    datos = storage.get(CLAVE)
    return datos if isinstance(datos, list) else []


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _anotar(s: dict, quien: str, que: str) -> None:
    s.setdefault("historial", []).append({"cuando": _ahora(), "quien": quien, "que": que})
    s["actualizado"] = _ahora()


def _para_google(s: dict) -> None:
    """Marca si hay que tocar Google: publicar lo aprobado o sacar lo que dejó de estarlo."""
    s["google_pendiente"] = s.get("estado") == APROBADA or bool(s.get("google_eventos"))


# ── Conflictos ──────────────────────────────────────────────────────────────
def conflictos(espacio_id: str, fechas: list[dict], excluir_id: str | None = None) -> list[dict]:
    """Reservas ya aprobadas en ese espacio que se pisan con las fechas pedidas.

    No bloquea: informa. Quien resuelve decide, porque a veces dos cosas en la misma
    sala se pueden acomodar y el sistema no tiene cómo saberlo.
    """
    choques: list[dict] = []
    for otra in _leer():
        if otra.get("id") == excluir_id or otra.get("estado") not in _OCUPAN:
            continue
        if otra.get("espacio_id") != espacio_id:
            continue
        for f in fechas:
            for g in otra.get("fechas", []):
                if _se_pisan(f, g):
                    choques.append({
                        "solicitud_id": otra["id"],
                        "titulo": otra.get("titulo", ""),
                        "solicitante": (otra.get("solicitante") or {}).get("nombre", ""),
                        "inicio": g["inicio"], "fin": g["fin"],
                        "pedida_inicio": f["inicio"], "pedida_fin": f["fin"],
                    })
    return sorted(choques, key=lambda c: c["inicio"])


# ── Alta y edición ──────────────────────────────────────────────────────────
_CAMPOS = ("titulo", "descripcion", "espacio_id", "inicio", "fin", "repeticion",
           "hasta", "personas", "necesidades", "abierta_publico", "arancelada")


def _normalizar(datos: dict) -> dict:
    """Valida y deja los campos listos para guardar. Levanta ErrorSolicitud si algo falta."""
    d = {k: (datos or {}).get(k) for k in _CAMPOS}
    d["titulo"] = (d["titulo"] or "").strip()
    if not d["titulo"]:
        raise ErrorSolicitud("Falta decir qué actividad es.")
    d["descripcion"] = (d["descripcion"] or "").strip()

    espacios.exigir(d["espacio_id"] or "")          # el espacio tiene que existir y estar activo

    d["repeticion"] = (d["repeticion"] or UNICA).strip() or UNICA
    d["hasta"] = (d["hasta"] or "").strip() or None
    d["fechas"] = ocurrencias(d["inicio"], d["fin"], d["repeticion"], d["hasta"])
    d["inicio"], d["fin"] = d["fechas"][0]["inicio"], d["fechas"][0]["fin"]

    try:
        d["personas"] = max(int(d["personas"] or 0), 0)
    except (TypeError, ValueError):
        raise ErrorSolicitud("La cantidad de personas tiene que ser un número.") from None
    d["necesidades"] = [str(n).strip() for n in (d["necesidades"] or []) if str(n).strip()]
    d["abierta_publico"] = bool(d["abierta_publico"])
    d["arancelada"] = bool(d["arancelada"])
    return d


def crear(datos: dict, solicitante: dict) -> dict:
    """Crea una solicitud en estado pendiente. `solicitante`: uid, usuario, nombre, subcomision."""
    d = _normalizar(datos)
    s = {
        "id": secrets.token_hex(8),
        **d,
        "solicitante": {
            "uid": solicitante.get("uid"),
            "usuario": solicitante.get("usuario", ""),
            "nombre": solicitante.get("nombre", ""),
            "subcomision": solicitante.get("subcomision", ""),
        },
        "estado": PENDIENTE,
        "pedido_original": None,
        "resolucion": None,
        "google_eventos": 0,           # cuántos eventos puede haber en Google (ver calendario_google)
        "google_pendiente": False,
        "google_error": "",
        "google_publicada": None,
        "avisos": [],                  # mails que se le mandaron a quien pidió
        "historial": [],
        "creado": _ahora(),
        "actualizado": _ahora(),
    }
    _anotar(s, solicitante.get("usuario", ""), "Creó la solicitud")
    with _LOCK:
        lista = _leer()
        lista.append(s)
        storage.set(CLAVE, lista)
    logger.info("Solicitud creada: %s (%s)", s["titulo"], s["id"])
    return s


def _buscar(lista: list[dict], sid: str) -> dict:
    s = next((x for x in lista if x.get("id") == sid), None)
    if s is None:
        raise ErrorSolicitud("Esa solicitud no existe.")
    return s


def editar(sid: str, datos: dict, por: str) -> dict:
    """El solicitante corrige su pedido. Solo mientras no esté resuelto."""
    d = _normalizar(datos)
    with _LOCK:
        lista = _leer()
        s = _buscar(lista, sid)
        if s["estado"] not in (PENDIENTE, OBSERVACIONES):
            raise ErrorSolicitud(
                f"Una solicitud {ETIQUETAS[s['estado']].lower()} ya no se puede editar.")
        s.update(d)
        s["estado"] = PENDIENTE          # vuelve a la cola de resolución
        _para_google(s)
        _anotar(s, por, "Editó el pedido")
        storage.set(CLAVE, lista)
    return s


def resolver(sid: str, decision: str, por: str, motivo: str = "",
             cambios: dict | None = None) -> dict:
    """Aprueba, rechaza o devuelve con observaciones.

    `cambios` permite ajustar espacio/fecha/horario **al aprobar**: es el caso normal,
    no la excepción. Si hubo cambios, se guarda una foto de lo que se había pedido
    para que el solicitante vea qué se le movió.
    """
    if decision not in ("aprobar", "rechazar", "observar"):
        raise ErrorSolicitud(f"Decisión desconocida: '{decision}'.")
    if decision in ("rechazar", "observar") and not (motivo or "").strip():
        raise ErrorSolicitud("Para rechazar o pedir cambios hay que escribir el motivo.")

    with _LOCK:
        lista = _leer()
        s = _buscar(lista, sid)
        if s["estado"] in (RECHAZADA, CANCELADA):
            raise ErrorSolicitud(
                f"Esta solicitud ya está {ETIQUETAS[s['estado']].lower()}.")

        if decision == "aprobar":
            if cambios:
                antes = {k: s.get(k) for k in ("espacio_id", "inicio", "fin", "repeticion",
                                               "hasta", "fechas")}
                nuevos = _normalizar({**{k: s.get(k) for k in _CAMPOS}, **cambios})
                movido = [k for k in ("espacio_id", "inicio", "fin", "repeticion", "hasta")
                          if antes.get(k) != nuevos.get(k)]
                if movido:
                    s["pedido_original"] = s.get("pedido_original") or antes
                    s.update({k: nuevos[k] for k in (*_CAMPOS, "fechas")})
                    _anotar(s, por, "Ajustó el pedido al aprobar: " + ", ".join(movido))
            s["estado"] = APROBADA
            _anotar(s, por, "Aprobó la solicitud")
        elif decision == "rechazar":
            s["estado"] = RECHAZADA
            _anotar(s, por, f"Rechazó la solicitud: {motivo.strip()}")
        else:
            s["estado"] = OBSERVACIONES
            _anotar(s, por, f"Pidió cambios: {motivo.strip()}")

        s["resolucion"] = {"por": por, "cuando": _ahora(),
                           "decision": decision, "motivo": (motivo or "").strip()}
        _para_google(s)
        storage.set(CLAVE, lista)
    logger.info("Solicitud %s: %s por %s", sid, decision, por)
    return s


def cancelar(sid: str, por: str, motivo: str = "") -> dict:
    """Da de baja la reserva. Sale del calendario (y de Google, si estaba publicada)."""
    with _LOCK:
        lista = _leer()
        s = _buscar(lista, sid)
        if s["estado"] == CANCELADA:
            raise ErrorSolicitud("Esta solicitud ya estaba cancelada.")
        s["estado"] = CANCELADA
        s["cancelacion"] = {"por": por, "cuando": _ahora(), "motivo": (motivo or "").strip()}
        _para_google(s)                  # si estaba publicada, hay que borrarla allá
        _anotar(s, por, "Canceló la reserva" + (f": {motivo.strip()}" if motivo else ""))
        storage.set(CLAVE, lista)
    return s


# ── Lo que pasa afuera: Google Calendar y avisos por mail ──────────────────
def huella(s: dict) -> tuple:
    """Lo que se publica en Google. Si cambia mientras se sincroniza, hay que volver."""
    return (s.get("estado"), s.get("titulo"), s.get("descripcion"), s.get("espacio_id"),
            tuple((f["inicio"], f["fin"]) for f in s.get("fechas", [])))


def reservar_eventos_google(sid: str, cantidad: int) -> None:
    """Antes de crear eventos en Google, anota que puede llegar a haber `cantidad`.

    Si la publicación se corta a la mitad, así se sabe hasta dónde hay que limpiar.
    """
    with _LOCK:
        lista = _leer()
        s = _buscar(lista, sid)
        s["google_eventos"] = max(int(s.get("google_eventos") or 0), cantidad)
        storage.set(CLAVE, lista)


def marcar_google(sid: str, huella_publicada: tuple, *, eventos: int | None = None,
                  error: str = "") -> None:
    """Anota cómo salió la sincronización con Google.

    Si la solicitud cambió mientras se publicaba, queda pendiente: lo que está en
    Google es lo de antes y la próxima pasada lo corrige.
    """
    with _LOCK:
        lista = _leer()
        s = next((x for x in lista if x.get("id") == sid), None)
        if s is None:
            return
        if error:
            s["google_error"] = error
        else:
            s["google_eventos"] = eventos or 0
            s["google_error"] = ""
            s["google_publicada"] = _ahora() if eventos else None
            s["google_pendiente"] = huella(s) != huella_publicada
        storage.set(CLAVE, lista)


def pendientes_google() -> list[str]:
    return [s["id"] for s in _leer() if s.get("google_pendiente")]


def anotar_aviso(sid: str, aviso: dict) -> None:
    """Guarda que se le mandó (o no se pudo mandar) un mail a quien pidió."""
    with _LOCK:
        lista = _leer()
        s = next((x for x in lista if x.get("id") == sid), None)
        if s is None:
            return
        s.setdefault("avisos", []).append({"cuando": _ahora(), **aviso})
        storage.set(CLAVE, lista)


# ── Consultas ───────────────────────────────────────────────────────────────
def obtener(sid: str) -> dict | None:
    return next((s for s in _leer() if s.get("id") == sid), None)


def listar(*, estado: str | None = None, solicitante_uid: str | None = None,
           subcomision: str | None = None) -> list[dict]:
    """Solicitudes ordenadas: primero las que esperan respuesta, después por fecha."""
    salida = _leer()
    if estado:
        salida = [s for s in salida if s.get("estado") == estado]
    if solicitante_uid:
        salida = [s for s in salida if (s.get("solicitante") or {}).get("uid") == solicitante_uid]
    if subcomision:
        salida = [s for s in salida
                  if (s.get("solicitante") or {}).get("subcomision") == subcomision]
    orden = {PENDIENTE: 0, OBSERVACIONES: 1, APROBADA: 2, RECHAZADA: 3, CANCELADA: 4}
    return sorted(salida, key=lambda s: (orden.get(s.get("estado"), 9), s.get("inicio") or ""))


def pendientes() -> int:
    """Cuántas esperan respuesta (para el contadorcito del menú)."""
    return sum(1 for s in _leer() if s.get("estado") in (PENDIENTE, OBSERVACIONES))


def con_espacio(s: dict) -> dict:
    """La solicitud con el nombre del espacio y la etiqueta del estado ya resueltos."""
    return {**s,
            "espacio": espacios.nombre_de(s.get("espacio_id", "")),
            "estado_etiqueta": ETIQUETAS.get(s.get("estado"), s.get("estado")),
            "repeticion_etiqueta": REPETICIONES.get(s.get("repeticion"), s.get("repeticion"))}


# ── Para el calendario ──────────────────────────────────────────────────────
COLOR_RESERVA = "#13235B"


def eventos(d1: date, d2: date) -> list[dict]:
    """Reservas aprobadas entre dos fechas, con la misma forma que los de Google.

    Así el calendario las dibuja mezcladas con los eventos de los Google Calendar
    sin tener que distinguir de dónde salió cada una.
    """
    desde, hasta = d1.isoformat(), d2.isoformat() + "T23:59"
    salida: list[dict] = []
    for s in _leer():
        if s.get("estado") not in _OCUPAN:
            continue
        for f in s.get("fechas", []):
            if f["fin"] < desde or f["inicio"] > hasta:
                continue
            quien = (s.get("solicitante") or {}).get("subcomision") \
                or (s.get("solicitante") or {}).get("nombre", "")
            salida.append({
                "titulo": s.get("titulo", ""),
                "lugar": espacios.nombre_de(s.get("espacio_id", "")),
                "descripcion": s.get("descripcion", ""),
                "inicio": f["inicio"],
                "fin": f["fin"],
                "todo_el_dia": False,
                "calendario": "Reservas de espacio",
                "color": COLOR_RESERVA,
                "solicitud_id": s["id"],
                "responsable": quien,
            })
    salida.sort(key=lambda e: e["inicio"])
    return salida
