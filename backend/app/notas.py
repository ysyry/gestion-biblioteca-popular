"""Notas de socios: los mensajes internos que las bibliotecarias dejan en Koha.

En Koha 3.18 se cargan desde circulación ("Agregar mensaje") y quedan en la tabla
`messages`: tipo `L` los ve solo el personal, tipo `B` también el socio. Los campos
de nota de la ficha (`borrowernotes`, `opacnote`) no se usan en esta biblioteca.

**Solo lectura** (decisión tomada): las notas se siguen escribiendo en Koha. La app
las lee, las clasifica y deja marcar una novedad como "resuelta"; esa marca es lo
único que se guarda de este lado, porque Koha no tiene dónde.

Clasificación. En el relevamiento del 15/09/2026, de ~7.250 notas:
  · **cuotas**  (~5.200) — registros de pago de rutina: "Cuotas hasta JUNIO/2025".
  · **reclamo** (~940)   — avisos de rutina: "Reclamé libros x wp".
  · **novedad** (~1.100) — todo lo que agrega información: acuerdos, bajas, becas,
    libros rotos o perdidos, "dice que lo devuelve en noviembre".
Solo se considera rutina lo que es **corto y sigue el patrón**. Muchas notas que
empiezan con "Cuotas…" siguen con un acuerdo ("…AÑO 2021 LO PAGARÁ EN CUOTAS"):
esas van como novedad, porque esconder una novedad es peor que mostrar de más.

El texto se pide a Koha en hexadecimal: el export separado por tabuladores no escapa
nada, así que una nota con un salto de línea o un tabulador partía la fila.
"""
from __future__ import annotations

import logging
import re
import threading
import unicodedata
from datetime import date, datetime, timedelta, timezone

from . import storage
from .koha.client import sql_literal

logger = logging.getLogger("notas")

CLAVE_RESUELTAS = "notas_resueltas"
_LOCK = threading.Lock()

CUOTAS, RECLAMO, NOVEDAD = "cuotas", "reclamo", "novedad"
ETIQUETAS = {CUOTAS: "Cuotas", RECLAMO: "Reclamo", NOVEDAD: "Novedad"}

# Tope de filas por consulta: "todo el historial" sin búsqueda son miles de notas.
LIMITE = 2000

# ── Clasificación ───────────────────────────────────────────────────────────
_MES = r"(ene|feb|mar|abr|may|jun|jul|ago|sep|set|oct|nov|dic)"

# Registro de pago de rutina. Tolera los errores de tipeo reales ("Cuaotas", "Cuatas",
# "ctaspagas") y las abreviaturas ("Ctas pagas h/ Dic'17").
_PAGO = re.compile(
    r"^\W*(pagos? (de )?|pagas? |se pag\w+ )?"
    r"(cu\w{0,3}tas?\b|ctas?(pagas)?\b|inscrip\w*|re-? ?inscrip\w*|debito automatico|matricula"
    r"|adhe\w* (al )?debito"
    rf"|(hasta )?{_MES}\w*\.?\s*(/|de )?\s*'?\d{{2,4}}\W*$|hasta {_MES})"
)
# Aviso de rutina: se reclamó, se recordó, se mandó un mensaje.
_AVISO = re.compile(
    r"^\W*(volv\w* a )?(reclam|record|consult|solicit|pedido de|ped[i]\b|mensaje|mande\b|envie\b)")
# Palabras que delatan información nueva aunque la nota empiece como rutina.
_INFO = re.compile(
    r"\$|deposit|garantia|dejo\b|adeud|\bdebe\b|beca|baja|acord|condon|devol|ficha|transitori"
    r"|a cuenta|a medias|no puede|dice|dijo|pagar|cuando|queda|falta")


def _normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y con los espacios colapsados."""
    s = unicodedata.normalize("NFD", (texto or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.split())


def clasificar(texto: str) -> str:
    n = _normalizar(texto)
    if _INFO.search(n):
        return NOVEDAD
    if len(n) <= 60 and _PAGO.search(n):
        return CUOTAS
    if len(n) <= 45 and _AVISO.search(n):
        return RECLAMO
    return NOVEDAD


# ── Consultas a Koha ────────────────────────────────────────────────────────
_COLUMNAS = """m.message_id AS id, m.message_date AS fecha, m.message_type AS tipo_koha,
       m.branchcode AS sede, HEX(m.message) AS texto_hex"""


def sql_de_socio(cardnumber: str) -> str:
    """Todas las notas de un socio, de la más nueva a la más vieja."""
    return f"""SELECT {_COLUMNAS}
