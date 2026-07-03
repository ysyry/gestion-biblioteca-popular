"""Envíos automáticos de la biblioteca — sistema de reportes configurables.

Hay una LISTA de reportes; cada uno se configura por separado (en tabs en la UI).
Un reporte tiene:
  - tipo: "interno" (un mail a una dirección) o "socios" (uno por socio).
  - contenido combinable: vencidos, por vencer, deuda de cuota.
  - cada_dias: frecuencia. dias_antes: ventana de "por vencer".
  - asunto / cuerpo / pie propios. Para "socios": lista de excluidos.

Los jobs corren SIN nadie logueado: usan las credenciales de SERVICIO de Koha.
La planilla de cuotas se lee con la cuenta de servicio de Google (módulo cuotas).
El programador (APScheduler) chequea 1 vez por día y dispara según `cada_dias`.
La config se persiste con el módulo storage (Postgres o archivo).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone

from . import cuotas
from . import mail
from . import storage
from .config import settings
from .koha.client import KohaClient
from .koha.reports import KohaRepository

logger = logging.getLogger("auto_mail")

CONFIG_KEY = "auto_mail"
_CONFIG_VER = 1   # versión del esquema; sube 1 por cada migración de datos guardados


def _default_reports() -> list[dict]:
    return [
        {
            "id": "resumen", "nombre": "Resumen interno", "tipo": "interno", "enabled": False,
            "cada_dias": 7, "dias_antes": 7, "to": "", "umbral_atraso": 1,
            "incluir_vencidos": True, "incluir_por_vencer": True, "incluir_cuotas": False, "umbral_cuota": 1,
            "subject": "Resumen de préstamos — Biblioteca Osvaldo Bayer",
            "body": ("Resumen automático al {{fecha}}.\n\n"
                     "VENCIDOS ({{total_vencidos}}):\n{{lista_vencidos}}\n\n"
                     "EN PRÉSTAMO (vencen en los próximos {{dias_antes}} días) ({{total_por_vencer}}):\n"
                     "{{lista_por_vencer}}\n\n"
                     "Socios con préstamos vencidos: {{total_socios_deben}}."),
            "footer": "— Sistema de gestión · Biblioteca Popular Osvaldo Bayer",
        },
        {
            "id": "socios", "nombre": "Recordatorio a socios", "tipo": "socios", "enabled": False,
            "cada_dias": 7, "dias_antes": 3,
            "incluir_vencidos": True, "incluir_por_vencer": True, "incluir_cuotas": False,
            "umbral_atraso": 1, "umbral_cuota": 1, "excluidos": [],
            "subject": "Tus préstamos en la Biblioteca Osvaldo Bayer",
            "body": ("Hola {{nombre}},\n\n"
                     "Te recordamos tus préstamos en la Biblioteca Popular Osvaldo Bayer.\n\n"
                     "Vencidos ({{cantidad_vencidos}}):\n{{vencidos}}\n\n"
                     "En préstamo ({{cantidad_por_vencer}}):\n{{por_vencer}}"),
            "footer": "Te esperamos para renovarlos o devolverlos. ¡Gracias!\nBiblioteca Popular Osvaldo Bayer.",
        },
    ]


# ── Config (lista de reportes) ──────────────────────────────────────────────
def load_config() -> dict:
    data = storage.get(CONFIG_KEY) or {}
    reports = data.get("reports")
    existing = bool(reports)   # True si ya había una config guardada (para migrar datos reales)
    if not reports:
        # Migración del formato viejo (dos jobs fijos) o primer arranque.
        reports = _default_reports()
        if data.get("resumen_interno") or data.get("recordatorio_socios"):
            mapa = {"resumen": "resumen_interno", "socios": "recordatorio_socios"}
            for rep in reports:
                old = data.get(mapa.get(rep["id"], ""), {})
                rep.update({k: v for k, v in old.items() if k in rep})
            existing = True
    cfg = {"reports": reports, "_last_run": data.get("_last_run") or {}}

    # Migraciones de datos guardados (idempotentes, se corren una sola vez por versión).
    ver = data.get("_ver", 0)
    if ver < _CONFIG_VER:
        if existing:
            _migrate_v1(cfg)
        cfg["_ver"] = _CONFIG_VER
        _save(cfg)
    else:
        cfg["_ver"] = ver
    return cfg


def _lectura_report(reports: list[dict]) -> dict:
    """Reporte a socios para hacer seguimiento de lectura (listando lo que tienen en préstamo)."""
    base = next(r for r in _default_reports() if r["tipo"] == "socios")
    return {
        **base,
        "id": _new_id(reports),
        "nombre": "Lectura",
        "enabled": False,
        "cada_dias": 30,
        "dias_antes": 90,   # ventana amplia: "en préstamo" abarca todo lo que tienen prestado
        "incluir_vencidos": True,
        "incluir_por_vencer": True,
        "incluir_cuotas": False,
        "subject": "¿Cómo vas con la lectura? — Biblioteca Osvaldo Bayer",
        "body": ("Hola {{nombre}}, este es un mensaje automático para saber cómo vas con la "
                 "lectura. ¿Te está gustando? ¿Te falta poco? Acordate que si no te atrapó "
                 "podés venir a cambiar de autor, de género, de tema. Hay mucho y estamos "
                 "para acompañarte. Escribinos al wp y contanos.\n\n"
                 "Nos aparece que tenés estos libros en préstamo, ¿es correcto?\n"
                 "{{por_vencer}}"),
        "footer": "",
    }


def _migrate_v1(cfg: dict) -> None:
    """v1: renombra 'por vencer' → 'en préstamo' en los reportes existentes y agrega 'Lectura'."""
    swaps = {
        "POR VENCER en los próximos {{dias_antes}} días ({{total_por_vencer}}):":
            "EN PRÉSTAMO (vencen en los próximos {{dias_antes}} días) ({{total_por_vencer}}):",
        "Por vencer ({{cantidad_por_vencer}}):":
            "En préstamo ({{cantidad_por_vencer}}):",
    }
    for r in cfg["reports"]:
        b = r.get("body") or ""
        for old, new in swaps.items():
            b = b.replace(old, new)   # solo cambia si el fragmento exacto está presente
        r["body"] = b
    if not any((r.get("nombre") or "").strip().lower() == "lectura" for r in cfg["reports"]):
        cfg["reports"].append(_lectura_report(cfg["reports"]))


def _save(cfg: dict) -> None:
    storage.set(CONFIG_KEY, cfg)


def get_report(rid: str) -> dict | None:
    return next((r for r in load_config()["reports"] if r["id"] == rid), None)


def add_report(tipo: str, nombre: str) -> dict:
    cfg = load_config()
    base = next(r for r in _default_reports() if r["tipo"] == (tipo if tipo in ("interno", "socios") else "interno"))
    rep = {**base, "id": _new_id(cfg["reports"]), "nombre": nombre or base["nombre"], "enabled": False}
    cfg["reports"].append(rep)
    _save(cfg)
    return rep


def update_report(rid: str, partial: dict) -> dict:
    cfg = load_config()
    for r in cfg["reports"]:
        if r["id"] == rid:
            r.update({k: v for k, v in partial.items() if k != "id"})
    _save(cfg)
    return cfg


def delete_report(rid: str) -> dict:
    cfg = load_config()
    cfg["reports"] = [r for r in cfg["reports"] if r["id"] != rid]
    cfg["_last_run"].pop(rid, None)
    _save(cfg)
    return cfg


def _new_id(reports: list[dict]) -> str:
    n = 1
    ids = {r["id"] for r in reports}
    while f"r{n}" in ids:
        n += 1
    return f"r{n}"


def _mark_run(rid: str, when: str) -> None:
    cfg = load_config()
    cfg["_last_run"][rid] = when
    _save(cfg)


# ── Historial de envíos ─────────────────────────────────────────────────────
HISTORY_KEY = "auto_mail_history"
_HISTORY_MAX = 200   # tope global de ejecuciones guardadas (las más nuevas primero)


def add_history(entry: dict) -> None:
    hist = storage.get(HISTORY_KEY) or []
    hist.insert(0, entry)
    storage.set(HISTORY_KEY, hist[:_HISTORY_MAX])


def get_history(rep_id: str | None = None, limit: int = 20) -> list[dict]:
    hist = storage.get(HISTORY_KEY) or []
    if rep_id:
        hist = [h for h in hist if h.get("report_id") == rep_id]
    return hist[:limit]


def _record_history(rep: dict, out: dict | None, trigger: str,
                    test_to: str | None, error: str | None = None) -> None:
    """Guarda una ejecución en el historial: fecha/hora, disparador y a quién se envió."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "report_id": rep.get("id"), "report_name": rep.get("nombre"), "tipo": rep.get("tipo"),
        "trigger": trigger, "test_to": test_to or None,
    }
    if error is not None:
        entry.update({"ok": False, "error": error, "total": 0, "enviados": 0, "destinatarios": []})
    else:
        res = (out or {}).get("result") or {}
        dests = [{"email": r.get("email"), "status": r.get("status"),
                  "nombre": r.get("nombre", ""), "detail": r.get("detail", "")}
                 for r in res.get("resultados", [])]
        entry.update({"ok": True, "total": res.get("total", len(dests)),
                      "enviados": res.get("enviados", 0), "destinatarios": dests})
    try:
        add_history(entry)
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo guardar el historial de envío: %s", exc)


