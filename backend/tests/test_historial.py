"""Tests del historial de envíos (automáticos + manuales) y del píxel de apertura."""
import re

import pytest

from app import historial, mail, storage, tracking


def _dests(n=2):
    recipients = [{"email": f"s{i}@x.com", "vars": {"nombre": f"S{i}", "apellido": "P"}}
                  for i in range(n)]
    results = [{"email": f"s{i}@x.com", "status": "sent", "idx": i} for i in range(n)]
    return recipients, results


def _guardar(store, **kw):
    recipients, results = _dests(kw.pop("n", 2))
    base = {"run_id": historial.new_run_id(), "origen": "auto", "titulo": "T",
            "subject_tpl": "Hola {{nombre}}", "body_tpl": "Cuerpo {{nombre}}",
            "dests": historial.destinatarios(recipients, results)}
    return historial.save_run(**{**base, **kw})


# ── Índice y detalle ────────────────────────────────────────────────────────
def test_guarda_resumen_en_el_indice_y_detalle_aparte(store):
    r = _guardar(store, n=3)
    assert r["total"] == 3 and r["enviados"] == 3
    # El índice NO lleva los destinatarios (por eso es barato de leer).
    assert "destinatarios" not in historial.listar()[0]
    assert len(historial.get_run(r["run_id"])["destinatarios"]) == 3


def test_lista_mas_nuevo_primero_y_filtra_por_origen_y_reporte(store):
    _guardar(store, origen="auto", report_id="a")
    _guardar(store, origen="manual")
    _guardar(store, origen="auto", report_id="b")
    assert len(historial.listar()) == 3
    assert [h["report_id"] for h in historial.listar(origen="auto")] == ["b", "a"]
    assert len(historial.listar(origen="manual")) == 1
    assert len(historial.listar(report_id="a")) == 1


def test_detalle_renderiza_el_mensaje_de_cada_persona(store):
    r = _guardar(store)
    d = historial.get_run(r["run_id"])["destinatarios"]
    assert d[0]["subject"] == "Hola S0" and d[0]["body"] == "Cuerpo S0"
    assert d[1]["subject"] == "Hola S1"


def test_detalle_respeta_el_mensaje_personalizado(store):
    recipients = [{"email": "a@x.com", "vars": {"nombre": "Ana"},
                   "subject": "Asunto propio", "body": "Texto propio para {{nombre}}"}]
    results = [{"email": "a@x.com", "status": "sent", "idx": 0}]
    r = historial.save_run(run_id=historial.new_run_id(), origen="manual", titulo="T",
                           subject_tpl="Genérico", body_tpl="Genérico",
                           dests=historial.destinatarios(recipients, results))
    d = historial.get_run(r["run_id"])["destinatarios"][0]
    assert d["subject"] == "Asunto propio" and d["body"] == "Texto propio para Ana"


def test_cruza_por_posicion_no_por_email(store):
    """En una prueba, todos los mails van a la misma dirección: cruzar por email
    mezclaría a las personas, por eso el cruce es por `idx`."""
    recipients = [{"email": "ana@x.com", "vars": {"nombre": "Ana"}},
                  {"email": "beto@x.com", "vars": {"nombre": "Beto"}}]
    results = [{"email": "prueba@x.com", "status": "sent", "idx": 0},
               {"email": "prueba@x.com", "status": "error", "detail": "rebotó", "idx": 1}]
    d = historial.destinatarios(recipients, results)
    assert d[0]["nombre"] == "Ana" and d[0]["status"] == "sent"
    assert d[1]["nombre"] == "Beto" and d[1]["status"] == "error" and d[1]["detail"] == "rebotó"
    assert d[0]["enviado_a"] == "prueba@x.com"      # se deja constancia de la desviación


def test_poda_borra_el_detalle_de_los_envios_viejos(store, monkeypatch):
    monkeypatch.setattr(historial, "_MAX_RUNS", 3)
    ids = [_guardar(store, n=1)["run_id"] for _ in range(5)]
    assert len(historial.listar(limit=100)) == 3
    assert historial.get_run(ids[-1]) is not None       # los 3 nuevos siguen
    assert historial.get_run(ids[0]) is None            # el más viejo se podó con su detalle


def test_record_no_rompe_el_envio_si_falla_el_storage(store, monkeypatch):
    def explota(*a, **k):
        raise RuntimeError("base caída")
    monkeypatch.setattr(storage, "set", explota)
    recipients, results = _dests(1)
    assert historial.record(run_id="x", origen="auto", titulo="T", subject_tpl="", body_tpl="",
                            dests=historial.destinatarios(recipients, results)) is None


# ── Migración del historial viejo ───────────────────────────────────────────
def test_migra_el_historial_viejo_de_automaticos(store):
    store["auto_mail_history"] = [{
        "ts": "2026-07-01T10:00:00+00:00", "report_id": "socios", "report_name": "Recordatorio",
        "tipo": "socios", "trigger": "auto", "ok": True, "total": 2, "enviados": 1,
        "destinatarios": [{"email": "a@x.com", "status": "sent", "nombre": "Ana", "detail": ""},
                          {"email": "", "status": "skipped", "nombre": "Beto", "detail": "sin email"}],
    }]
    items = historial.listar()
    assert len(items) == 1
    h = items[0]
    assert h["origen"] == "auto" and h["report_id"] == "socios" and h["titulo"] == "Recordatorio"
    assert h["enviados"] == 1 and h["sin_email"] == 1
    assert historial.get_run(h["run_id"])["destinatarios"][0]["nombre"] == "Ana"
    # Idempotente: ya migrado, no vuelve a correr ni duplica.
    assert len(historial.listar()) == 1