FROM messages m JOIN borrowers b ON b.borrowernumber = m.borrowernumber
WHERE b.cardnumber = {sql_literal(cardnumber)}
ORDER BY m.message_date DESC, m.message_id DESC"""


def sql_recientes(desde: date | None, texto: str | None = None, limite: int = LIMITE) -> str:
    """Notas de todos los socios desde una fecha (o de siempre), con búsqueda opcional."""
    donde = []
    if desde:
        donde.append(f"m.message_date >= '{desde.isoformat()} 00:00:00'")
    if texto:
        donde.append(f"m.message LIKE {sql_literal('%' + texto + '%')}")
    where = ("WHERE " + " AND ".join(donde)) if donde else ""
    return f"""SELECT {_COLUMNAS}, b.cardnumber, b.surname, b.firstname
FROM messages m JOIN borrowers b ON b.borrowernumber = m.borrowernumber
{where}
ORDER BY m.message_date DESC, m.message_id DESC
LIMIT {int(limite)}"""


def decodificar(texto_hex: str | None) -> str:
    """Texto de la nota a partir del HEX de Koha (UTF-8), sin los finales de línea sueltos."""
    try:
        crudo = bytes.fromhex((texto_hex or "").strip())
    except ValueError:
        return ""
    texto = crudo.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(linea.rstrip() for linea in texto.split("\n")).strip()


# ── Resueltas (lo único que se guarda en la app) ────────────────────────────
def resueltas() -> dict:
    return storage.get(CLAVE_RESUELTAS) or {}


def marcar(nota_id: str, resuelta: bool, por: str) -> dict:
    """Marca o desmarca una nota como resuelta. Devuelve la marca (vacía si se quitó)."""
    nota_id = str(nota_id).strip()
    if not nota_id.isdigit():
        raise ValueError("Id de nota inválido.")
    with _LOCK:
        todas = resueltas()
        if resuelta:
            todas[nota_id] = {"por": por,
                              "cuando": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        else:
            todas.pop(nota_id, None)
        storage.set(CLAVE_RESUELTAS, todas)
    logger.info("Nota %s %s por %s", nota_id, "resuelta" if resuelta else "reabierta", por)
    return todas.get(nota_id, {})


# ── Armado de lo que ve la pantalla ─────────────────────────────────────────
def armar(filas: list[dict], marcas: dict | None = None) -> list[dict]:
    """Filas crudas de Koha → notas listas: texto, tipo y si está resuelta."""
    marcas = resueltas() if marcas is None else marcas
    out = []
    for f in filas:
        nid = str(f.get("id") or "").strip()
        texto = decodificar(f.get("texto_hex"))
        if not nid or not texto:
            continue
        tipo = clasificar(texto)
        marca = marcas.get(nid)
        nota = {
            "id": nid,
            "fecha": str(f.get("fecha") or ""),
            "texto": texto,
            "tipo": tipo,
            "tipo_etiqueta": ETIQUETAS[tipo],
            "sede": f.get("sede") or "",
            "para_socio": (f.get("tipo_koha") or "") == "B",
            "resuelta": bool(marca),
            "resuelta_por": (marca or {}).get("por", ""),
            "resuelta_cuando": (marca or {}).get("cuando", ""),
        }
        if "cardnumber" in f:
            nota.update({"cardnumber": f.get("cardnumber") or "",
                         "surname": f.get("surname") or "",
                         "firstname": f.get("firstname") or ""})
        out.append(nota)
    return out


def _dia(nota: dict) -> date | None:
    try:
        return date.fromisoformat(nota["fecha"][:10])
    except (KeyError, ValueError):
        return None


def novedades_pendientes(notas: list[dict], dias: int, hoy: date | None = None) -> list[dict]:
    """Novedades sin resolver de los últimos `dias` días."""
    limite = (hoy or date.today()) - timedelta(days=dias)
    return [n for n in notas
            if n["tipo"] == NOVEDAD and not n["resuelta"] and (_dia(n) or date.min) >= limite]


def avisos_por_socio(notas: list[dict], dias: int, hoy: date | None = None) -> dict:
    """Carnet → novedades pendientes recientes. Lo usan Préstamos, Mails y Automáticos
    para mostrar el 📝 antes de reclamarle algo a alguien que ya avisó."""
    out: dict[str, dict] = {}
    for n in novedades_pendientes(notas, dias, hoy):
        card = str(n.get("cardnumber") or "").strip()
        if not card:
            continue
        a = out.setdefault(card, {"cantidad": 0, "notas": []})
        a["cantidad"] += 1
        if len(a["notas"]) < 3:                      # alcanza para el vistazo; el resto, en la ficha
            a["notas"].append({"id": n["id"], "fecha": n["fecha"], "texto": n["texto"]})
    return out