# ── Datos (Koha + cuotas, con credenciales de servicio) ─────────────────────
def _norm(x) -> str:
    x = str(x or "").strip()
    return x.lstrip("0") or x


async def _all_loans() -> list[dict]:
    if not settings.koha_user or not settings.koha_password:
        raise RuntimeError("Faltan credenciales de servicio de Koha (KOHA_USER/KOHA_PASSWORD).")
    client = KohaClient(settings.koha_base_url, settings.koha_user, settings.koha_password)
    await client.login()
    try:
        return await KohaRepository(client).loans_contact()
    finally:
        await client.aclose()


BAJA_CATS = {"B"}        # categoría "de baja" en Koha
NO_CUOTA_CATS = {"BEC."}  # becados: no pagan cuota


async def _members_map() -> dict:
    """{carnet_norm: {surname, firstname, email, categorycode}} — contacto + categoría."""
    client = KohaClient(settings.koha_base_url, settings.koha_user, settings.koha_password)
    await client.login()
    try:
        rows = await client.run_sql(
            "SELECT cardnumber, surname, firstname, "
            "COALESCE(NULLIF(TRIM(email),''), NULLIF(TRIM(emailpro),''), NULLIF(TRIM(B_email),'')) AS email, "
            "categorycode FROM borrowers")
    finally:
        await client.aclose()
    return {_norm(r["cardnumber"]): r for r in rows if r.get("cardnumber")}


