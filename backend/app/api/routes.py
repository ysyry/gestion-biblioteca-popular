"""Endpoints de la API. Todos los de datos requieren sesión (token Bearer)."""
from __future__ import annotations

import asyncio
import logging

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
