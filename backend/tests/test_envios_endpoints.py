"""Tests de las rutas del historial de envíos y del píxel de apertura (end-to-end HTTP)."""
import pytest
from fastapi.testclient import TestClient

from app import historial, mail, tracking
from app.auth import get_current_username
from app.main import app


@pytest.fixture
def cliente(store):
    """App con la sesión resuelta (los tests no hablan con Koha).

    Sin `with`: así no se disparan los eventos de arranque (warmup contra Koha y
    el programador de automáticos), que acá no queremos.
    """
    app.dependency_overrides[get_current_username] = lambda: "flor"
    yield TestClient(app)
    app.dependency_overrides.clear()


def _envio(**kw):
    recipients = [{"email": "ana@x.com", "vars": {"nombre": "Ana", "apellido": "Paz"}},
                  {"email": "beto@x.com", "vars": {"nombre": "Beto", "apellido": "Ruiz"}}]
    results = [{"email": r["email"], "status": "sent", "idx": i} for i, r in enumerate(recipients)]
    return historial.save_run(run_id=historial.new_run_id(), origen="auto", titulo="Recordatorio",
                              subject_tpl="Hola {{nombre}}", body_tpl="Tenés libros, {{nombre}}.",
                              dests=historial.destinatarios(recipients, results),
                              seguimiento=True, **kw)


def test_lista_y_detalle(cliente):
    r = _envio(report_id="socios")
    items = cliente.get("/api/envios").json()["items"]
    assert len(items) == 1 and items[0]["titulo"] == "Recordatorio"

    d = cliente.get(f"/api/envios/{r['run_id']}").json()
    assert [x["nombre"] for x in d["destinatarios"]] == ["Ana", "Beto"]
    assert d["destinatarios"][0]["subject"] == "Hola Ana"       # el mensaje que recibió cada uno
    assert d["destinatarios"][1]["body"] == "Tenés libros, Beto."


def test_filtra_por_origen_y_reporte(cliente):
    _envio(report_id="socios")
    historial.save_run(run_id=historial.new_run_id(), origen="manual", titulo="Campaña",
                       subject_tpl="", body_tpl="", dests=[])
    assert len(cliente.get("/api/envios?origen=manual").json()["items"]) == 1
    assert len(cliente.get("/api/envios?report_id=socios").json()["items"]) == 1
    assert cliente.get("/api/envios?origen=xxx").status_code == 422   # origen inválido


def test_detalle_inexistente_da_404(cliente):
    assert cliente.get("/api/envios/nada").status_code == 404


def test_pixel_registra_la_apertura(cliente):
    r = _envio()
    tok = tracking.make_token(r["run_id"], 1)

    resp = cliente.get(f"/api/t/{tok}.gif")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/gif"
    assert resp.content[:6] == b"GIF89a"
    assert "no-store" in resp.headers["cache-control"]

    d = cliente.get(f"/api/envios/{r['run_id']}").json()
    assert d["destinatarios"][1]["opened_at"] and d["destinatarios"][1]["opens"] == 1
    assert not d["destinatarios"][0].get("opened_at")
    assert cliente.get("/api/envios").json()["items"][0]["aperturas"] == 1


def test_pixel_no_registra_con_token_invalido(cliente):
    r = _envio()
    resp = cliente.get(f"/api/t/{r['run_id']}.0.firmafalsa123.gif")
    assert resp.status_code == 200 and resp.content[:6] == b"GIF89a"   # igual devuelve la imagen
    assert cliente.get("/api/envios").json()["items"][0]["aperturas"] == 0


def test_pixel_es_publico(store):
    """Lo pide el cliente de correo del socio, que no tiene sesión: no debe pedir token."""
    assert TestClient(app).get("/api/t/loquesea.gif").status_code == 200


def test_mail_send_guarda_historial(cliente, monkeypatch):
    """La pestaña Mails también deja registro de a quién se le mandó qué."""
    monkeypatch.setattr(mail.settings, "mail_dry_run", False)
    monkeypatch.setattr(mail.settings, "mail_provider", "smtp")
    monkeypatch.setattr(mail.settings, "smtp_host", "smtp.test")
    monkeypatch.setattr(mail, "_send_sync", lambda msgs: [mail._res(m, "sent") for m in msgs])

    body = {"subject": "Hola {{nombre}}", "body": "Te esperamos, {{nombre}}.",
            "recipients": [{"email": "ana@x.com", "vars": {"nombre": "Ana"}},
                           {"email": "beto@x.com", "vars": {"nombre": "Beto"},
                            "subject": "Asunto propio", "body": "Texto propio"}]}
    res = cliente.post("/api/mail/send", json=body).json()
    assert res["enviados"] == 2 and res["run_id"]

    h = cliente.get("/api/envios?origen=manual").json()["items"][0]
    assert h["usuario"] == "flor" and h["enviados"] == 2 and h["titulo"] == "Hola {{nombre}}"

    d = cliente.get(f"/api/envios/{res['run_id']}").json()["destinatarios"]
    assert d[0]["subject"] == "Hola Ana" and d[0]["body"] == "Te esperamos, Ana."
    assert d[1]["subject"] == "Asunto propio"      # respeta la personalización individual


def test_mail_send_registra_el_fallo(cliente, monkeypatch):
    monkeypatch.setattr(mail.settings, "mail_dry_run", False)
    monkeypatch.setattr(mail.settings, "mail_provider", "smtp")
    monkeypatch.setattr(mail.settings, "smtp_host", "")     # sin SMTP → RuntimeError
    body = {"subject": "s", "body": "b", "recipients": [{"email": "a@x.com", "vars": {}}]}
    assert cliente.post("/api/mail/send", json=body).status_code == 400
    h = cliente.get("/api/envios?origen=manual").json()["items"][0]
    assert h["ok"] is False and "SMTP" in h["error"]