def _cat(members: dict, carnet: str) -> str:
    return (members.get(carnet, {}).get("categorycode") or "").strip()


async def _cuota_map() -> dict:
    if not cuotas.configured():
        return {}
    data = await asyncio.to_thread(cuotas.estado_cuotas, max(cuotas.anios_disponibles()))
    return {_norm(s["matricula"]): s for s in data["socios"] if s.get("matricula")}


def _d(s) -> str:
    """Fecha para mostrar en los mails, en formato argentino DD/MM/AAAA."""
    s = (s or "")[:10]
    try:
        y, m, d = s.split("-")
        return f"{d}/{m}/{y}"
    except ValueError:
        return s


def _dias(row) -> int | None:
    try:
        return int(row.get("dias_atraso"))
    except (TypeError, ValueError):
        return None


def _titulo(l):
    return l.get("title") or l.get("barcode") or "(sin título)"


def _split_loans(rows, dias_antes, umbral_atraso=1):
    venc, porv = [], []
    for r in rows:
        d = _dias(r)
        if d is None:
            continue
        if d >= umbral_atraso and d > 0:
            venc.append(r)
        elif -dias_antes <= d <= 0:
            porv.append(r)
    venc.sort(key=lambda r: -(_dias(r) or 0))
    porv.sort(key=lambda r: (_dias(r) or 0))
    return venc, porv


