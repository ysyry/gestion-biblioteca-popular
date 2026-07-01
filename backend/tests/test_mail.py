"""Tests del servicio de mails: render, HTML y resiliencia del envío (bug del 500)."""
import smtplib

import pytest

from app import mail


def test_render_reemplaza_variables():
    assert mail.render("Hola {{nombre}}", {"nombre": "Ana"}) == "Hola Ana"
    # variable desconocida queda intacta
    assert mail.render("Hola {{x}}", {}) == "Hola {{x}}"


def test_render_html_escapa_y_inyecta_tablas():
    out = mail.render_html("Hola {{nombre}}\n{{lista}}",
                           {"nombre": "<b>Ana</b>"}, {"lista": "<table></table>"})
    assert "&lt;b&gt;Ana&lt;/b&gt;" in out      # el valor se escapa
    assert "<br>" in out                         # el salto de línea pasa a <br>
    assert "<table></table>" in out              # el bloque HTML se inyecta crudo


def test_html_table_vacia_y_con_datos():
    assert "Ninguno" in mail.html_table(["A"], [])
    t = mail.html_table(["Libro", "Venció"], [["El Eternauta", "2024-01-01"]])
    assert "El Eternauta" in t and "<table" in t


async def test_send_campaign_dry_run_no_envia():
    res = await mail.send_campaign("Asunto", "Cuerpo", [{"email": "a@b.com", "vars": {}}], dry_run=True)
    assert res["simulados"] == 1 and res["enviados"] == 0
    assert res["resultados"][0]["status"] == "simulado"


async def test_send_campaign_omite_sin_email():
    res = await mail.send_campaign("S", "B", [{"email": None, "vars": {"nombre": "X"}}], dry_run=True)
    assert res["resultados"][0]["status"] == "skipped"


async def test_send_campaign_real_usa_send_sync(monkeypatch):
    monkeypatch.setattr(mail.settings, "mail_provider", "smtp")
    monkeypatch.setattr(mail.settings, "smtp_host", "smtp.test")
    enviados = {}
    def fake(msgs):
        enviados["n"] = len(msgs)
        return [{"email": m["to"], "status": "sent", "detail": ""} for m in msgs]
    monkeypatch.setattr(mail, "_send_sync", fake)
    res = await mail.send_campaign("S", "Hola {{nombre}}", [{"email": "a@b.com", "vars": {"nombre": "Ana"}}], dry_run=False)
    assert res["enviados"] == 1 and enviados["n"] == 1


def _msg(to):
    return {"to": to, "from": "x@y.com", "subject": "s", "plain": "p", "html": "<p>h</p>"}


def test_send_sync_no_revienta_si_no_conecta(monkeypatch):
    # BUG del 500: si no se puede conectar, debe devolver errores, NO lanzar.
    def boom():
        raise smtplib.SMTPConnectError(421, "no conecta")
    monkeypatch.setattr(mail, "_smtp_connect", boom)
    res = mail._send_sync([_msg("a@b.com"), _msg("c@d.com")])
    assert len(res) == 2
    assert all(r["status"] == "error" for r in res)


async def test_send_campaign_resend(monkeypatch):
    # Con provider=resend usa la API HTTPS (no SMTP).
    monkeypatch.setattr(mail.settings, "mail_provider", "resend")
    monkeypatch.setattr(mail.settings, "resend_api_key", "re_test")
    monkeypatch.setattr(mail.settings, "mail_from", "biblioteca@dominio.org")
    usados = {}
    def fake(msgs):
        usados["from"] = msgs[0]["from"]
        return [{"email": m["to"], "status": "sent", "detail": ""} for m in msgs]
    monkeypatch.setattr(mail, "_send_resend", fake)
    res = await mail.send_campaign("S", "B", [{"email": "a@b.com", "vars": {}}], dry_run=False)
    assert res["enviados"] == 1
    assert "biblioteca@dominio.org" in usados["from"]
