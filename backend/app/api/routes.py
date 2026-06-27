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
from .. import cuotas
from .. import cache
from ..config import settings

# TTL (segundos) por tipo de dato. Lo que cambia rápido, caché corto.
TTL_LOANS = 120        # préstamos vigentes (cambian durante el día)
TTL_CATALOG = 3600     # catálogo (cambia día a día)
TTL_HEAVY = 1800       # estrategia / histórico (datos históricos)
TTL_CRUCE = 900        # cruce Koha↔planilla
TTL_AGENDA = 1800      # agenda (eventos cambian lento)


async def _loans_contact_cached(repo: KohaRepository, fresh: bool = False):
    """Préstamos vigentes con caché compartido (lo usan préstamos, stats y cruce)."""
    if fresh:
        cache.invalidate("loans_contact")
    return await cache.cached("loans_contact", TTL_LOANS, repo.loans_contact)
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
async def loans_contact(fresh: bool = Query(False), repo: KohaRepository = Depends(get_repository)):
    """Todos los préstamos vigentes con contacto y días respecto del vencimiento.

    dias_atraso > 0 → vencido; = 0 → vence hoy; < 0 → por vencer (faltan N días).
    """
    return await _loans_contact_cached(repo, fresh)


# ── Estadísticas ────────────────────────────────────────────────────────────
def _dias_int(r) -> int | None:
    try:
        return int(r.get("dias_atraso"))
    except (TypeError, ValueError):
        return None


@router.get("/stats", tags=["stats"])
async def stats(fresh: bool = Query(False), repo: KohaRepository = Depends(get_repository)):
    """KPIs calculados sobre los préstamos vigentes (reporte loans_contact)."""
    rows = await _loans_contact_cached(repo, fresh)
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
async def stats_catalog(fresh: bool = Query(False), repo: KohaRepository = Depends(get_repository)):
    """KPIs del catálogo (ejemplares, títulos, circulación, tipos, colecciones)."""
    if fresh:
        cache.invalidate("stats_catalog")
    return await cache.cached("stats_catalog", TTL_CATALOG, lambda: _stats_catalog(repo))


async def _stats_catalog(repo: KohaRepository):
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
    fresh: bool = Query(False),
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
    key = f"hist:{d1.isoformat()}:{d2.isoformat()}"
    if fresh:
        cache.invalidate(key)
    return await cache.cached(key, TTL_HEAVY, lambda: _stats_historico(repo, d1, d2))


async def _stats_historico(repo: KohaRepository, d1, d2):
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
async def stats_estrategia(fresh: bool = Query(False), repo: KohaRepository = Depends(get_repository)):
    """Panel estratégico: crecimiento, socios, estacionalidad y antigüedad del acervo."""
    if fresh:
        cache.invalidate("estrategia")
    return await cache.cached("estrategia", TTL_HEAVY, lambda: _stats_estrategia(repo))


async def _stats_estrategia(repo: KohaRepository):
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

    # Enriquece con la deuda de cuota por carnet, así {{meses_debe}}/{{meses_impagos}}
    # funcionan aunque el socio se haya agregado por búsqueda (no solo desde el cruce).
    usa_cuota = "{{meses_debe}}" in (body.body or "") or "{{meses_impagos}}" in (body.body or "")
    if cuotas.configured() and usa_cuota:
        try:
            import asyncio
            data = await asyncio.to_thread(cuotas.estado_cuotas, max(cuotas.anios_disponibles()))
            cmap = {_norm_id(s["matricula"]): s for s in data["socios"] if s.get("matricula")}
            for r in recipients:
                v = r.get("vars") or {}
                s = cmap.get(_norm_id(v.get("carnet")))
                if s:
                    v["meses_debe"] = str(s.get("debe", 0))
                    v["meses_impagos"] = ", ".join(s.get("impagos", [])) or "—"
                    r["vars"] = v
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo enriquecer cuotas en mail: %s", exc)

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
    except Exception as exc:  # cualquier otro error: 502 con mensaje, nunca 500 crudo
        logger.exception("mail/send falló")
        raise HTTPException(status_code=502, detail=f"Error al enviar: {exc}") from exc


# ── Agenda de actividades (Google Calendar, solo lectura) ──────────────────────
@router.get("/agenda", tags=["agenda"])
async def agenda_events(
    desde: str | None = Query(None, description="YYYY-MM-DD (por defecto hoy)"),
    dias: int = Query(90, ge=1, le=400),
    fresh: bool = Query(False),
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
    key = f"agenda:{d1.isoformat()}:{d2.isoformat()}"
    if fresh:
        cache.invalidate(key)
    try:
        evs = await cache.cached(key, TTL_AGENDA, lambda: agenda.events(d1, d2))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"No se pudo leer el calendario: {exc}") from exc
    return {"configured": True, "desde": d1.isoformat(), "hasta": d2.isoformat(),
            "calendarios": agenda.calendars(), "events": evs}


# ── Cuotas societarias (planilla de Google, solo lectura) ──────────────────────
@router.get("/cuotas", tags=["cuotas"])
async def cuotas_estado(
    anio: int = Query(None, description="Año (por defecto el más reciente)"),
    fresh: bool = Query(False),
    _: str = Depends(get_current_username),
):
    """Estado de cuotas de todos los socios para un año."""
    if not cuotas.configured():
        return {"configured": False}
    import asyncio
    if fresh:
        cuotas.clear_cache()
        cache.invalidate("cruce_members")  # el cruce depende de la planilla
    try:
        data = await asyncio.to_thread(cuotas.estado_cuotas, anio or max(cuotas.anios_disponibles()))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"No se pudo leer la planilla: {exc}") from exc
    data["configured"] = True
    return data


