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


def _smtp_connect():
    server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
    server.ehlo()
    if settings.smtp_use_tls:
        server.starttls()
        server.ehlo()
    if settings.smtp_user:
        server.login(settings.smtp_user, settings.smtp_password)
    return server


def _build_mime(m: dict) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = m["subject"]
    msg["From"] = m["from"]
    msg["To"] = m["to"]
    msg.attach(MIMEText(m["plain"], "plain", "utf-8"))
    msg.attach(MIMEText(m["html"], "html", "utf-8"))
    return msg


def _send_sync(messages: list[dict]) -> list[dict]:
    """Manda por SMTP. Resiliente: reconecta cada ~90 envíos (Gmail corta sesiones
    largas) y reintenta una vez si la conexión se cae. Nunca lanza: si no puede
    conectar, marca todos como error (para no devolver 500)."""
    results: list[dict] = []
    try:
        server = _smtp_connect()
    except Exception as exc:  # no se pudo conectar/autenticar
        detail = f"No se pudo conectar al servidor de correo: {exc}"
        return [{"email": m["to"], "status": "error", "detail": detail} for m in messages]

    enviados = 0
    try:
        for m in messages:
            to_addr = m["to"]; msg = _build_mime(m)
            try:
                if enviados and enviados % 90 == 0:   # refrescar conexión
                    try: server.quit()
                    except Exception: pass
                    server = _smtp_connect()
                server.sendmail(msg["From"], [to_addr], msg.as_string())
                results.append({"email": to_addr, "status": "sent", "detail": ""}); enviados += 1
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, ConnectionError):
                try:  # reconecta y reintenta una vez
                    server = _smtp_connect()
                    server.sendmail(msg["From"], [to_addr], msg.as_string())
                    results.append({"email": to_addr, "status": "sent", "detail": ""}); enviados += 1
                except Exception as exc:
                    results.append({"email": to_addr, "status": "error", "detail": str(exc)})
            except Exception as exc:   # un destinatario falla, seguimos con el resto
                results.append({"email": to_addr, "status": "error", "detail": str(exc)})
    finally:
        try: server.quit()
        except Exception: pass
    return results


def _send_resend(messages: list[dict]) -> list[dict]:
    """Manda por la API HTTPS de Resend (funciona en Railway, que bloquea SMTP)."""
    import httpx
    results: list[dict] = []
    headers = {"Authorization": f"Bearer {settings.resend_api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=30) as client:
        for m in messages:
            payload = {"from": m["from"], "to": [m["to"]], "subject": m["subject"],
                       "html": m["html"], "text": m["plain"]}
            try:
                r = client.post("https://api.resend.com/emails", json=payload, headers=headers)
                if r.status_code in (200, 201):
                    results.append({"email": m["to"], "status": "sent", "detail": ""})
                else:
                    results.append({"email": m["to"], "status": "error",
                                    "detail": f"Resend {r.status_code}: {r.text[:160]}"})
            except Exception as exc:  # noqa: BLE001
                results.append({"email": m["to"], "status": "error", "detail": str(exc)})
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
    provider = (settings.mail_provider or "smtp").lower()
    from_addr = (settings.mail_from if provider == "resend" else "") or settings.smtp_from or settings.smtp_user
    from_hdr = formataddr((settings.smtp_from_name, from_addr))
    prepared: list[dict] = []
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
        prepared.append({
            "to": to_addr, "from": from_hdr,
            "subject": render(r.get("subject") or subject_tpl, variables),
            "plain": render(tpl, variables),
            "html": _wrap_html(render_html(tpl, variables, r.get("html"))),
        })

    if dry_run:
        for m in prepared:
            results.append({"email": m["to"], "status": "simulado",
                            "detail": "DRY RUN (no se envió)", "subject": m["subject"]})
        enviados = 0
    elif provider == "resend":
        if not settings.resend_api_key:
            raise RuntimeError("Falta RESEND_API_KEY.")
        sent_results = await asyncio.to_thread(_send_resend, prepared)
        results.extend(sent_results)
        enviados = sum(1 for x in sent_results if x["status"] == "sent")
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