# ── Reporte INTERNO (a una dirección) ───────────────────────────────────────
async def build_interno(rep: dict) -> dict:
    rows = await _all_loans()
    dias_antes = int(rep.get("dias_antes", 7))
    umbral_atraso = int(rep.get("umbral_atraso", 1))   # vencidos desde N días de atraso
    venc, porv = _split_loans(rows, dias_antes, umbral_atraso)

    def nom(r):
        return f'{r.get("surname","")}, {r.get("firstname","")}'.strip(", ")

    def car(r):
        return r.get("cardnumber") or "—"

    vars = {
        "fecha": date.today().isoformat(),
        "dias_antes": str(dias_antes),
        "total_vencidos": str(len(venc)),
        "total_por_vencer": str(len(porv)),
        "total_socios_deben": str(len({r.get("cardnumber") for r in venc if r.get("cardnumber")})),
        "lista_vencidos": "\n".join(f"• N° {car(r)} — {nom(r)} — {_titulo(r)} (venció {_d(r.get('date_due'))}, {_dias(r)} días)" for r in venc) or "ninguno",
        "lista_por_vencer": "\n".join(f"• N° {car(r)} — {nom(r)} — {_titulo(r)} (vence {_d(r.get('date_due'))})" for r in porv) or "ninguno",
        "total_deudores_cuota": "0", "lista_cuotas": "ninguno",
    }
    html_blocks = {
        "lista_vencidos": mail.html_table(["N° socio", "Socio", "Libro", "Vencimiento", "Atraso"],
                                          [[car(r), nom(r), _titulo(r), _d(r.get("date_due")), f"{_dias(r)} días"] for r in venc]),
        "lista_por_vencer": mail.html_table(["N° socio", "Socio", "Libro", "Vencimiento"],
                                            [[car(r), nom(r), _titulo(r), _d(r.get("date_due"))] for r in porv]),
    }
    stats = {"vencidos": len(venc), "por_vencer": len(porv)}

    if rep.get("incluir_cuotas"):
        umbral = int(rep.get("umbral_cuota", 1))
        cm = await _cuota_map()
        members = await _members_map()
        # Excluir bajas y becados (no pagan cuota) de la lista de deudores.
        omit = {c for c, m in members.items()
                if (m.get("categorycode") or "").strip() in (BAJA_CATS | NO_CUOTA_CATS)}
        deud = [s for c, s in cm.items() if (s.get("debe", 0) or 0) >= umbral and c not in omit]
        deud.sort(key=lambda s: -s.get("debe", 0))
        vars["total_deudores_cuota"] = str(len(deud))
        vars["lista_cuotas"] = "\n".join(
            f"• {s['apellido']}, {s['nombre']} (mat. {s['matricula']}) — debe {s['debe']}: {', '.join(s.get('impagos', []))}"
            for s in deud) or "ninguno"
        html_blocks["lista_cuotas"] = mail.html_table(
            ["Socio", "Matrícula", "Debe", "Meses"],
            [[f"{s['apellido']}, {s['nombre']}", s["matricula"], f"{s['debe']} mes(es)", ", ".join(s.get("impagos", []))] for s in deud])
        stats["deudores_cuota"] = len(deud)

    footer = ("\n\n" + rep["footer"]) if rep.get("footer") else ""
    return {
        "to": (rep.get("to") or "").strip() or settings.smtp_from or settings.smtp_user,
        "subject_tpl": rep["subject"], "body_tpl": rep["body"] + footer,
        "vars": vars, "html": html_blocks, "stats": stats,
        "subject": mail.render(rep["subject"], vars),
        "body": mail.render(rep["body"], vars) + footer,
    }


