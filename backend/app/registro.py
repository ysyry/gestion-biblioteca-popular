"""Registro de actividades realizadas: qué pasó, cuánta gente vino y cómo salió.

Lo carga **quien estuvo a cargo**, desde un link público, sin usuario (ver
`docs/CASOS-DE-USO.md`, módulo E). Hay dos formularios:

  · **actividad** — una actividad única (charla, taller abierto, muestra, visita…).
  · **taller**    — el **resumen mensual** de un taller regular (decisión tomada: los
    talleres no se registran encuentro por encuentro).

Y tres clases de link:
  · **general**   — uno fijo para cualquier actividad única; el formulario viene vacío.
  · **actividad** — para una actividad puntual; viene precargado y queda atado a lo
    que se había planificado (una reserva aprobada o un evento del calendario).
  · **taller**    — uno fijo por taller; sirve todos los meses.

Todo lo que entra queda **"recibido"** hasta que una bibliotecaria lo revisa: solo lo
**validado** cuenta después en las estadísticas. Nada de esto guarda datos personales
del público: la asistencia va en números.

Si la actividad o el taller es de una **subcomisión**, el registro lo dice (`subcomision`,
con el mismo nombre que tienen sus usuarios): así cada subcomisión ve lo suyo.

Guardado: `storage`. `registro_links` para los links y una clave por año para los
registros (`registros_actividad_2026`), porque esto crece para siempre.
"""
from __future__ import annotations

import logging
import re
import secrets
import threading
import unicodedata
from datetime import date, datetime, timezone

from . import espacios, storage, usuarios

logger = logging.getLogger("registro")

_LOCK = threading.Lock()
CLAVE_LINKS = "registro_links"

# ── Vocabulario (lo que se puede sumar y comparar después) ──────────────────
# Renombrar una etiqueta no parte el histórico: la clave es lo que se guarda.
TIPOS = {
    "encuentro_taller": "Encuentro de taller suelto",
    "seminario": "Seminario",
    "charla": "Charla / conversatorio",
    "presentacion": "Presentación de libro",
    "club_lectura": "Club de lectura",
    "proyeccion": "Proyección / cineclub",
    "musica": "Música / espectáculo",
    "muestra": "Muestra / exposición",
    "visita": "Visita escolar o institucional",
    "feria": "Feria / jornada",
    "reunion": "Reunión / asamblea",
    "otra": "Otra",
}
TEMATICAS = {
    "literatura": "Literatura", "infancias": "Infancias", "memoria": "Memoria y DDHH",
    "generos": "Géneros", "ambiente": "Ambiente / huerta", "musica": "Música",
    "artes_visuales": "Artes visuales", "teatro": "Teatro",
    "ciencia": "Ciencia y tecnología", "oficios": "Oficios", "salud": "Salud",
    "comunidad": "Barrio y comunidad", "otra": "Otra",
}
FRANJAS = {
    "0_5": "0 a 5", "6_12": "6 a 12", "13_17": "13 a 17",
    "18_29": "18 a 29", "30_59": "30 a 59", "60": "60 o más",
}
MODALIDADES = {"presencial": "Presencial", "virtual": "Virtual", "hibrida": "Híbrida"}
DIFUSION = {
    "redes": "Redes", "whatsapp": "WhatsApp", "cartel": "Cartel", "boca": "Boca en boca",
    "escuela": "Escuela o institución", "ya_venian": "Ya venían", "no_se": "No sé",
}
ACCESOS = {"gratuita": "Gratuita", "gorra": "A la gorra", "arancelada": "Arancelada"}
ORGANIZA = {"biblioteca": "La biblioteca", "subcomision": "Una subcomisión",
            "institucion": "Junto con otra institución"}
CONTINUA = {"si": "Sí", "no": "No", "a_definir": "A definir"}
SUSPENSION = {"feriado": "Feriado", "clima": "Clima", "ausencia": "Ausencia del tallerista",
              "otro": "Otro"}

CLASES = ("actividad", "taller")
CLASES_LINK = ("general", "actividad", "taller")
RECIBIDO, VALIDADO, DESCARTADO = "recibido", "validado", "descartado"
# Versión de la forma de los datos. Si algún día cambia un campo, los registros viejos
# se reconocen por este número y se pueden convertir sin adivinar.
ESQUEMA = 1
ESTADOS = {RECIBIDO: "Recibido", VALIDADO: "Validado", DESCARTADO: "Descartado"}

