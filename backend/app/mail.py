"""Servicio de envío de mails a socios.

- Plantilla con variables de combinación: {{nombre}}, {{apellido}}, {{carnet}},
  {{email}} y cualquier otra que se pase en `vars`. Cada socio puede además
  sobrescribir su asunto/cuerpo (personalización individual).
- Envío por SMTP. Modo de seguridad `mail_dry_run`: si está activo, NO envía de
  verdad (simula) — pensado para probar sin molestar a los socios.
- `test_to`: si se indica, todos los correos se mandan a esa dirección (con el
  contenido real de cada socio), para previsualizar envíos reales sin riesgo.
"""
from __future__ import annotations

import asyncio
import logging
import re
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr

from .config import settings

logger = logging.getLogger("mail")

_VAR_RE = re.compile(r"{{\s*(\w+)\s*}}")


def render(template: str, variables: dict[str, str]) -> str:
    """Reemplaza {{clave}} por su valor; deja intactas las claves desconocidas."""
    if not template:
        return ""
    return _VAR_RE.sub(lambda m: str(variables.get(m.group(1), m.group(0))), template)


def _send_sync(messages: list[tuple[str, MIMEText]]) -> list[dict]:
    """Abre UNA conexión SMTP y manda todos los mensajes. Devuelve resultados."""
    results: list[dict] = []
    server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
    try:
        server.ehlo()
        if settings.smtp_use_tls:
            server.starttls()
            server.ehlo()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        for to_addr, msg in messages:
            try:
                server.sendmail(msg["From"], [to_addr], msg.as_string())
                results.append({"email": to_addr, "status": "sent", "detail": ""})
            except Exception as exc:  # un destinatario falla, seguimos con el resto
                results.append({"email": to_addr, "status": "error", "detail": str(exc)})
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return results


async def send_campaign(
    subject_tpl: str,
    body_tpl: str,
    recipients: list[dict],
    dry_run: bool,
    test_to: str | None = None,
) -> dict:
    """Renderiza y envía la campaña. `recipients` = lista de dicts con:
       email, vars (dict), subject (override|None), body (override|None).
    """
    from_addr = settings.smtp_from or settings.smtp_user
    prepared: list[tuple[str, MIMEText]] = []
    results: list[dict] = []

    for r in recipients:
        to_addr = test_to or r.get("email")
        variables = r.get("vars") or {}
        nombre = variables.get("nombre") or ""
        if not to_addr:
            results.append({"email": r.get("email") or "(sin email)", "status": "skipped",
                            "detail": "socio sin email", "nombre": nombre})
            continue
        subject = render(r.get("subject") or subject_tpl, variables)
        body = render(r.get("body") or body_tpl, variables)
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = formataddr((settings.smtp_from_name, from_addr))
        msg["To"] = to_addr
        prepared.append((to_addr, msg))

    if dry_run:
        for to_addr, msg in prepared:
            results.append({"email": to_addr, "status": "simulado",
                            "detail": "DRY RUN (no se envió)", "subject": msg["Subject"]})
        enviados = 0
    else:
        if not settings.smtp_host:
            raise RuntimeError("SMTP no configurado (completá SMTP_HOST en .env).")
        sent_results = await asyncio.to_thread(_send_sync, prepared)
        results.extend(sent_results)
        enviados = sum(1 for x in sent_results if x["status"] == "sent")

    return {
        "dry_run": dry_run,
        "test_to": test_to,
        "total": len(recipients),
        "preparados": len(prepared),
        "enviados": enviados if not dry_run else 0,
        "simulados": len(prepared) if dry_run else 0,
        "resultados": results,
    }
