"""Historial de envíos de mail — unificado para la pestaña Mails y la de Automáticos.

Guarda QUÉ se mandó, A QUIÉN y CON QUÉ RESULTADO, y (si hay APP_PUBLIC_URL) si el
mail fue abierto.

Cómo se guarda (dos niveles, a propósito):
  - `envios_index`  → una lista de RESÚMENES, la más nueva primero. Es lo que se
    lee para pintar la lista; es corta y barata.
  - `envio_<run_id>` → el DETALLE de un envío: cada destinatario con su estado,
    sus variables de combinación y sus aperturas.

Se separan porque un envío a socios puede tener cientos de destinatarios: si todo
viviera en una sola clave, cada apertura de mail reescribiría megabytes.

El mensaje de cada persona NO se guarda ya renderizado: se guarda la plantilla del
envío + las variables de esa persona (+ su override, si lo personalizaron), y se
renderiza al pedir el detalle con la misma función que usó el envío. Es idéntico a
lo que recibió y ocupa mucho menos.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import mail, storage, tracking
from .tracking import new_run_id  # noqa: F401  (re-export: los llamadores piden el id acá)

logger = logging.getLogger("historial")

INDEX_KEY = "envios_index"
RUN_PREFIX = "envio_"
_LEGACY_KEY = "auto_mail_history"    # formato anterior (solo automáticos, todo en una clave)

_MAX_RUNS = 200        # ejecuciones guardadas (las más viejas se podan con su detalle)
_MAX_VAR_LEN = 4000    # corte de seguridad por variable, para que un dato raro no infle la base


def _run_key(run_id: str) -> str:
    return f"{RUN_PREFIX}{run_id}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Índice ──────────────────────────────────────────────────────────────────
def _read_index() -> list[dict]:
    idx = storage.get(INDEX_KEY)
    if idx is None:
        idx = _migrate_legacy()
    return idx or []


def _migrate_legacy() -> list[dict]:
    """Pasa el historial viejo de automáticos (`auto_mail_history`) al formato nuevo.

    Corre una sola vez: al terminar deja `envios_index` escrito. No borra la clave
    vieja (por si hay que mirarla), simplemente deja de usarla.
    """
    old = storage.get(_LEGACY_KEY)
    if not old:
        storage.set(INDEX_KEY, [])
        return []
    idx: list[dict] = []
    for h in old[:_MAX_RUNS]:      # lo que excede el tope no se migra (si no, quedan detalles huérfanos)
        run_id = h.get("run_id") or new_run_id()
        dests = [{"email": d.get("email", ""), "nombre": d.get("nombre", ""), "apellido": "",
                  "carnet": "", "status": d.get("status", ""), "detail": d.get("detail", ""),
                  "vars": {}, "subject": None, "body": None}
                 for d in (h.get("destinatarios") or [])]
        resumen = {
            "run_id": run_id, "ts": h.get("ts") or "", "origen": "auto",
            "titulo": h.get("report_name") or "", "report_id": h.get("report_id"),
            "tipo": h.get("tipo"), "trigger": h.get("trigger"), "usuario": "",
            "test_to": h.get("test_to"), "dry_run": False,
            "ok": h.get("ok", True), "error": h.get("error"),
            **_contadores(dests), "aperturas": 0, "seguimiento": False,
        }
        idx.append(resumen)
        try:
            storage.set(_run_key(run_id), {**resumen, "subject_tpl": "", "body_tpl": "",
                                           "destinatarios": dests})
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo migrar el detalle del envío %s: %s", run_id, exc)
    storage.set(INDEX_KEY, idx[:_MAX_RUNS])
    logger.info("Historial migrado al formato nuevo: %d envíos.", len(idx))
    return idx[:_MAX_RUNS]


def _contadores(dests: list[dict]) -> dict:
    st = [d.get("status") for d in dests]
    return {"total": len(dests),
            "enviados": st.count("sent"),
            "sin_email": st.count("skipped"),
            "errores": st.count("error"),
            "simulados": st.count("simulado")}


# ── Guardar un envío ────────────────────────────────────────────────────────
def _clip(v) -> str:
    s = str(v if v is not None else "")
    return s if len(s) <= _MAX_VAR_LEN else s[:_MAX_VAR_LEN] + "…"


def destinatarios(recipients: list[dict], results: list[dict]) -> list[dict]:
    """Cruza los destinatarios que se mandaron a `mail.send_campaign` con su resultado.

    El cruce es por `idx` (la posición original), no por email: en modo prueba todos
    los mails van a la misma dirección y cruzar por email mezclaría a las personas.
    """
    out = []
    for r in recipients:
        v = {k: _clip(val) for k, val in (r.get("vars") or {}).items()}
        out.append({
            "email": r.get("email") or "",
            "nombre": v.get("nombre", ""), "apellido": v.get("apellido", ""),
            "carnet": str(r.get("_carnet") or v.get("carnet") or ""),
            "status": "", "detail": "",
            "vars": v,
            "subject": r.get("subject") or None,   # override individual, si lo hubo
            "body": r.get("body") or None,
        })
    for res in results:
        i = res.get("idx")
        if not isinstance(i, int) or not (0 <= i < len(out)):
            continue
        out[i]["status"] = res.get("status", "")
        out[i]["detail"] = res.get("detail", "")
        enviado_a = res.get("email") or ""
        if enviado_a and enviado_a != out[i]["email"]:
            out[i]["enviado_a"] = enviado_a       # envío de prueba: fue a otra dirección
    return out


def save_run(*, run_id: str, origen: str, titulo: str, subject_tpl: str, body_tpl: str,
             dests: list[dict], report_id: str | None = None, tipo: str | None = None,
             trigger: str = "manual", usuario: str = "", test_to: str | None = None,
             dry_run: bool = False, ok: bool = True, error: str | None = None,
             seguimiento: bool = False) -> dict:
    """Guarda el detalle del envío y su resumen en el índice. Devuelve el resumen."""
    resumen = {
        "run_id": run_id, "ts": _now(), "origen": origen, "titulo": titulo,
        "report_id": report_id, "tipo": tipo, "trigger": trigger, "usuario": usuario,
        "test_to": test_to or None, "dry_run": bool(dry_run),
        "ok": ok, "error": error,
        **_contadores(dests), "aperturas": 0, "seguimiento": bool(seguimiento),
    }
    storage.set(_run_key(run_id), {**resumen, "subject_tpl": subject_tpl or "",
                                   "body_tpl": body_tpl or "", "destinatarios": dests})
    idx = _read_index()
    idx.insert(0, resumen)
    for viejo in idx[_MAX_RUNS:]:               # poda: borra el detalle de lo que sale del índice
        try:
            storage.delete(_run_key(viejo["run_id"]))
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo podar el envío %s: %s", viejo.get("run_id"), exc)
    storage.set(INDEX_KEY, idx[:_MAX_RUNS])
    return resumen


def record(**kwargs) -> dict | None:
    """`save_run` que nunca rompe el envío: si el historial falla, solo lo registra."""
    try:
        return save_run(**kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo guardar el historial del envío: %s", exc)
        return None


# ── Leer ────────────────────────────────────────────────────────────────────
def listar(origen: str | None = None, report_id: str | None = None, limit: int = 20) -> list[dict]:
    items = _read_index()
    if origen:
        items = [h for h in items if h.get("origen") == origen]
    if report_id:
        items = [h for h in items if h.get("report_id") == report_id]
    return items[:limit]


def get_run(run_id: str) -> dict | None:
    """Detalle de un envío, con el mensaje de cada destinatario ya renderizado."""
    run = storage.get(_run_key(run_id))
    if not run:
        return None
    subject_tpl, body_tpl = run.get("subject_tpl") or "", run.get("body_tpl") or ""
    dests = []
    for d in run.get("destinatarios") or []:
        v = d.get("vars") or {}
        dests.append({**d,
                      "subject": mail.render(d.get("subject") or subject_tpl, v),
                      "body": mail.render(d.get("body") or body_tpl, v)})
    return {**run, "destinatarios": dests}


# ── Aperturas ───────────────────────────────────────────────────────────────
def record_open(run_id: str, idx: int) -> bool:
    """Registra que el destinatario `idx` del envío `run_id` abrió el mail."""
    run = storage.get(_run_key(run_id))
    if not run:
        return False
    dests = run.get("destinatarios") or []
    if not (0 <= idx < len(dests)):
        return False
    d = dests[idx]
    primera = not d.get("opened_at")
    ahora = _now()
    if primera:
        d["opened_at"] = ahora
    d["opens"] = int(d.get("opens") or 0) + 1
    d["last_open"] = ahora
    aperturas = sum(1 for x in dests if x.get("opened_at"))
    run["aperturas"] = aperturas
    storage.set(_run_key(run_id), run)
    if primera:                                  # el índice solo cambia cuando alguien abre por 1ª vez
        items = _read_index()
        for h in items:
            if h.get("run_id") == run_id:
                h["aperturas"] = aperturas
                storage.set(INDEX_KEY, items)
                break
    return True


def aplicar_seguimiento(recipients: list[dict], run_id: str) -> bool:
    """Pone el píxel de apertura en cada destinatario. False si no hay APP_PUBLIC_URL."""
    if not tracking.enabled():
        return False
    for i, r in enumerate(recipients):
        url = tracking.pixel_url(run_id, i)
        if url:
            r["track_url"] = url
    return True
