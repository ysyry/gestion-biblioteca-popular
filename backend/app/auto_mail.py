"""Envíos automáticos de la biblioteca.

Dos funciones configurables:
  1. resumen_interno    → un mail a la biblioteca con préstamos por vencer + vencidos.
  2. recordatorio_socios → un mail a cada socio con préstamos por vencer/vencidos.

La config se persiste en backend/data/auto_config.json. Los jobs corren SIN nadie
logueado, así que usan las credenciales de SERVICIO de Koha (KOHA_USER/PASSWORD).
El programador (APScheduler) chequea una vez por día y dispara según `cada_dias`.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path

from . import mail
from .config import settings
from .koha.client import KohaClient
from .koha.reports import KohaRepository

logger = logging.getLogger("auto_mail")

# Ruta de datos configurable (en Railway se apunta a un volumen persistente con APP_DATA_DIR).
DATA_DIR = Path(os.getenv("APP_DATA_DIR") or (Path(__file__).resolve().parent.parent / "data"))
CONFIG_FILE = DATA_DIR / "auto_config.json"

JOBS = ("resumen_interno", "recordatorio_socios")

DEFAULTS = {
    "resumen_interno": {
        "enabled": False,
        "to": "",                 # vacío => usa SMTP_FROM
        "cada_dias": 7,
        "dias_antes": 7,          # "por vencer" = vence dentro de N días
        "subject": "Resumen de préstamos — Biblioteca Osvaldo Bayer",
        "body": ("Resumen automático de préstamos al {{fecha}}.\n\n"
                 "VENCIDOS ({{total_vencidos}}):\n{{lista_vencidos}}\n\n"
                 "POR VENCER en los próximos {{dias_antes}} días ({{total_por_vencer}}):\n"
                 "{{lista_por_vencer}}\n\n"
                 "Socios con préstamos vencidos: {{total_socios_deben}}."),
        "footer": "— Sistema de gestión · Biblioteca Popular Osvaldo Bayer",
    },
    "recordatorio_socios": {
        "enabled": False,
        "cada_dias": 7,
        "dias_antes": 3,
        "incluir_vencidos": True,
        "incluir_por_vencer": True,
        "umbral_atraso": 1,       # mínimo de días de atraso para incluir un vencido
        "subject": "Tus préstamos en la Biblioteca Osvaldo Bayer",
        "body": ("Hola {{nombre}},\n\n"
                 "Te recordamos tus préstamos en la Biblioteca Popular Osvaldo Bayer.\n\n"
                 "Vencidos ({{cantidad_vencidos}}):\n{{vencidos}}\n\n"
                 "Por vencer ({{cantidad_por_vencer}}):\n{{por_vencer}}"),
        "footer": "Te esperamos para renovarlos o devolverlos. ¡Gracias!\nBiblioteca Popular Osvaldo Bayer.",
    },
}


# ── Config ──────────────────────────────────────────────────────────────────
def load_config() -> dict:
    data = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    cfg = {job: {**DEFAULTS[job], **(data.get(job) or {})} for job in JOBS}
    cfg["_last_run"] = data.get("_last_run") or {job: None for job in JOBS}
    return cfg


def save_config(partial: dict) -> dict:
    cfg = load_config()
    for job in JOBS:
        if isinstance(partial.get(job), dict):
            cfg[job] = {**cfg[job], **partial[job]}
    _write(cfg)
    return cfg


def _write(cfg: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _mark_run(job: str, when: str) -> None:
    cfg = load_config()
    cfg["_last_run"][job] = when
    _write(cfg)


# ── Datos (Koha, con credenciales de servicio) ──────────────────────────────
async def _all_loans() -> list[dict]:
    if not settings.koha_user or not settings.koha_password:
        raise RuntimeError("Faltan credenciales de servicio de Koha (KOHA_USER/KOHA_PASSWORD en .env).")
    client = KohaClient(settings.koha_base_url, settings.koha_user, settings.koha_password)
    await client.login()
    try:
        return await KohaRepository(client).loans_contact()
    finally:
        await client.aclose()


def _d(s) -> str:
    return (s or "")[:10]


def _dias(row) -> int | None:
    try:
        return int(row.get("dias_atraso"))
    except (TypeError, ValueError):
        return None


# ── Resumen interno ─────────────────────────────────────────────────────────
def _build_resumen_vars(rows: list[dict], cfg: dict) -> tuple[dict, dict]:
    dias_antes = int(cfg["dias_antes"])
    venc, porv = [], []
    for r in rows:
        d = _dias(r)
        if d is None:
            continue
        if d > 0:
            venc.append(r)
        elif -dias_antes <= d <= 0:
            porv.append(r)
    venc.sort(key=lambda r: -_dias(r))
    porv.sort(key=lambda r: _dias(r))

    def nombre(r):
        return f'{r.get("surname","")}, {r.get("firstname","")}'.strip(", ")

    def lv(r):
        return f"• {nombre(r)} — {r.get('title') or r.get('barcode') or '(sin título)'} (venció {_d(r.get('date_due'))}, {_dias(r)} días)"

    def lp(r):
        return f"• {nombre(r)} — {r.get('title') or r.get('barcode') or '(sin título)'} (vence {_d(r.get('date_due'))}, en {-_dias(r)} días)"

    socios_deben = len({r.get("cardnumber") for r in venc if r.get("cardnumber")})
    vars = {
        "fecha": date.today().isoformat(),
        "dias_antes": str(dias_antes),
        "total_vencidos": str(len(venc)),
        "total_por_vencer": str(len(porv)),
        "total_socios_deben": str(socios_deben),
        "lista_vencidos": "\n".join(lv(r) for r in venc) or "ninguno",
        "lista_por_vencer": "\n".join(lp(r) for r in porv) or "ninguno",
    }
    stats = {"vencidos": len(venc), "por_vencer": len(porv), "socios_deben": socios_deben}
    return vars, stats


async def build_resumen(cfg: dict) -> dict:
    rows = await _all_loans()
    vars, stats = _build_resumen_vars(rows, cfg)
    footer = ("\n\n" + cfg["footer"]) if cfg.get("footer") else ""
    return {
        "to": (cfg.get("to") or "").strip() or settings.smtp_from or settings.smtp_user,
        "subject": mail.render(cfg["subject"], vars),
        "body": mail.render(cfg["body"], vars) + footer,
        "stats": stats,
    }


async def run_resumen(cfg: dict, test_to: str | None = None) -> dict:
    data = await build_resumen(cfg)
    to = test_to or data["to"]
    if not to:
        raise RuntimeError("No hay dirección destino (configurá 'A qué correo' o SMTP_FROM).")
    res = await mail.send_campaign(
        data["subject"], data["body"],
        [{"email": to, "vars": {}, "subject": None, "body": None}],
        dry_run=False, test_to=None,
    )
    return {"sent_to": to, "stats": data["stats"], "result": res}


# ── Recordatorio a socios ───────────────────────────────────────────────────
def _build_recordatorio_recipients(rows: list[dict], cfg: dict) -> dict:
    dias_antes = int(cfg["dias_antes"])
    umbral = int(cfg["umbral_atraso"])
    inc_v = bool(cfg["incluir_vencidos"])
    inc_p = bool(cfg["incluir_por_vencer"])

    by: dict[str, dict] = {}
    for r in rows:
        c = r.get("cardnumber")
        if not c:
            continue
        by.setdefault(c, {"info": r, "loans": []})["loans"].append(r)

    def fv(l):
        return f"• {l.get('title') or l.get('barcode') or '(sin título)'} (venció {_d(l.get('date_due'))})"

    def fp(l):
        return f"• {l.get('title') or l.get('barcode') or '(sin título)'} (vence {_d(l.get('date_due'))})"

    recipients = []
    for c, g in by.items():
        venc = [l for l in g["loans"] if (_dias(l) or 0) >= umbral]
        porv = [l for l in g["loans"] if _dias(l) is not None and -dias_antes <= _dias(l) <= 0]
        relevante = (inc_v and venc) or (inc_p and porv)
        if not relevante:
            continue
        info = g["info"]
        recipients.append({
            "email": info.get("email") or None,
            "vars": {
                "nombre": info.get("firstname", ""), "apellido": info.get("surname", ""), "carnet": c,
                "vencidos": "\n".join(fv(l) for l in venc) or "ninguno",
                "por_vencer": "\n".join(fp(l) for l in porv) or "ninguno",
                "cantidad_vencidos": str(len(venc)),
                "cantidad_por_vencer": str(len(porv)),
            },
            "subject": None, "body": None,
        })
    con_email = sum(1 for r in recipients if r["email"])
    return {"recipients": recipients, "con_email": con_email, "sin_email": len(recipients) - con_email}


def _body_tpl(cfg: dict) -> str:
    return cfg["body"] + (("\n\n" + cfg["footer"]) if cfg.get("footer") else "")


async def build_recordatorio(cfg: dict) -> dict:
    rows = await _all_loans()
    data = _build_recordatorio_recipients(rows, cfg)
    sample = None
    if data["recipients"]:
        r0 = data["recipients"][0]
        sample = {
            "subject": mail.render(cfg["subject"], r0["vars"]),
            "body": mail.render(_body_tpl(cfg), r0["vars"]),
            "email": r0["email"],
        }
    return {"total": len(data["recipients"]), "con_email": data["con_email"],
            "sin_email": data["sin_email"], "sample": sample}


async def run_recordatorio(cfg: dict, test_to: str | None = None) -> dict:
    rows = await _all_loans()
    data = _build_recordatorio_recipients(rows, cfg)
    res = await mail.send_campaign(cfg["subject"], _body_tpl(cfg), data["recipients"],
                                   dry_run=False, test_to=test_to)
    return {"stats": {"socios": len(data["recipients"]), "con_email": data["con_email"],
                      "sin_email": data["sin_email"]}, "result": res}


# ── Programador (chequeo diario) ────────────────────────────────────────────
async def tick() -> None:
    """Corre los jobs habilitados cuya última ejecución sea más vieja que `cada_dias`."""
    cfg = load_config()
    today = date.today()
    runners = {"resumen_interno": run_resumen, "recordatorio_socios": run_recordatorio}
    for job in JOBS:
        c = cfg[job]
        if not c.get("enabled"):
            continue
        last = cfg["_last_run"].get(job)
        if last:
            try:
                if (today - date.fromisoformat(last)).days < int(c["cada_dias"]):
                    continue
            except ValueError:
                pass
        try:
            await runners[job](c)
            _mark_run(job, today.isoformat())
            logger.info("Auto-mail '%s' ejecutado.", job)
        except Exception as exc:
            logger.error("Auto-mail '%s' falló: %s", job, exc)
