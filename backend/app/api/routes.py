"""Endpoints de la API. Todos los de datos requieren sesión (token Bearer)."""
from __future__ import annotations

import asyncio
import logging
from collections import Counter

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from ..auth import (
    authenticate,
    get_current_username,
    get_repository,
    logout,
    oauth2_scheme,
    _decode,
)
from .. import mail
from .. import auto_mail
from .. import agenda
from ..config import settings
from ..koha.client import KohaError
from ..koha.reports import KohaRepository
from ..schemas import LoginRequest, LoginResponse, MailSendRequest

logger = logging.getLogger("api")
router = APIRouter(prefix="/api")


# ── Auth ───────────────────────────────────────────────────────────────────
@router.post("/auth/login", response_model=LoginResponse, tags=["auth"])
async def login(body: LoginRequest):
    """Inicia sesión con las credenciales de Koha de la bibliotecaria."""
    return await authenticate(body.username, body.password)


@router.post("/auth/logout", tags=["auth"])
async def do_logout(token: str = Depends(oauth2_scheme)):
    sid = _decode(token).get("sid")
    if sid:
        await logout(sid)
    return {"ok": True}


@router.get("/me", tags=["auth"])
async def me(username: str = Depends(get_current_username)):
    return {"username": username}


# ── Préstamos ────────────────────────────────────────────────────────────────
@router.get("/loans/active", tags=["loans"])
async def loans_active(repo: KohaRepository = Depends(get_repository)):
    """Préstamos vigentes (todo lo que está prestado ahora)."""
    return await repo.active_loans()


@router.get("/loans/overdue", tags=["loans"])
async def loans_overdue(repo: KohaRepository = Depends(get_repository)):
    """Préstamos vencidos, con días de atraso y contacto del socio."""
    return await repo.overdue_loans()


@router.get("/loans/contact", tags=["loans"])
async def loans_contact(repo: KohaRepository = Depends(get_repository)):
    """Todos los préstamos vigentes con contacto y días respecto del vencimiento.

    dias_atraso > 0 → vencido; = 0 → vence hoy; < 0 → por vencer (faltan N días).
    """
    return await repo.loans_contact()


# ── Estadísticas ────────────────────────────────────────────────────────────
def _dias_int(r) -> int | None:
    try:
        return int(r.get("dias_atraso"))
    except (TypeError, ValueError):
        return None


@router.get("/stats", tags=["stats"])
async def stats(repo: KohaRepository = Depends(get_repository)):
    """KPIs calculados sobre los préstamos vigentes (reporte loans_contact)."""
    rows = await repo.loans_contact()
    total = len(rows)
    vencidos = [r for r in rows if (_dias_int(r) or 0) > 0]
    por_vencer = [r for r in rows if _dias_int(r) is not None and _dias_int(r) <= 0]
    socios = {r.get("cardnumber") for r in rows if r.get("cardnumber")}
    socios_venc = {r.get("cardnumber") for r in vencidos if r.get("cardnumber")}

    buckets = {"por_vencer": 0, "venc_1_30": 0, "venc_31_90": 0, "venc_90": 0}
    for r in rows:
        d = _dias_int(r)
        if d is None:
            continue
        if d <= 0:
            buckets["por_vencer"] += 1
        elif d <= 30:
            buckets["venc_1_30"] += 1
        elif d <= 90:
            buckets["venc_31_90"] += 1
        else:
            buckets["venc_90"] += 1

    titulos = Counter((r.get("title") or "(sin título)") for r in rows)
    top_titulos = [{"label": t, "count": c} for t, c in titulos.most_common(10)]

    por_socio: Counter = Counter()
    nombres: dict = {}
    for r in rows:
        c = r.get("cardnumber")
        if not c:
            continue
        por_socio[c] += 1
        nombres[c] = (f'{r.get("surname", "")}, {r.get("firstname", "")}').strip(", ")
    top_socios = [{"label": nombres.get(c, c), "count": n} for c, n in por_socio.most_common(10)]

    return {
        "total_vigentes": total,
        "vencidos": len(vencidos),
        "por_vencer": len(por_vencer),
        "pct_vencidos": round(100 * len(vencidos) / total, 1) if total else 0,
        "socios_con_prestamos": len(socios),
        "socios_con_vencidos": len(socios_venc),
        "buckets": buckets,
        "top_titulos": top_titulos,
        "top_socios": top_socios,
    }


_SQL_TOTALES = """
SELECT
  (SELECT COUNT(*) FROM items) AS ejemplares,
  (SELECT COUNT(DISTINCT biblionumber) FROM items) AS titulos,
  (SELECT COUNT(*) FROM items WHERE issues IS NULL OR issues = 0) AS sin_circular,
  (SELECT COUNT(*) FROM items WHERE itemlost > 0) AS perdidos,
  (SELECT COUNT(*) FROM items WHERE damaged > 0) AS danados,
  (SELECT COUNT(*) FROM items WHERE withdrawn > 0) AS retirados
""".strip()