# ── Cruce de datos: Koha (préstamos) vs planilla (cuotas) ──────────────────────
def _norm_id(x) -> str:
    x = str(x or "").strip()
    return x.lstrip("0") or x


@router.get("/cruce", tags=["cuotas"])
async def cruce(fresh: bool = Query(False), repo: KohaRepository = Depends(get_repository),
                _: str = Depends(get_current_username)):
    """Cruza socios de Koha (actividad de préstamo) con la planilla de cuotas (matrícula=carnet)."""
    if not cuotas.configured():
        return {"configured": False}
    import asyncio
    sql = """SELECT br.cardnumber, br.surname, br.firstname, br.email, br.categorycode,
      c.description AS categoria,
      (SELECT COUNT(*) FROM statistics s WHERE s.borrowernumber=br.borrowernumber
        AND s.type='issue' AND s.datetime >= NOW() - INTERVAL 1 YEAR) AS l12
      FROM borrowers br LEFT JOIN categories c ON c.categorycode = br.categorycode"""
    if fresh:
        cache.invalidate("cruce_members")
    koha = await cache.cached("cruce_members", TTL_CRUCE, lambda: repo.run_sql(sql))
    data = await asyncio.to_thread(cuotas.estado_cuotas, max(cuotas.anios_disponibles()))

    # Socios "de baja" en Koha (categoría B). No cuentan ni reciben recordatorios.
    BAJA = {"B"}
    koha_by = {_norm_id(r["cardnumber"]): r for r in koha
               if r.get("cardnumber") and (r.get("categorycode") or "").strip() not in BAJA}
    pl_by = {_norm_id(s["matricula"]): s for s in data["socios"] if s.get("matricula")}
    ks, ps = set(koha_by), set(pl_by)
    inter = ks & ps

    def retira(card):
        r = koha_by.get(card)
        try:
            return r is not None and int(r["l12"]) > 0
        except (TypeError, ValueError):
            return False

    NO_CUOTA = {"BEC."}  # becados no pagan cuota → nunca figuran como deudores

    def info(card, s=None):
        k = koha_by.get(card, {})
        return {"carnet": k.get("cardnumber") or (s["matricula"] if s else ""),
                "apellido": k.get("surname") or (s["apellido"] if s else ""),
                "nombre": k.get("firstname") or (s["nombre"] if s else ""),
                "email": k.get("email") or "",
                "categoria": k.get("categoria") or (k.get("categorycode") or "")}

    # Lista completa de socios que coinciden, con meses adeudados y si retira.
    # El frontend arma la matriz/listas aplicando un umbral configurable de meses.
    matched = []
    for m in inter:
        k = koha_by[m]
        becado = (k.get("categorycode") or "").strip() in NO_CUOTA
        d = info(m, pl_by[m])
        d["debe"] = 0 if becado else pl_by[m].get("debe", 0)
        d["impagos"] = [] if becado else pl_by[m].get("impagos", [])
        d["retira"] = retira(m)
        matched.append(d)

    return {
        "configured": True,
        "anio": data["anio"],
        "koha_total": len(koha_by),
        "planilla_total": len(pl_by),
        "coinciden": len(inter),
        "solo_koha": len(ks - ps),
        "solo_planilla": len(ps - ks),
        "matched": matched,
        "retiran_sin_planilla": [info(k) for k in (ks - ps) if retira(k)],
    }


# ── Envíos automáticos (lista de reportes configurables) ───────────────────────
@router.get("/auto/config", tags=["auto"])
async def auto_config_get(_: str = Depends(get_current_username)):
    """Lista de reportes automáticos + última ejecución de cada uno."""
    return auto_mail.load_config()


@router.post("/auto/report", tags=["auto"])
async def auto_report_add(body: dict = Body(...), _: str = Depends(get_current_username)):
    """Crea un reporte nuevo. body: {tipo: 'interno'|'socios', nombre}."""
    return auto_mail.add_report((body or {}).get("tipo", "interno"), (body or {}).get("nombre", ""))


@router.put("/auto/report/{rid}", tags=["auto"])
async def auto_report_update(rid: str, partial: dict = Body(...), _: str = Depends(get_current_username)):
    """Actualiza (merge) la configuración de un reporte."""
    return auto_mail.update_report(rid, partial)


@router.delete("/auto/report/{rid}", tags=["auto"])
async def auto_report_delete(rid: str, _: str = Depends(get_current_username)):
    return auto_mail.delete_report(rid)


@router.get("/auto/preview/{rid}", tags=["auto"])
async def auto_preview(rid: str, _: str = Depends(get_current_username)):
    """Vista previa de lo que se enviaría (sin enviar nada)."""
    rep = auto_mail.get_report(rid)
    if not rep:
        raise HTTPException(status_code=404, detail="Reporte desconocido.")
    try:
        return await auto_mail.preview_report(rep)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/auto/run/{rid}", tags=["auto"])
async def auto_run(rid: str, body: dict = Body(default={}), _: str = Depends(get_current_username)):
    """Ejecuta un reporte ahora. Si se pasa test_to, manda una prueba a esa dirección."""
    rep = auto_mail.get_report(rid)
    if not rep:
        raise HTTPException(status_code=404, detail="Reporte desconocido.")
    try:
        return await auto_mail.run_report(rep, test_to=(body or {}).get("test_to") or None)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
