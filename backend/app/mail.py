"""Servicio de envío de mails a socios.

- Plantilla con variables de combinación: {{nombre}}, {{apellido}}, {{carnet}},
  {{email}} y cualquier otra que se pase en `vars`. Cada socio puede además
  sobrescribir su asunto/cuerpo (personalización individual).
- Los mails se envían en HTML (con plantilla de marca: cabecera, color y pie) y
  también en texto plano como respaldo (multipart/alternative).
- Variables que son listas (préstamos vencidos/activos) se pueden pasar como
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
_NAVY, _ORANGE, _MAGENTA, _YELLOW = "#13235B", "#F5821F", "#861E92", "#EEB500"


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


# Encabezados unificados de la tabla de libros que ve el socio (mails y automáticos).
LIBROS_HEADERS = ["Libro", "Vencimiento"]


def libros_table(items: list[dict], con_fecha: bool = True) -> str:
    """Tabla HTML unificada de libros para el socio: columnas 'Libro' y 'Vencimiento'.

    `items`: lista de {"titulo": str, "fecha": str}. Fuente única para que el
    compositor de mails y los envíos automáticos muestren exactamente lo mismo.

    `con_fecha=False` deja solo la columna 'Libro'. Se usa para los VENCIDOS: al
    socio se le nombra el libro, no la fecha en que se le pasó.
    """
    if not con_fecha:
        return html_table(["Libro"], [[i.get("titulo") or "(sin título)"] for i in items])
    return html_table(LIBROS_HEADERS,
                      [[i.get("titulo") or "(sin título)", i.get("fecha") or ""] for i in items])


def _wrap_html(inner: str, track_url: str | None = None) -> str:
    """Envuelve el cuerpo en la plantilla de marca (logo + cinta de colores + pie).

    Si viene `track_url`, agrega al final el píxel de 1×1 que registra la apertura
    (ver `tracking.py` por sus límites: imágenes bloqueadas, precarga de Apple, etc.).
    """
    pixel = (f'<img src="{track_url}" width="1" height="1" alt="" '
             'style="display:block;width:1px;height:1px;border:0;opacity:0">') if track_url else ""
    if settings.app_public_url:
        url = settings.app_public_url.rstrip("/")
        # Logo completo a tamaño natural (948x456 ≈ 2:1), sin deformar.
        cabecera = (f'<img src="{url}/logo.png" alt="Biblioteca Popular Osvaldo Bayer" '
                    'width="248" style="display:block;width:248px;max-width:70%;height:auto">')
    else:
        cabecera = (f'<div style="color:{_MAGENTA};font-size:20px;font-weight:bold;line-height:1.2">'
                    'Biblioteca Popular<br>Osvaldo Bayer</div>')
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;background:#f4f5f7;font-family:Arial,Helvetica,sans-serif;color:#1f2430">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:24px 0"><tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 2px 10px rgba(19,35,91,.08)">
  <tr><td align="center" style="padding:26px 24px 18px">{cabecera}</td></tr>
  <tr><td style="font-size:0;line-height:0">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td width="34%" style="height:7px;background:{_YELLOW}">&nbsp;</td>
      <td width="33%" style="height:7px;background:{_ORANGE}">&nbsp;</td>
      <td width="33%" style="height:7px;background:{_MAGENTA}">&nbsp;</td>
    </tr></table></td></tr>
  <tr><td style="padding:26px 24px;font-size:15px;line-height:1.6">{inner}</td></tr>
  <tr><td style="padding:16px 24px;background:#faf7fc;color:#6b7280;font-size:12px;border-top:2px solid {_YELLOW}">
    <b style="color:{_MAGENTA}">Biblioteca Popular Osvaldo Bayer</b> · Villa La Angostura, Neuquén<br>
    Mensaje del sistema de gestión de la biblioteca.</td></tr>
</table></td></tr></table>{pixel}</body></html>"""


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