_SQL_TIPOS = """
SELECT COALESCE(t.description, NULLIF(i.itype,''), '(sin tipo)') AS label, COUNT(*) AS count
FROM items i LEFT JOIN itemtypes t ON t.itemtype = i.itype
GROUP BY label ORDER BY count DESC LIMIT 12
""".strip()

_SQL_TOP_HIST = """
SELECT b.title AS label, i.issues AS count
FROM items i JOIN biblio b ON b.biblionumber = i.biblionumber
WHERE i.issues > 0 ORDER BY i.issues DESC LIMIT 10
""".strip()


def _num(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


@router.get("/stats/catalog", tags=["stats"])
async def stats_catalog(repo: KohaRepository = Depends(get_repository)):
    """KPIs del catálogo (ejemplares, títulos, circulación, tipos, colecciones)."""
    async def q(sql):
        try:
            return await repo.run_sql(sql)
        except Exception as exc:  # noqa: BLE001
            logger.warning("stats/catalog: consulta falló: %s", exc)
            return []

    totals = await q(_SQL_TOTALES)
    tipos = await q(_SQL_TIPOS)
    top = await q(_SQL_TOP_HIST)
    t = totals[0] if totals else {}
    ejemplares, titulos = _num(t.get("ejemplares")), _num(t.get("titulos"))
    sin_circular = _num(t.get("sin_circular"))

    def serie(rows):
        return [{"label": r.get("label") or "—", "count": _num(r.get("count"))} for r in rows]

    return {
        "ejemplares": ejemplares,
        "titulos": titulos,
        "sin_circular": sin_circular,
        "pct_sin_circular": round(100 * sin_circular / ejemplares, 1) if ejemplares else 0,
        "perdidos": _num(t.get("perdidos")),
        "danados": _num(t.get("danados")),
        "retirados": _num(t.get("retirados")),
        "por_tipo": serie(tipos),
        "top_historico": serie(top),
    }


@router.get("/stats/historico", tags=["stats"])
async def stats_historico(
    desde: str | None = Query(None, description="YYYY-MM-DD"),
    hasta: str | None = Query(None, description="YYYY-MM-DD"),
    repo: KohaRepository = Depends(get_repository),
):
    """Estadísticas de circulación (tabla statistics) en un rango de fechas."""
    import datetime as dt
    today = dt.date.today()
    try:
        d2 = dt.date.fromisoformat(hasta) if hasta else today
        d1 = dt.date.fromisoformat(desde) if desde else (d2 - dt.timedelta(days=365))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Fechas inválidas (YYYY-MM-DD).") from exc
    if d1 > d2:
        d1, d2 = d2, d1
    # Fechas re-serializadas desde objetos date -> seguras para interpolar.
    rango = f"s.datetime >= '{d1.isoformat()} 00:00:00' AND s.datetime <= '{d2.isoformat()} 23:59:59'"

    async def q(sql):
        try:
            return await repo.run_sql(sql)
        except Exception as exc:  # noqa: BLE001
            logger.warning("stats/historico: consulta falló: %s", exc)
            return []

    totales = await q(f"""
        SELECT SUM(s.type='issue') AS prestamos, SUM(s.type='return') AS devoluciones,
               SUM(s.type='renew') AS renovaciones,
               COUNT(DISTINCT CASE WHEN s.type='issue' THEN s.borrowernumber END) AS socios_activos
        FROM statistics s WHERE {rango}""")
    por_mes = await q(f"""
        SELECT DATE_FORMAT(s.datetime,'%Y-%m') AS label, COUNT(*) AS count
        FROM statistics s WHERE s.type='issue' AND {rango}
        GROUP BY label ORDER BY label""")
    top_titulos = await q(f"""
        SELECT b.title AS label, COUNT(*) AS count
        FROM statistics s JOIN items i ON i.itemnumber=s.itemnumber
        JOIN biblio b ON b.biblionumber=i.biblionumber
        WHERE s.type='issue' AND {rango}
        GROUP BY b.title ORDER BY count DESC LIMIT 10""")
    top_socios = await q(f"""
        SELECT CONCAT(br.surname, ', ', br.firstname) AS label, COUNT(*) AS count
        FROM statistics s JOIN borrowers br ON br.borrowernumber=s.borrowernumber
        WHERE s.type='issue' AND {rango}
        GROUP BY br.borrowernumber ORDER BY count DESC LIMIT 10""")

    t = totales[0] if totales else {}

    def serie(rows):
        return [{"label": r.get("label") or "—", "count": _num(r.get("count"))} for r in rows]

    return {
        "desde": d1.isoformat(), "hasta": d2.isoformat(),
        "prestamos": _num(t.get("prestamos")),
        "devoluciones": _num(t.get("devoluciones")),
        "renovaciones": _num(t.get("renovaciones")),
        "socios_activos": _num(t.get("socios_activos")),
        "por_mes": serie(por_mes),
        "top_titulos": serie(top_titulos),
        "top_socios": serie(top_socios),
    }


@router.get("/stats/estrategia", tags=["stats"])
async def stats_estrategia(repo: KohaRepository = Depends(get_repository)):
    """Panel estratégico: crecimiento, socios, estacionalidad y antigüedad del acervo."""
    async def q(sql):
        try:
            return await repo.run_sql(sql)
        except Exception as exc:  # noqa: BLE001
            logger.warning("stats/estrategia: consulta falló: %s", exc)
            return []

    def serie(rows):
        return [{"label": str(r.get("label") or "—"), "count": _num(r.get("count"))} for r in rows]

    prestamos_anio = await q("""SELECT YEAR(datetime) AS label, COUNT(*) AS count
        FROM statistics WHERE type='issue' AND datetime>='2013-01-01' GROUP BY label ORDER BY label""")
    socios_activos_anio = await q("""SELECT YEAR(datetime) AS label, COUNT(DISTINCT borrowernumber) AS count
        FROM statistics WHERE type='issue' AND datetime>='2013-01-01' GROUP BY label ORDER BY label""")
    socios_nuevos_anio = await q("""SELECT YEAR(dateenrolled) AS label, COUNT(*) AS count
        FROM borrowers WHERE dateenrolled IS NOT NULL GROUP BY label ORDER BY label""")
    estacionalidad = await q("""SELECT MONTH(datetime) AS label, COUNT(*) AS count
        FROM statistics WHERE type='issue' GROUP BY label ORDER BY label""")
    acervo_anio = await q("""SELECT YEAR(dateaccessioned) AS label, COUNT(*) AS count
        FROM items WHERE dateaccessioned IS NOT NULL AND YEAR(dateaccessioned) >= YEAR(CURDATE())-15
        GROUP BY label ORDER BY label""")
    socios_kpi = await q("""SELECT
        (SELECT COUNT(*) FROM borrowers) AS total,
        (SELECT COUNT(*) FROM borrowers b WHERE EXISTS (
            SELECT 1 FROM statistics s WHERE s.borrowernumber=b.borrowernumber
            AND s.type='issue' AND s.datetime >= NOW() - INTERVAL 1 YEAR)) AS activos12,
        (SELECT COUNT(*) FROM borrowers b WHERE NOT EXISTS (
            SELECT 1 FROM statistics s WHERE s.borrowernumber=b.borrowernumber AND s.type='issue')) AS nunca""")
    acervo_kpi = await q("""SELECT
        (SELECT COUNT(*) FROM items) AS total_items,
        SUM(dateaccessioned >= CURDATE() - INTERVAL 1 YEAR) AS nuevos12,
        SUM(dateaccessioned >= CURDATE() - INTERVAL 5 YEAR) AS ult5
        FROM items""")

    sk = socios_kpi[0] if socios_kpi else {}
    ak = acervo_kpi[0] if acervo_kpi else {}
    total, activos12, nunca = _num(sk.get("total")), _num(sk.get("activos12")), _num(sk.get("nunca"))
    total_items, ult5 = _num(ak.get("total_items")), _num(ak.get("ult5"))

    return {
        "prestamos_por_anio": serie(prestamos_anio),
        "socios_activos_por_anio": serie(socios_activos_anio),
        "socios_nuevos_por_anio": serie(socios_nuevos_anio),
        "estacionalidad": serie(estacionalidad),
        "acervo_por_anio": serie(acervo_anio),
        "socios": {"total": total, "activos_12m": activos12,
                   "dormidos": max(total - activos12 - nunca, 0), "nunca": nunca},
        "acervo": {"nuevos_12m": _num(ak.get("nuevos12")), "ult_5": ult5,
                   "mas_5": max(total_items - ult5, 0)},
    }


# ── Socios ────────────────────────────────────────────────────────────────────
@router.get("/members", tags=["members"])
async def members_search(
    q: str = Query(..., min_length=1, description="Apellido, nombre o número de carnet"),
    repo: KohaRepository = Depends(get_repository),
):
    """Busca socios por apellido/nombre/carnet."""
    return await repo.search_members(q)


@router.get("/members/{cardnumber}/loans", tags=["members"])
async def member_loans(cardnumber: str, repo: KohaRepository = Depends(get_repository)):
    """Préstamos vigentes de un socio puntual."""
    return await repo.member_loans(cardnumber)


@router.get("/members/{cardnumber}/profile", tags=["members"])
async def member_profile(cardnumber: str, repo: KohaRepository = Depends(get_repository)):
    """Ficha del socio: datos, préstamos vigentes e historial.

    Nota: lo relativo a pagos de cuotas (deuda / estado de cuenta) se gestiona en
    una planilla aparte; será un módulo separado (ver docs/ISSUE-modulo-pagos.md).
    """
    profile, loans, history = await asyncio.gather(
        repo.member_profile(cardnumber),
        repo.member_loans(cardnumber),
        repo.member_history(cardnumber),
    )
    socio = profile[0] if profile else None
    if socio is None:
        raise HTTPException(status_code=404, detail="Socio no encontrado.")

    return {
        "socio": socio,
        "prestamos_vigentes": loans,
        "historial": history,
    }


# ── Mails ─────────────────────────────────────────────────────────────────────
@router.get("/mail/config", tags=["mail"])
async def mail_config(_: str = Depends(get_current_username)):
    """Estado de la configuración de mail (sin exponer credenciales)."""
    return {
        "configured": bool(settings.smtp_host),
        "from": settings.smtp_from or settings.smtp_user,
        "from_name": settings.smtp_from_name,
        "dry_run_default": settings.mail_dry_run,
    }


@router.post("/mail/send", tags=["mail"])
async def mail_send(body: MailSendRequest, _: str = Depends(get_current_username)):
    """Envía (o simula) una campaña de mail a los destinatarios seleccionados.

    Variables de combinación en asunto/cuerpo: {{nombre}}, {{apellido}}, {{carnet}},
    {{email}} (y las que se pasen por destinatario). Cada socio puede personalizarse.
    """
    dry_run = settings.mail_dry_run if body.dry_run is None else body.dry_run
    recipients = [r.model_dump() for r in body.recipients]
    try:
        return await mail.send_campaign(
            subject_tpl=body.subject,
            body_tpl=body.body,
            recipients=recipients,
            dry_run=dry_run,
            test_to=body.test_to,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── Agenda de actividades (Google Calendar, solo lectura) ──────────────────────
@router.get("/agenda", tags=["agenda"])
async def agenda_events(
    desde: str | None = Query(None, description="YYYY-MM-DD (por defecto hoy)"),
    dias: int = Query(90, ge=1, le=400),
    _: str = Depends(get_current_username),
):
    """Eventos del calendario de la biblioteca, desde 'desde' por 'dias' días."""
    import datetime as _dt
    if not agenda.configured():
        return {"configured": False, "events": []}
    try:
        d1 = _dt.date.fromisoformat(desde) if desde else _dt.date.today()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Fecha inválida (YYYY-MM-DD).") from exc
    d2 = d1 + _dt.timedelta(days=dias)
    try:
        evs = await agenda.events(d1, d2)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"No se pudo leer el calendario: {exc}") from exc
    return {"configured": True, "desde": d1.isoformat(), "hasta": d2.isoformat(),
            "calendarios": agenda.calendars(), "events": evs}


# ── Envíos automáticos ─────────────────────────────────────────────────────────
@router.get("/auto/config", tags=["auto"])
async def auto_config_get(_: str = Depends(get_current_username)):
    """Configuración actual de los dos envíos automáticos + última ejecución."""
    return auto_mail.load_config()


@router.put("/auto/config", tags=["auto"])
async def auto_config_put(partial: dict = Body(...), _: str = Depends(get_current_username)):
    """Guarda (merge) la configuración de uno o ambos jobs."""
    return auto_mail.save_config(partial)


@router.get("/auto/preview/{job}", tags=["auto"])
async def auto_preview(job: str, _: str = Depends(get_current_username)):
    """Vista previa de lo que se enviaría (sin enviar nada)."""
    cfg = auto_mail.load_config()
    if job not in auto_mail.JOBS:
        raise HTTPException(status_code=404, detail="Job desconocido.")
    try:
        if job == "resumen_interno":
            return await auto_mail.build_resumen(cfg[job])
        return await auto_mail.build_recordatorio(cfg[job])
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/auto/run/{job}", tags=["auto"])
async def auto_run(job: str, body: dict = Body(default={}), _: str = Depends(get_current_username)):
    """Ejecuta un job ahora. Si se pasa test_to, manda todo a esa dirección de prueba."""
    cfg = auto_mail.load_config()
    if job not in auto_mail.JOBS:
        raise HTTPException(status_code=404, detail="Job desconocido.")
    test_to = (body or {}).get("test_to") or None
    try:
        if job == "resumen_interno":
            return await auto_mail.run_resumen(cfg[job], test_to=test_to)
        return await auto_mail.run_recordatorio(cfg[job], test_to=test_to)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