# ── Aperturas ───────────────────────────────────────────────────────────────
def test_token_firmado_ida_y_vuelta():
    t = tracking.make_token("abc123", 7)
    assert tracking.parse_token(t) == ("abc123", 7)
    assert tracking.parse_token(t + ".gif") == ("abc123", 7)   # así llega desde la URL
    assert tracking.parse_token(t + "x") is None                # firma alterada: no vale


def test_token_rechaza_firma_invalida():
    run_id, idx, _sig = tracking.make_token("abc123", 7).split(".")
    assert tracking.parse_token(f"{run_id}.{idx}.deadbeefdead") is None
    assert tracking.parse_token("cualquiera") is None
    assert tracking.parse_token("") is None


def test_registrar_apertura_cuenta_una_sola_persona(store):
    r = _guardar(store, n=2)
    assert historial.record_open(r["run_id"], 0) is True
    assert historial.record_open(r["run_id"], 0) is True      # segunda vista de la misma persona
    run = historial.get_run(r["run_id"])
    assert run["destinatarios"][0]["opens"] == 2
    assert run["destinatarios"][0]["opened_at"]               # queda la primera vez
    assert not run["destinatarios"][1].get("opened_at")
    assert run["aperturas"] == 1                             # 1 persona, no 2 aperturas
    assert historial.listar()[0]["aperturas"] == 1           # y se refleja en el índice


def test_apertura_ignora_envios_o_indices_inexistentes(store):
    r = _guardar(store, n=1)
    assert historial.record_open("no-existe", 0) is False
    assert historial.record_open(r["run_id"], 99) is False


# ── Píxel dentro del mail ───────────────────────────────────────────────────
def test_pixel_solo_si_hay_url_publica(store, monkeypatch):
    recipients = [{"email": "a@x.com", "vars": {}}]
    monkeypatch.setattr(tracking.settings, "app_public_url", "")
    assert historial.aplicar_seguimiento(recipients, "run1") is False
    assert "track_url" not in recipients[0]

    monkeypatch.setattr(tracking.settings, "app_public_url", "https://app.test/")
    assert historial.aplicar_seguimiento(recipients, "run1") is True
    assert recipients[0]["track_url"].startswith("https://app.test/api/t/run1.0.")


def test_el_html_del_mail_lleva_el_pixel():
    con = mail._wrap_html("Hola", "https://app.test/api/t/tok.gif")
    assert 'src="https://app.test/api/t/tok.gif"' in con and 'width="1"' in con
    assert "<img" not in mail._wrap_html("Hola").split("Mensaje del sistema")[-1]


async def test_el_mail_enviado_lleva_el_pixel_de_esa_persona(store, monkeypatch):
    """Camino completo: el HTML que sale lleva un píxel distinto por destinatario,
    y ese píxel resuelve a la persona correcta dentro del envío."""
    monkeypatch.setattr(tracking.settings, "app_public_url", "https://app.test")
    monkeypatch.setattr(mail.settings, "mail_provider", "smtp")
    monkeypatch.setattr(mail.settings, "smtp_host", "smtp.test")
    enviados = []
    monkeypatch.setattr(mail, "_send_sync",
                        lambda msgs: enviados.extend(msgs) or [mail._res(m, "sent") for m in msgs])

    run_id = historial.new_run_id()
    recipients = [{"email": "ana@x.com", "vars": {"nombre": "Ana"}},
                  {"email": "beto@x.com", "vars": {"nombre": "Beto"}}]
    assert historial.aplicar_seguimiento(recipients, run_id) is True
    res = await mail.send_campaign("s", "Hola {{nombre}}", recipients, dry_run=False)
    historial.save_run(run_id=run_id, origen="manual", titulo="T", subject_tpl="s",
                       body_tpl="Hola {{nombre}}", seguimiento=True,
                       dests=historial.destinatarios(recipients, res["resultados"]))

    # Cada mail lleva SU píxel (ojo: el HTML trae también el logo, hay que buscar el de /api/t/).
    urls = [re.search(r'src="([^"]*/api/t/[^"]+)"', m["html"]).group(1) for m in enviados]
    assert len(set(urls)) == 2
    token = urls[1].rsplit("/api/t/", 1)[1]
    assert tracking.parse_token(token) == (run_id, 1)

    historial.record_open(*tracking.parse_token(token))
    d = historial.get_run(run_id)["destinatarios"]
    assert d[1]["nombre"] == "Beto" and d[1]["opened_at"]
    assert not d[0].get("opened_at")


async def test_send_campaign_devuelve_resultados_en_orden_con_idx(monkeypatch):
    """Los 'sin email' se acumulan aparte; sin reordenar quedarían descolocados."""
    monkeypatch.setattr(mail.settings, "mail_provider", "smtp")
    monkeypatch.setattr(mail.settings, "smtp_host", "smtp.test")
    monkeypatch.setattr(mail, "_send_sync", lambda msgs: [mail._res(m, "sent") for m in msgs])
    recipients = [{"email": None, "vars": {"nombre": "A"}},
                  {"email": "b@x.com", "vars": {"nombre": "B"}},
                  {"email": None, "vars": {"nombre": "C"}}]
    res = await mail.send_campaign("s", "b", recipients, dry_run=False)
    assert [r["idx"] for r in res["resultados"]] == [0, 1, 2]
    assert [r["status"] for r in res["resultados"]] == ["skipped", "sent", "skipped"]