async def run_interno(rep: dict, test_to: str | None = None) -> dict:
    data = await build_interno(rep)
    to = test_to or data["to"]
    if not to:
        raise RuntimeError("No hay dirección destino (configurá 'A qué correo' o SMTP_FROM).")
    res = await mail.send_campaign(
        data["subject_tpl"], data["body_tpl"],
        [{"email": to, "vars": data["vars"], "subject": None, "body": None, "html": data["html"]}],
        dry_run=False, test_to=None,
    )
    return {"sent_to": to, "stats": data["stats"], "result": res}


# ── Reporte A SOCIOS (uno por socio) ────────────────────────────────────────
async def _socios_recipients(rep: dict) -> dict:
    dias_antes = int(rep.get("dias_antes", 3))
    umbral_atraso = int(rep.get("umbral_atraso", 1))
    inc_v = bool(rep.get("incluir_vencidos"))
    inc_p = bool(rep.get("incluir_por_vencer"))
    inc_c = bool(rep.get("incluir_cuotas"))
    umbral_cuota = int(rep.get("umbral_cuota", 1))
    excluidos = {_norm(x) for x in (rep.get("excluidos") or [])}

    rows = await _all_loans()
    by: dict[str, dict] = {}
    for r in rows:
        c = r.get("cardnumber")
        if not c:
            continue
        by.setdefault(_norm(c), {"info": r, "loans": []})["loans"].append(r)

    cm = await _cuota_map() if inc_c else {}
    members = await _members_map()                 # siempre: para filtrar bajas
    bajas = {c for c in members if _cat(members, c) in BAJA_CATS}

    carnets = set()
    for c, g in by.items():
        venc = [l for l in g["loans"] if (_dias(l) or 0) >= umbral_atraso and (_dias(l) or 0) > 0]
        porv = [l for l in g["loans"] if _dias(l) is not None and -dias_antes <= _dias(l) <= 0]
        if (inc_v and venc) or (inc_p and porv):
            carnets.add(c)
    if inc_c:
        for c, s in cm.items():
            if (s.get("debe", 0) or 0) >= umbral_cuota and _cat(members, c) not in NO_CUOTA_CATS:
                carnets.add(c)

    recipients = []
    for c in carnets:
        if c in excluidos or c in bajas:           # no escribir a socios de baja
            continue
        g = by.get(c, {"info": {}, "loans": []})
        info = g["info"]
        m = members.get(c, {})
        nombre = info.get("firstname") or m.get("firstname") or ""
        apellido = info.get("surname") or m.get("surname") or ""
        email = info.get("email") or m.get("email") or None
        carnet = info.get("cardnumber") or (cm.get(c, {}).get("matricula")) or c
        venc = [l for l in g["loans"] if (_dias(l) or 0) >= umbral_atraso and (_dias(l) or 0) > 0]
        porv = [l for l in g["loans"] if _dias(l) is not None and -dias_antes <= _dias(l) <= 0]
        s = cm.get(c, {})
        recipients.append({
            "email": email,
            "vars": {
                "nombre": nombre, "apellido": apellido, "carnet": carnet,
                "vencidos": "\n".join(f"• {_titulo(l)} (venció {_d(l.get('date_due'))})" for l in venc) or "ninguno",
                "por_vencer": "\n".join(f"• {_titulo(l)} (vence {_d(l.get('date_due'))})" for l in porv) or "ninguno",
                "cantidad_vencidos": str(len(venc)), "cantidad_por_vencer": str(len(porv)),
                "meses_debe": str(s.get("debe", 0)),
                "meses_impagos": ", ".join(s.get("impagos", [])) or "—",
            },
            "html": {
                "vencidos": mail.libros_table([{"titulo": _titulo(l), "fecha": _d(l.get("date_due"))} for l in venc]),
                "por_vencer": mail.libros_table([{"titulo": _titulo(l), "fecha": _d(l.get("date_due"))} for l in porv]),
            },
            "subject": None, "body": None,
            "_carnet": carnet, "_vencidos": len(venc), "_porvencer": len(porv), "_debe": s.get("debe", 0),
        })
    recipients.sort(key=lambda r: (r["vars"]["apellido"], r["vars"]["nombre"]))
    con_email = sum(1 for r in recipients if r["email"])
    return {"recipients": recipients, "con_email": con_email, "sin_email": len(recipients) - con_email}


