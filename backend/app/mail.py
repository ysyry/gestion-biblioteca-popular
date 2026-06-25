"""Servicio de envío de mails a socios.

- Plantilla con variables de combinación: {{nombre}}, {{apellido}}, {{carnet}},
  {{email}} y cualquier otra que se pase en `vars`. Cada socio puede además
  sobrescribir su asunto/cuerpo (personalización individual).
- Los mails se envían en HTML (con plantilla de marca: cabecera, color y pie) y
  también en texto plano como respaldo (multipart/alternative).
- Variables que son listas (préstamos vencidos/por vencer) se pueden pasar como
  bloques HTML por destinatario (`html`) para que lleguen como TABLA en vez de
  un listado de texto interminable.
- Modo `mail_dry_run`: simula sin enviar. `test_to`: manda todo a una dirección.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from .config import settings

logger = logging.getLogger("mail")

_VAR_RE = re.compile(r"{{\s*(\w+)\s*}}")

# Paleta de marca (Manual de Identidad BPOB)
_NAVY, _ORANGE, _MAGENTA = "#13235B", "#F5821F", "#861E92"


def render(template: str, variables: dict[str, str]) -> str:
    """Texto plano: reemplaza {{clave}} por su valor; deja intactas las desconocidas."""
    if not template:
        return ""
    return _VAR_RE.sub(lambda m: str(variables.get(m.group(1), m.group(0))), template)


def render_html(template: str, variables: dict, html_blocks: dict | None = None) -> str:
    """HTML: escapa el texto y los saltos de línea, e inyecta bloques HTML (tablas)
    para las claves que estén en `html_blocks` (sin escapar)."""
    html_blocks = html_blocks or {}
    out, last = [], 0
    nl2br = lambda s: html.escape(s).replace("\n", "<br>")
    for m in _VAR_RE.finditer(template or ""):
        out.append(nl2br(template[last:m.start()]))
        k = m.group(1)
        if k in html_blocks:
            out.append(html_blocks[k])
        elif k in variables:
            out.append(nl2br(str(variables[k])))
        else:
            out.append(html.escape(m.group(0)))
        last = m.end()
    out.append(nl2br((template or "")[last:]))
    return "".join(out)


def html_table(headers: list[str], rows: list[list]) -> str:
    """Devuelve una tabla HTML lista para email (estilos inline)."""
    if not rows:
        return '<p style="color:#6b7280;margin:6px 0">— Ninguno —</p>'
    th = "".join(
        f'<th align="left" style="padding:7px 10px;background:#f3eef8;color:{_MAGENTA};'
        f'font-size:12px;text-transform:uppercase;letter-spacing:.03em">{html.escape(c)}</th>'
        for c in headers
    )
    body = ""
    for r in rows:
        tds = "".join(
            f'<td style="padding:7px 10px;border-bottom:1px solid #eef0f3;font-size:13px;'
            f'color:#1f2430">{html.escape(str(c))}</td>' for c in r
        )
        body += f"<tr>{tds}</tr>"
    return ('<table width="100%" cellpadding="0" cellspacing="0" '
            'style="border-collapse:collapse;margin:8px 0;border:1px solid #eef0f3;border-radius:8px;overflow:hidden">'
            f'<tr>{th}</tr>{body}</table>')


def _wrap_html(inner: str) -> str:
    """Envuelve el cuerpo en la plantilla de marca (cabecera + color + pie)."""
    logo = ""
    if settings.app_public_url:
        url = settings.app_public_url.rstrip("/")
        logo = (f'<img src="{url}/logo.png" alt="" width="38" height="38" '
                'style="vertical-align:middle;border-radius:8px;background:#fff;padding:3px;margin-right:10px">')
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;background:#f4f5f7;font-family:Arial,Helvetica,sans-serif;color:#1f2430">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:24px 0"><tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#fff;border-radius:14px;overflow:hidden">
  <tr><td style="background:{_NAVY};padding:18px 24px;color:#fff">
    {logo}<span style="font-size:17px;font-weight:bold;vertical-align:middle">Biblioteca Popular Osvaldo Bayer</span></td></tr>
  <tr><td style="height:5px;background:{_ORANGE};font-size:0;line-height:0">&nbsp;</td></tr>
  <tr><td style="padding:24px;font-size:15px;line-height:1.6">{inner}</td></tr>
  <tr><td style="padding:16px 24px;background:#f7f8fa;color:#6b7280;font-size:12px;border-top:1px solid #e6e8ec">
    Biblioteca Popular Osvaldo Bayer · Villa La Angostura, Neuquén<br>
    Mensaje del sistema de gestión de la biblioteca.</td></tr>
</table></td></tr></table></body></html>"""


def _send_sync(messages: list[tuple[str, MIMEMultipart]]) -> list[dict]:
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
       email, vars (dict), subject (override|None), body (override|None),
       html (dict opcional var->HTML para listas que llegan como tabla).
    """
    from_addr = settings.smtp_from or settings.smtp_user
    prepared: list[tuple[str, MIMEMultipart]] = []
    results: list[dict] = []

    for r in recipients:
        to_addr = test_to or r.get("email")
        variables = r.get("vars") or {}
        nombre = variables.get("nombre") or ""
        if not to_addr:
            results.append({"email": r.get("email") or "(sin email)", "status": "skipped",
                            "detail": "socio sin email", "nombre": nombre})
            continue
        tpl = r.get("body") or body_tpl
        subject = render(r.get("subject") or subject_tpl, variables)
        body_plain = render(tpl, variables)
        body_html = _wrap_html(render_html(tpl, variables, r.get("html")))

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = formataddr((settings.smtp_from_name, from_addr))
        msg["To"] = to_addr
        msg.attach(MIMEText(body_plain, "plain", "utf-8"))
        msg.attach(MIMEText(body_html, "html", "utf-8"))
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