MAX_TEXTO = 2000
MAX_PERSONAS = 100000


class ErrorRegistro(ValueError):
    """Error de validación, con un mensaje que se muestra tal cual."""


class NoEncontrado(ErrorRegistro):
    pass


class LinkCerrado(ErrorRegistro):
    pass


# ── Utilidades ──────────────────────────────────────────────────────────────
def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _txt(v, largo: int = MAX_TEXTO) -> str:
    return str(v or "").strip()[:largo]


def _entero(v, campo: str, minimo: int = 0, maximo: int = MAX_PERSONAS) -> int | None:
    if v in (None, "", []):
        return None
    try:
        n = int(float(str(v).replace(",", ".")))
    except (TypeError, ValueError) as exc:
        raise ErrorRegistro(f"'{campo}' tiene que ser un número.") from exc
    if not minimo <= n <= maximo:
        raise ErrorRegistro(f"'{campo}' está fuera de rango.")
    return n


def _fecha(v, campo: str) -> str:
    try:
        return date.fromisoformat(str(v)[:10]).isoformat()
    except ValueError as exc:
        raise ErrorRegistro(f"'{campo}' tiene que ser una fecha (AAAA-MM-DD).") from exc


def _mes(v, campo: str = "mes") -> str:
    m = re.fullmatch(r"(\d{4})-(\d{2})", str(v or "").strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ErrorRegistro(f"'{campo}' tiene que ser un mes (AAAA-MM).")
    return str(v).strip()


def _hora(v, campo: str) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", s):
        raise ErrorRegistro(f"'{campo}' tiene que ser una hora (HH:MM).")
    return s if len(s) == 5 else "0" + s


def _claves(valores, catalogo: dict, campo: str) -> list[str]:
    out = []
    for v in valores or []:
        k = str(v).strip()
        if k not in catalogo:
            raise ErrorRegistro(f"Opción desconocida en '{campo}': '{k}'.")
        if k not in out:
            out.append(k)
    return out


def _clave_de(valor, catalogo: dict, campo: str, obligatorio: bool = False) -> str:
    k = str(valor or "").strip()
    if not k:
        if obligatorio:
            raise ErrorRegistro(f"Falta '{campo}'.")
        return ""
    if k not in catalogo:
        raise ErrorRegistro(f"Opción desconocida en '{campo}': '{k}'.")
    return k


def normalizar(texto: str) -> str:
    s = unicodedata.normalize("NFD", (texto or "").lower())
    return " ".join("".join(c for c in s if unicodedata.category(c) != "Mn").split())


def catalogos() -> dict:
    """Todo el vocabulario junto: lo usan el formulario público y la bandeja."""
    return {"tipos": TIPOS, "tematicas": TEMATICAS, "franjas": FRANJAS,
            "modalidades": MODALIDADES, "difusion": DIFUSION, "accesos": ACCESOS,
            "organiza": ORGANIZA, "continua": CONTINUA, "suspension": SUSPENSION,
            "espacios": [{"id": e["id"], "nombre": e["nombre"]} for e in espacios.listar()],
            "subcomisiones": usuarios.subcomisiones()}


def subcomision_de(valor) -> str:
    """El nombre de la subcomisión tal como la conoce la app ("prensa " → "Prensa").

    Si no coincide con ninguna conocida se guarda igual, como vino: puede ser una que
    todavía no tiene usuarios, y la biblioteca la corrige al validar.
    """
    texto = _txt(valor, 120)
    clave = normalizar(texto)
    return next((c for c in usuarios.subcomisiones() if normalizar(c) == clave), texto)


def es_de(reg: dict, subcomision: str) -> bool:
    return bool(subcomision) and normalizar(reg["datos"].get("subcomision", "")) == normalizar(subcomision)


# ── Links ───────────────────────────────────────────────────────────────────
def _leer_links() -> list[dict]:
    return storage.get(CLAVE_LINKS) or []


def links(incluir_cerrados: bool = True) -> list[dict]:
    out = [dict(l, vencido=_vencido(l)) for l in _leer_links()]
    if not incluir_cerrados:
        out = [l for l in out if l["abierto"] and not l["vencido"]]
    return sorted(out, key=lambda l: l.get("creado", ""), reverse=True)


def _vencido(link: dict) -> bool:
    return bool(link.get("vence")) and link["vence"] < date.today().isoformat()


def crear_link(datos: dict, por: str) -> dict:
    clase = _clave_de(datos.get("clase"), dict.fromkeys(CLASES_LINK), "clase", obligatorio=True)
    titulo = _txt(datos.get("titulo"), 200)
    if clase != "general" and not titulo:
        raise ErrorRegistro("Poné un nombre para saber de qué actividad o taller es el link.")

    # El link general sirve para cualquier actividad: no precarga nada. Su nombre es
    # para que la biblioteca lo reconozca en la lista, no el de una actividad.
    precarga = {} if clase == "general" else dict(datos.get("precarga") or {})
    if clase == "actividad" and precarga.get("fecha"):
        precarga["fecha"] = _fecha(precarga["fecha"], "fecha")
    for campo in ("hora_inicio", "hora_fin"):
        if precarga.get(campo):
            precarga[campo] = _hora(precarga[campo], campo)
    if precarga.get("tipo"):
        precarga["tipo"] = _clave_de(precarga["tipo"], TIPOS, "tipo")
    if precarga.get("tematicas"):
        precarga["tematicas"] = _claves(precarga["tematicas"], TEMATICAS, "tematicas")
    if precarga.get("subcomision"):
        precarga["subcomision"] = subcomision_de(precarga["subcomision"])
        if clase == "actividad":
            precarga["organiza"] = "subcomision"

    link = {
        "id": secrets.token_hex(4),
        "token": secrets.token_urlsafe(24),
        "clase": clase,
        "titulo": titulo or "Registro de actividades",
        "precarga": precarga,
        "origen": datos.get("origen") or None,         # {tipo: "solicitud"|"evento", id}
        "abierto": True,
        "vence": _fecha(datos["vence"], "vence") if datos.get("vence") else None,
        "creado": _ahora(),
        "creado_por": por,
        "usos": 0,
        "ultimo_uso": None,
    }
    with _LOCK:
        lista = _leer_links()
        lista.append(link)
        storage.set(CLAVE_LINKS, lista)
    logger.info("Registro: link %s (%s) creado por %s", link["id"], clase, por)
    return link


def actualizar_link(link_id: str, cambios: dict) -> dict:
    with _LOCK:
        lista = _leer_links()
        link = next((l for l in lista if l["id"] == link_id), None)
        if link is None:
            raise NoEncontrado("Link no encontrado.")
        if "abierto" in cambios:
            link["abierto"] = bool(cambios["abierto"])
        if "titulo" in cambios:
            link["titulo"] = _txt(cambios["titulo"], 200) or link["titulo"]
        if "vence" in cambios:
            link["vence"] = _fecha(cambios["vence"], "vence") if cambios["vence"] else None
        if "precarga" in cambios:
            link["precarga"] = dict(cambios["precarga"] or {})
        storage.set(CLAVE_LINKS, lista)
    return link


def borrar_link(link_id: str) -> None:
    with _LOCK:
        lista = _leer_links()
        if not any(l["id"] == link_id for l in lista):
            raise NoEncontrado("Link no encontrado.")
        storage.set(CLAVE_LINKS, [l for l in lista if l["id"] != link_id])


def por_token(token: str) -> dict:
    """El link de ese código, si se puede usar. Es lo primero que ve la página pública."""
    link = next((l for l in _leer_links() if secrets.compare_digest(l["token"], str(token or ""))), None)
    if link is None:
        raise NoEncontrado("Este link no existe o fue dado de baja.")
    if not link["abierto"]:
        raise LinkCerrado("Este link está cerrado. Pedile uno nuevo a la biblioteca.")
    if _vencido(link):
        raise LinkCerrado("Este link venció. Pedile uno nuevo a la biblioteca.")
    return link


def _marcar_uso(link_id: str) -> None:
    with _LOCK:
        lista = _leer_links()
        for l in lista:
            if l["id"] == link_id:
                l["usos"] = int(l.get("usos") or 0) + 1
                l["ultimo_uso"] = _ahora()
        storage.set(CLAVE_LINKS, lista)


# ── Registros ───────────────────────────────────────────────────────────────
def _clave_anio(anio: int) -> str:
    return f"registros_actividad_{anio}"


def _anio_de(reg: dict) -> int:
    d = reg["datos"]
    base = d.get("fecha") or (d.get("mes") and d["mes"] + "-01") or reg["creado"][:10]
    return int(str(base)[:4])


def _leer(anio: int) -> list[dict]:
    return storage.get(_clave_anio(anio)) or []


def _guardar(anio: int, lista: list[dict]) -> None:
    storage.set(_clave_anio(anio), lista)


def _ubicar(reg_id: str) -> tuple[int, list[dict], dict]:
    """(año de la clave, lista de ese año, registro).

    El id arranca con el año que tenía la actividad al cargarse, pero si después se
    corrige la fecha el registro se muda de clave (para que los listados por año den
    bien). Por eso se busca también en los años de al lado.
    """
    anio_txt, _, _ = str(reg_id).partition("-")
    if not anio_txt.isdigit():
        raise NoEncontrado("Registro no encontrado.")
    base = int(anio_txt)
    for anio in (base, base - 1, base + 1, base - 2, base + 2):
        lista = _leer(anio)
        for r in lista:
            if r["id"] == reg_id:
                return anio, lista, r
    raise NoEncontrado("Registro no encontrado.")


def _quien_completa(datos: dict) -> dict:
    nombre = _txt((datos.get("quien_completa") or {}).get("nombre"), 120)
    if not nombre:
        raise ErrorRegistro("Falta tu nombre (quién completa el formulario).")
    return {"nombre": nombre,
            "contacto": _txt((datos.get("quien_completa") or {}).get("contacto"), 120)}


def _franjas(datos: dict) -> dict:
    crudo = datos.get("franjas") or {}
    out = {}
    for k in FRANJAS:
        n = _entero(crudo.get(k), f"franja {FRANJAS[k]}")
        if n:
            out[k] = n
    return out


def _incidente(datos: dict) -> dict:
    """El incidente llega como texto + tilde desde el formulario, o ya armado si se
    está corrigiendo un registro guardado. Acepta las dos formas."""
    crudo = datos.get("incidente")
    if isinstance(crudo, dict):
        texto = _txt(crudo.get("texto"))
        atencion = bool(datos.get("incidente_atencion", crudo.get("requiere_atencion")))
    else:
        texto = _txt(crudo)
        atencion = bool(datos.get("incidente_atencion"))
    return {"texto": texto, "requiere_atencion": atencion and bool(texto)}


def limpiar_actividad(datos: dict) -> dict:
    """Valida y ordena lo que llega del formulario de una actividad única."""
    titulo = _txt(datos.get("titulo"), 200)
    if not titulo:
        raise ErrorRegistro("Falta el nombre de la actividad.")
    fecha = _fecha(datos.get("fecha"), "fecha")
    inicio, fin = _hora(datos.get("hora_inicio"), "hora de inicio"), _hora(datos.get("hora_fin"), "hora de fin")
    if not inicio:
        raise ErrorRegistro("Falta la hora de inicio.")
    if fin and fin < inicio:
        raise ErrorRegistro("La hora de fin no puede ser anterior a la de inicio.")

    franjas = _franjas(datos)
    total = _entero(datos.get("personas_total"), "cantidad de personas")
    if total is None:
        total = sum(franjas.values()) if franjas else None
    if total is None:
        raise ErrorRegistro("Falta la cantidad de personas que vinieron.")

    espacio_id = _txt(datos.get("espacio_id"), 40)
    if espacio_id and not espacios.obtener(espacio_id):
        espacio_id = ""
    # La subcomisión cuenta solo si la organizó una: si se corrige a "la biblioteca", se va.
    subcomision = subcomision_de(datos.get("subcomision"))
    organiza = _clave_de(datos.get("organiza"), ORGANIZA, "organiza") or \
        ("subcomision" if subcomision else "biblioteca")
    if organiza != "subcomision":
        subcomision = ""
    return {
        "titulo": titulo,
        "tipo": _clave_de(datos.get("tipo"), TIPOS, "tipo", obligatorio=True),
        "fecha": fecha,
        "hora_inicio": inicio,
        "hora_fin": fin,
        "espacio_id": espacio_id,
        "espacio_otro": _txt(datos.get("espacio_otro"), 120),
        "modalidad": _clave_de(datos.get("modalidad"), MODALIDADES, "modalidad") or "presencial",
        "tematicas": _claves(datos.get("tematicas"), TEMATICAS, "temática")[:3],
        "a_cargo": _txt(datos.get("a_cargo"), 300),
        "organiza": organiza,
        "organiza_detalle": _txt(datos.get("organiza_detalle"), 160),
        "subcomision": subcomision,
        "personas_total": total,
        "personas_aprox": bool(datos.get("personas_aprox")),
        "franjas": franjas,
        "primera_vez": _entero(datos.get("primera_vez"), "personas por primera vez"),
        "difusion": _claves(datos.get("difusion"), DIFUSION, "difusión"),
        "acceso": _clave_de(datos.get("acceso"), ACCESOS, "acceso") or "gratuita",
        "monto": _txt(datos.get("monto"), 60),
        "recaudacion": _txt(datos.get("recaudacion"), 60),
        "descripcion": _txt(datos.get("descripcion")),
        "valoracion": _entero(datos.get("valoracion"), "valoración", 1, 5),
        "funciono": _txt(datos.get("funciono")),
        "mejorar": _txt(datos.get("mejorar")),
        "incidente": _incidente(datos),
        "continua": _clave_de(datos.get("continua"), CONTINUA, "continúa"),
        "quien_completa": _quien_completa(datos),
    }


def limpiar_taller(datos: dict) -> dict:
    """Valida y ordena el resumen mensual de un taller."""
    titulo = _txt(datos.get("titulo"), 200)
    if not titulo:
        raise ErrorRegistro("Falta el nombre del taller.")
    encuentros = _entero(datos.get("encuentros"), "encuentros realizados", 0, 60)
    if encuentros is None:
        raise ErrorRegistro("Falta cuántos encuentros se hicieron en el mes.")
    participantes = _entero(datos.get("participantes"), "participantes del mes")
    if participantes is None:
        raise ErrorRegistro("Falta cuántas personas participaron en el mes.")

    espacio_id = _txt(datos.get("espacio_id"), 40)
    if espacio_id and not espacios.obtener(espacio_id):
        espacio_id = ""
    return {
        "titulo": titulo,
        "mes": _mes(datos.get("mes")),
        "tallerista": _txt(datos.get("tallerista"), 200),
        "subcomision": subcomision_de(datos.get("subcomision")),
        "espacio_id": espacio_id,
        "espacio_otro": _txt(datos.get("espacio_otro"), 120),
        "tematicas": _claves(datos.get("tematicas"), TEMATICAS, "temática")[:3],
        "encuentros": encuentros,
        "suspendidos": _entero(datos.get("suspendidos"), "encuentros suspendidos", 0, 60) or 0,
        "motivo_suspension": _clave_de(datos.get("motivo_suspension"), SUSPENSION, "motivo"),
        "participantes": participantes,
        "asistencia_promedio": _entero(datos.get("asistencia_promedio"), "asistencia promedio"),
        "franjas": _franjas(datos),
        "altas": _entero(datos.get("altas"), "altas del mes"),
        "bajas": _entero(datos.get("bajas"), "bajas del mes"),
        "trabajado": _txt(datos.get("trabajado")),
        "valoracion": _entero(datos.get("valoracion"), "valoración", 1, 5),
        "mejorar": _txt(datos.get("mejorar")),
        "incidente": _incidente(datos),
        "quien_completa": _quien_completa(datos),
    }


def limpiar(clase: str, datos: dict) -> dict:
    if clase == "taller":
        return limpiar_taller(datos)
    if clase == "actividad":
        return limpiar_actividad(datos)
    raise ErrorRegistro(f"Clase de registro desconocida: '{clase}'.")


def _parecidos(reg: dict) -> list[str]:
    """Ids de registros que parecen el mismo: misma fecha (o mes) y nombre parecido."""
    d, clase = reg["datos"], reg["clase"]
    titulo = normalizar(d["titulo"])
    out = []
    for otro in _leer(_anio_de(reg)):
        if otro["id"] == reg["id"] or otro["clase"] != clase or otro["estado"] == DESCARTADO:
            continue
        o = otro["datos"]
        mismo_momento = (o.get("fecha") == d.get("fecha")) if clase == "actividad" else (o.get("mes") == d.get("mes"))
        if mismo_momento and normalizar(o["titulo"]) == titulo:
            out.append(otro["id"])
    return out


def guardar(clase: str, datos: dict, *, link: dict | None = None, cargado_por: str = "") -> dict:
    """Crea un registro. Si viene de un link público queda 'recibido'; si lo carga
    una bibliotecaria desde la app, ya queda validado (lo está cargando ella)."""
    clase = str(clase or "").strip()
    limpios = limpiar(clase, datos)
    reg = {
        "id": "",
        "clase": clase,
        "estado": RECIBIDO if link else VALIDADO,
        "datos": limpios,
        "link_id": (link or {}).get("id"),
        "origen": (link or {}).get("origen"),
        "creado": _ahora(),
        "cargado_por": cargado_por,
        "historial": [],
        "validado_por": cargado_por if not link else "",
        "validado_cuando": _ahora() if not link else "",
        "motivo": "",
        "esquema": ESQUEMA,
    }
    anio = _anio_de(reg)
    reg["id"] = f"{anio}-{secrets.token_hex(5)}"
    reg["posibles_duplicados"] = _parecidos(reg)
    with _LOCK:
        lista = _leer(anio)
        lista.append(reg)
        _guardar(anio, lista)
    if link:
        _marcar_uso(link["id"])
    logger.info("Registro %s (%s) guardado%s", reg["id"], clase, " desde link" if link else "")
    return reg


def corregir(reg_id: str, datos: dict, por: str) -> dict:
    """Completa o corrige un registro recibido. Queda constancia de lo que se cambió."""
    with _LOCK:
        anio, lista, reg = _ubicar(reg_id)
        nuevos = limpiar(reg["clase"], {**reg["datos"], **(datos or {})})
        cambios = sorted(k for k in nuevos if nuevos[k] != reg["datos"].get(k))
        if cambios:
            reg.setdefault("historial", []).append(
                {"por": por, "cuando": _ahora(), "cambios": cambios,
                 "antes": {k: reg["datos"].get(k) for k in cambios}})
            reg["datos"] = nuevos
        if _anio_de(reg) != anio:                       # cambió la fecha de año: se muda de clave
            _guardar(anio, [r for r in lista if r["id"] != reg_id])
            otra = _leer(_anio_de(reg))
            otra.append(reg)
            _guardar(_anio_de(reg), otra)
        else:
            _guardar(anio, lista)
    return reg


def resolver(reg_id: str, estado: str, por: str, motivo: str = "") -> dict:
    """Validar o descartar. Solo lo validado cuenta en las estadísticas."""
    if estado not in (VALIDADO, DESCARTADO):
        raise ErrorRegistro(f"Estado desconocido: '{estado}'.")
    if estado == DESCARTADO and not (motivo or "").strip():
        raise ErrorRegistro("Para descartar hay que decir por qué.")
    with _LOCK:
        anio, lista, reg = _ubicar(reg_id)
        reg["estado"] = estado
        reg["validado_por"] = por
        reg["validado_cuando"] = _ahora()
        reg["motivo"] = _txt(motivo, 300)
        reg.setdefault("historial", []).append(
            {"por": por, "cuando": _ahora(), "estado": estado, "motivo": reg["motivo"]})
        _guardar(anio, lista)
    return reg


def borrar(reg_id: str) -> None:
    with _LOCK:
        anio, lista, _ = _ubicar(reg_id)
        _guardar(anio, [r for r in lista if r["id"] != reg_id])


def obtener(reg_id: str) -> dict:
    return _ubicar(reg_id)[2]


def listar(*, estado: str | None = None, clase: str | None = None,
           desde: str | None = None, hasta: str | None = None, anio: int | None = None) -> list[dict]:
    """Registros ordenados del más nuevo al más viejo. `desde`/`hasta` son fechas."""
    anios = [anio] if anio else sorted({date.today().year, date.today().year - 1})
    out = []
    for a in anios:
        for r in _leer(a):
            if estado and r["estado"] != estado:
                continue
            if clase and r["clase"] != clase:
                continue
            cuando = cuando_de(r)
            if desde and cuando < desde:
                continue
            if hasta and cuando > hasta:
                continue
            out.append(r)
    return sorted(out, key=lambda r: (cuando_de(r), r["creado"]), reverse=True)


def cuando_de(reg: dict) -> str:
    """La fecha de la actividad, o el último día del mes informado (para ordenar juntos)."""
    d = reg["datos"]
    return d.get("fecha") or (d.get("mes", "") + "-28")


def de_subcomision(subcomision: str, anio: int | None = None) -> list[dict]:
    """Lo que registró una subcomisión (sin lo descartado), del más nuevo al más viejo."""
    return [r for r in listar(anio=anio) if es_de(r, subcomision) and r["estado"] != DESCARTADO]


def validados_entre(desde: date, hasta: date, subcomision: str | None = None) -> list[dict]:
    """Registros validados cuya actividad (o mes de taller) cae entre dos fechas.

    Con `subcomision`, solo los de esa subcomisión.
    """
    out = []
    for anio in range(desde.year, hasta.year + 1):
        for r in _leer(anio):
            if r["estado"] != VALIDADO:
                continue
            if subcomision and not es_de(r, subcomision):
                continue
            ini, fin = rango_de(r)
            if ini <= hasta and fin >= desde:
                out.append(r)
    return out


def rango_de(reg: dict) -> tuple[date, date]:
    """Días que abarca un registro: el de la actividad, o el mes entero del resumen."""
    d = reg["datos"]
    if d.get("fecha"):
        dia = date.fromisoformat(d["fecha"])
        return dia, dia
    anio, mes = (int(x) for x in d["mes"].split("-"))
    fin = date(anio + (mes == 12), mes % 12 + 1, 1)
    return date(anio, mes, 1), date.fromordinal(fin.toordinal() - 1)


DIAS = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
MOMENTOS = {"manana": "Mañana", "tarde": "Tarde", "noche": "Noche"}


def derivados(reg: dict) -> dict:
    """Datos que se calculan de lo cargado y sirven para comparar: día de la semana,
    momento del día y duración. Se calculan siempre igual, así no hay dos criterios."""
    d = reg["datos"]
    if reg["clase"] != "actividad" or not d.get("fecha"):
        return {}
    dia = date.fromisoformat(d["fecha"]).weekday()
    hora = int((d.get("hora_inicio") or "0:0").split(":")[0])
    momento = "manana" if hora < 12 else "tarde" if hora < 19 else "noche"
    duracion = None
    if d.get("hora_inicio") and d.get("hora_fin"):
        h1, m1 = (int(x) for x in d["hora_inicio"].split(":"))
        h2, m2 = (int(x) for x in d["hora_fin"].split(":"))
        duracion = max(0, (h2 * 60 + m2) - (h1 * 60 + m1)) or None
    return {"dia_semana": dia, "dia_nombre": DIAS[dia], "momento": momento,
            "momento_nombre": MOMENTOS[momento], "duracion_min": duracion}


def pendientes() -> int:
    return sum(1 for r in listar(estado=RECIBIDO))


def con_atencion() -> list[dict]:
    """Registros que reportan un incidente marcado como 'requiere atención'."""
    return [r for r in listar() if (r["datos"].get("incidente") or {}).get("requiere_atencion")
            and r["estado"] != DESCARTADO]


def para_ver(reg: dict) -> dict:
    """El registro con los nombres a la vista (para la bandeja y el detalle)."""
    d = reg["datos"]
    espacio = espacios.nombre_de(d.get("espacio_id", "")) if d.get("espacio_id") else d.get("espacio_otro", "")
    return {
        **reg,
        "estado_etiqueta": ESTADOS.get(reg["estado"], reg["estado"]),
        "cuando": cuando_de(reg),
        "espacio_nombre": espacio,
        "tipo_etiqueta": TIPOS.get(d.get("tipo"), ""),
        "tematicas_etiquetas": [TEMATICAS[t] for t in d.get("tematicas", []) if t in TEMATICAS],
    }


def para_subcomision(reg: dict) -> dict:
    """Lo que ve una subcomisión de un registro suyo: lo cargado, sin la cocina de la
    bandeja (duplicados, links, quién validó, qué había antes de cada corrección)."""
    v = para_ver(reg)
    return {k: v[k] for k in ("id", "clase", "estado", "estado_etiqueta", "datos", "cuando",
                              "espacio_nombre", "tipo_etiqueta", "tematicas_etiquetas")} | {
        "corregido": any(h.get("cambios") for h in reg.get("historial", []))}


# ── Exportación ─────────────────────────────────────────────────────────────
_COLUMNAS = [
    ("id", lambda r, d: r["id"]),
    ("clase", lambda r, d: r["clase"]),
    ("estado", lambda r, d: ESTADOS.get(r["estado"], r["estado"])),
    ("fecha_o_mes", lambda r, d: d.get("fecha") or d.get("mes", "")),
    ("titulo", lambda r, d: d.get("titulo", "")),
    ("tipo", lambda r, d: TIPOS.get(d.get("tipo"), "")),
    ("tematicas", lambda r, d: " · ".join(TEMATICAS.get(t, t) for t in d.get("tematicas", []))),
    ("hora_inicio", lambda r, d: d.get("hora_inicio", "")),
    ("hora_fin", lambda r, d: d.get("hora_fin", "")),
    ("espacio", lambda r, d: espacios.nombre_de(d["espacio_id"]) if d.get("espacio_id") else d.get("espacio_otro", "")),
    ("modalidad", lambda r, d: MODALIDADES.get(d.get("modalidad"), "")),
    ("a_cargo", lambda r, d: d.get("a_cargo") or d.get("tallerista", "")),
    ("organiza", lambda r, d: ORGANIZA.get(d.get("organiza"), "")),
    ("organiza_detalle", lambda r, d: d.get("organiza_detalle", "")),
    ("subcomision", lambda r, d: d.get("subcomision", "")),
    ("encuentros", lambda r, d: d.get("encuentros", "")),
    ("suspendidos", lambda r, d: d.get("suspendidos", "")),
    ("personas", lambda r, d: d.get("personas_total", d.get("participantes", ""))),
    ("asistencia_promedio", lambda r, d: d.get("asistencia_promedio", "")),
    *[(f"edad_{k}", (lambda k: lambda r, d: (d.get("franjas") or {}).get(k, ""))(k)) for k in FRANJAS],
    ("primera_vez", lambda r, d: d.get("primera_vez", "")),
    ("altas", lambda r, d: d.get("altas", "")),
    ("bajas", lambda r, d: d.get("bajas", "")),
    ("difusion", lambda r, d: " · ".join(DIFUSION.get(x, x) for x in d.get("difusion", []))),
    ("acceso", lambda r, d: ACCESOS.get(d.get("acceso"), "")),
    ("monto", lambda r, d: d.get("monto", "")),
    ("recaudacion", lambda r, d: d.get("recaudacion", "")),
    ("descripcion", lambda r, d: d.get("descripcion") or d.get("trabajado", "")),
    ("valoracion", lambda r, d: d.get("valoracion", "")),
    ("funciono", lambda r, d: d.get("funciono", "")),
    ("mejorar", lambda r, d: d.get("mejorar", "")),
    ("incidente", lambda r, d: (d.get("incidente") or {}).get("texto", "")),
    ("continua", lambda r, d: CONTINUA.get(d.get("continua"), "")),
    ("completo", lambda r, d: (d.get("quien_completa") or {}).get("nombre", "")),
    ("dia_semana", lambda r, d: derivados(r).get("dia_nombre", "")),
    ("momento", lambda r, d: derivados(r).get("momento_nombre", "")),
    ("duracion_min", lambda r, d: derivados(r).get("duracion_min") or ""),
    ("cargado", lambda r, d: r.get("creado", "")[:10]),
]


def exportar_csv(registros: list[dict]) -> str:
    """CSV listo para abrir en Excel o en una planilla (separador coma, con encabezado)."""
    import csv
    import io

    salida = io.StringIO()
    w = csv.writer(salida)
    w.writerow([c[0] for c in _COLUMNAS])
    for r in registros:
        d = r["datos"]
        w.writerow([f(r, d) if f(r, d) is not None else "" for _, f in _COLUMNAS])
    return salida.getvalue()