def _body_tpl(rep: dict) -> str:
    return rep["body"] + (("\n\n" + rep["footer"]) if rep.get("footer") else "")


async def build_socios(rep: dict) -> dict:
    data = await _socios_recipients(rep)
    recs = data["recipients"]
    sample = None
    if recs:
        r0 = recs[0]
        sample = {"subject": mail.render(rep["subject"], r0["vars"]),
                  "body": mail.render(_body_tpl(rep), r0["vars"]), "email": r0["email"]}
    destinatarios = [{"carnet": r["_carnet"], "nombre": r["vars"]["nombre"], "apellido": r["vars"]["apellido"],
                      "email": r["email"] or "", "vencidos": r["_vencidos"], "por_vencer": r["_porvencer"],
                      "debe": r["_debe"]} for r in recs]
    return {"total": len(recs), "con_email": data["con_email"], "sin_email": data["sin_email"],
            "sample": sample, "destinatarios": destinatarios}


async def run_socios(rep: dict, test_to: str | None = None) -> dict:
    data = await _socios_recipients(rep)
    recs = data["recipients"]
    if test_to:
        recs = recs[:1]   # prueba: una sola muestra
    res = await mail.send_campaign(rep["subject"], _body_tpl(rep), recs, dry_run=False, test_to=test_to)
    # Completa el nombre en cada resultado (para el historial "a quién se envió").
    name_by = {(r.get("email") or ""): (r.get("vars") or {}).get("nombre", "") for r in recs}
    for x in res.get("resultados", []):
        if not x.get("nombre"):
            x["nombre"] = name_by.get(x.get("email") or "", "")
    return {"stats": {"socios": len(data["recipients"]), "con_email": data["con_email"],
                      "sin_email": data["sin_email"], "prueba": bool(test_to)}, "result": res}


# ── Dispatch ────────────────────────────────────────────────────────────────
async def preview_report(rep: dict) -> dict:
    return await (build_interno(rep) if rep["tipo"] == "interno" else build_socios(rep))


async def run_report(rep: dict, test_to: str | None = None, trigger: str = "manual") -> dict:
    trig = "prueba" if test_to else trigger
    try:
        out = await (run_interno(rep, test_to) if rep["tipo"] == "interno" else run_socios(rep, test_to))
    except Exception as exc:
        _record_history(rep, None, trig, test_to, error=str(exc))
        raise
    _record_history(rep, out, trig, test_to)
    return out


# ── Programador (chequeo diario) ────────────────────────────────────────────
async def tick() -> None:
    cfg = load_config()
    today = date.today()
    for rep in cfg["reports"]:
        if not rep.get("enabled"):
            continue
        last = cfg["_last_run"].get(rep["id"])
        if last:
            try:
                if (today - date.fromisoformat(last)).days < int(rep["cada_dias"]):
                    continue
            except ValueError:
                pass
        try:
            await run_report(rep, trigger="auto")
            _mark_run(rep["id"], today.isoformat())
            logger.info("Reporte automático '%s' ejecutado.", rep.get("nombre"))
        except Exception as exc:  # noqa: BLE001
            logger.error("Reporte '%s' falló: %s", rep.get("nombre"), exc)