def _res(m: dict, status: str, detail: str = "") -> dict:
    """Fila de resultado de un envío. `idx` = posición original del destinatario:
    es lo que permite después cruzar quién recibió qué (por email no alcanza,
    porque en modo prueba todos los mails van a la misma dirección)."""
    return {"email": m["to"], "status": status, "detail": detail,
            "idx": m.get("idx"), "nombre": m.get("nombre", "")}


def _send_sync(messages: list[dict]) -> list[dict]:
    """Manda por SMTP. Resiliente: reconecta cada ~90 envíos (Gmail corta sesiones
    largas) y reintenta una vez si la conexión se cae. Nunca lanza: si no puede
    conectar, marca todos como error (para no devolver 500)."""
    results: list[dict] = []
    try:
        server = _smtp_connect()
    except Exception as exc:  # no se pudo conectar/autenticar
        detail = f"No se pudo conectar al servidor de correo: {exc}"
        return [_res(m, "error", detail) for m in messages]

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
                results.append(_res(m, "sent")); enviados += 1
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, ConnectionError):
                try:  # reconecta y reintenta una vez
                    server = _smtp_connect()
                    server.sendmail(msg["From"], [to_addr], msg.as_string())
                    results.append(_res(m, "sent")); enviados += 1
                except Exception as exc:
                    results.append(_res(m, "error", str(exc)))
            except Exception as exc:   # un destinatario falla, seguimos con el resto
                results.append(_res(m, "error", str(exc)))
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
                    # Guardamos el id de Resend: sirve para rastrear el envío en su panel.
                    try:
                        msg_id = (r.json() or {}).get("id") or ""
                    except Exception:  # noqa: BLE001
                        msg_id = ""
                    results.append({**_res(m, "sent"), "provider_id": msg_id})
                else:
                    results.append(_res(m, "error", f"Resend {r.status_code}: {r.text[:160]}"))
            except Exception as exc:  # noqa: BLE001
                results.append(_res(m, "error", str(exc)))
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
       html (dict opcional var->HTML para listas que llegan como tabla),
       track_url (opcional: píxel de apertura de ese destinatario).

    Los `resultados` vuelven EN EL ORDEN de `recipients` y con su `idx`, para poder
    cruzar después quién recibió qué (lo usa el historial).
    """
    # En modo prueba se manda UNA sola muestra (el primer destinatario), no una por socio.
    if test_to:
        recipients = recipients[:1]
    provider = (settings.mail_provider or "smtp").lower()
    from_addr = (settings.mail_from if provider == "resend" else "") or settings.smtp_from or settings.smtp_user
    from_hdr = formataddr((settings.smtp_from_name, from_addr))
    prepared: list[dict] = []
    results: list[dict] = []

    for i, r in enumerate(recipients):
        to_addr = test_to or r.get("email")
        variables = r.get("vars") or {}
        nombre = variables.get("nombre") or ""
        if not to_addr:
            results.append({"email": r.get("email") or "(sin email)", "status": "skipped",
                            "detail": "socio sin email", "nombre": nombre, "idx": i})
            continue
        tpl = r.get("body") or body_tpl
        prepared.append({
            "to": to_addr, "from": from_hdr, "idx": i, "nombre": nombre,
            "subject": render(r.get("subject") or subject_tpl, variables),
            "plain": render(tpl, variables),
            "html": _wrap_html(render_html(tpl, variables, r.get("html")), r.get("track_url")),
        })

    if dry_run:
        for m in prepared:
            results.append({**_res(m, "simulado", "DRY RUN (no se envió)"),
                            "subject": m["subject"]})
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

    # Devolver en el orden original: los "sin email" se acumulan aparte y si no,
    # aparecerían todos juntos al principio, descolocados respecto de la lista real.
    results.sort(key=lambda x: x.get("idx") if isinstance(x.get("idx"), int) else 0)

    return {
        "dry_run": dry_run,
        "test_to": test_to,
        "total": len(recipients),
        "preparados": len(prepared),
        "enviados": enviados if not dry_run else 0,
        "simulados": len(prepared) if dry_run else 0,
        "resultados": results,
    }
