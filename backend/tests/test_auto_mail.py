"""Tests de los reportes automáticos: config, split de préstamos y exclusión de bajas/becados.

El fixture `store` (storage en memoria) vive en conftest.py: lo comparten estos
tests y los del historial.
"""
import pytest

from app import auto_mail, historial


# ── Config: lista de reportes ───────────────────────────────────────────────
def test_config_arranca_con_dos_reportes(store):
    cfg = auto_mail.load_config()
    ids = [r["id"] for r in cfg["reports"]]
    assert ids == ["resumen", "socios"]


def test_add_update_delete_report(store):
    rep = auto_mail.add_report("interno", "Cuotas mensual")
    assert rep["tipo"] == "interno" and rep["nombre"] == "Cuotas mensual"
    auto_mail.update_report(rep["id"], {"cada_dias": 30, "incluir_cuotas": True})
    assert auto_mail.get_report(rep["id"])["cada_dias"] == 30
    auto_mail.delete_report(rep["id"])
    assert auto_mail.get_report(rep["id"]) is None


def test_migracion_de_formato_viejo(store):
    # Formato viejo: dos jobs fijos con sus valores.
    store["auto_mail"] = {"resumen_interno": {"cada_dias": 15, "enabled": True},
                          "recordatorio_socios": {"umbral_atraso": 30},
                          "_last_run": {"resumen_interno": "2026-01-01"}}
    cfg = auto_mail.load_config()
    resumen = next(r for r in cfg["reports"] if r["id"] == "resumen")
    socios = next(r for r in cfg["reports"] if r["id"] == "socios")
    assert resumen["cada_dias"] == 15 and resumen["enabled"] is True
    assert socios["umbral_atraso"] == 30


def test_migracion_v1_renombra_y_agrega_lectura(store):
    # Config existente con el texto viejo y sin versión → debe migrarse una sola vez.
    store["auto_mail"] = {"reports": [
        {"id": "resumen", "tipo": "interno", "nombre": "Resumen interno",
         "body": "VENCIDOS:\n{{lista_vencidos}}\n\nPor vencer ({{cantidad_por_vencer}}):\n{{por_vencer}}",
         "enabled": True},
    ]}
    cfg = auto_mail.load_config()
    nombres = [r.get("nombre") for r in cfg["reports"]]
    assert "Lectura" in nombres                       # se agregó el reporte de lectura
    # Las dos migraciones corren en cadena: "Por vencer" → "En préstamo" (v1) → "Activos" (v2).
    body = cfg["reports"][0]["body"]
    assert "Por vencer" not in body and "En préstamo" not in body
    assert "Activos ({{cantidad_activos}})" in body and "{{activos}}" in body
    assert cfg["_ver"] == auto_mail._CONFIG_VER

    # Idempotente: correrla de nuevo no duplica "Lectura" ni vuelve a tocar nada.
    cfg2 = auto_mail.load_config()
    assert [r.get("nombre") for r in cfg2["reports"]].count("Lectura") == 1
    lectura = next(r for r in cfg2["reports"] if r["nombre"] == "Lectura")
    assert lectura["tipo"] == "socios" and "{{activos}}" in lectura["body"]


def test_migracion_v2_unifica_el_vocabulario_en_activos(store):
    """Config ya en v1: solo debe correr v2 (renombrar las variables 'por vencer')."""
    store["auto_mail"] = {"_ver": 1, "reports": [
        {"id": "resumen", "tipo": "interno", "nombre": "R",
         "subject": "Resumen", "footer": "",
         "body": "EN PRÉSTAMO (vencen en los próximos {{dias_antes}} días) ({{total_por_vencer}}):\n{{lista_por_vencer}}"},
        {"id": "socios", "tipo": "socios", "nombre": "S", "subject": "S", "footer": "",
         "body": "En préstamo ({{cantidad_por_vencer}}):\n{{por_vencer}}"},
    ]}
    reports = auto_mail.load_config()["reports"]
    assert reports[0]["body"] == ("ACTIVOS (vencen en los próximos {{dias_antes}} días) "
                                  "({{total_activos}}):\n{{lista_activos}}")
    assert reports[1]["body"] == "Activos ({{cantidad_activos}}):\n{{activos}}"
    # v2 no vuelve a agregar "Lectura" (eso era de v1, que acá no corre).
    assert [r["id"] for r in reports] == ["resumen", "socios"]


def test_las_plantillas_viejas_siguen_funcionando(store):
    """Aunque alguien deje {{por_vencer}} escrito a mano, tiene que seguir renderizando."""
    from app import mail
    vars_socio = {"activos": "• El Aleph (vence 10/08/2026)", "por_vencer": "• El Aleph (vence 10/08/2026)"}
    assert mail.render("{{por_vencer}}", vars_socio) == mail.render("{{activos}}", vars_socio)


"""Tests de la lógica de préstamos y exclusiones."""


async def test_run_report_registra_en_historial(store, monkeypatch):
    async def recs(rep):
        return {"recipients": [{"email": "ana@b.com", "vars": {"nombre": "Ana"}},
                               {"email": None, "vars": {"nombre": "Beto"}}],
                "con_email": 1, "sin_email": 1}
    async def fake_send(subject, body, recipients, dry_run, test_to=None):
        return {"total": 2, "enviados": 1, "resultados": [
            {"email": "ana@b.com", "status": "sent", "idx": 0},
            {"email": "(sin email)", "status": "skipped", "detail": "socio sin email", "idx": 1}]}
    monkeypatch.setattr(auto_mail, "_socios_recipients", recs)
    monkeypatch.setattr(auto_mail.mail, "send_campaign", fake_send)

    rep = {"id": "socios", "nombre": "Recordatorio", "tipo": "socios",
           "subject": "Hola {{nombre}}", "body": "Cuerpo para {{nombre}}", "footer": ""}
    out = await auto_mail.run_report(rep, trigger="auto", usuario="flor")

    h = auto_mail.get_history("socios")
    assert len(h) == 1
    assert h[0]["trigger"] == "auto" and h[0]["usuario"] == "flor"
    assert h[0]["enviados"] == 1 and h[0]["sin_email"] == 1 and h[0]["total"] == 2
    assert h[0]["run_id"] == out["run_id"]

    # El detalle guarda a quién se le mandó QUÉ mensaje (renderizado por persona).
    detalle = historial.get_run(out["run_id"])
    d0, d1 = detalle["destinatarios"]
    assert d0["nombre"] == "Ana" and d0["status"] == "sent"
    assert d0["subject"] == "Hola Ana" and d0["body"] == "Cuerpo para Ana"
    assert d1["nombre"] == "Beto" and d1["status"] == "skipped"


async def test_automaticos_respetan_mail_dry_run(store, monkeypatch):
    """Con MAIL_DRY_RUN=true no sale ningún mail, tampoco desde Automáticos."""
    visto = {}
    async def recs(rep):
        return {"recipients": [{"email": "ana@b.com", "vars": {"nombre": "Ana"}}],
                "con_email": 1, "sin_email": 0}
    async def fake_send(subject, body, recipients, dry_run, test_to=None):
        visto["dry_run"] = dry_run
        return {"total": 1, "enviados": 0, "simulados": 1, "dry_run": dry_run,
                "resultados": [{"email": "ana@b.com", "status": "simulado", "idx": 0}]}
    monkeypatch.setattr(auto_mail, "_socios_recipients", recs)
    monkeypatch.setattr(auto_mail.mail, "send_campaign", fake_send)
    monkeypatch.setattr(auto_mail.settings, "mail_dry_run", True)

    rep = {"id": "socios", "nombre": "R", "tipo": "socios", "subject": "s", "body": "b", "footer": ""}
    out = await auto_mail.run_report(rep, test_to="flor@b.com")

    assert visto["dry_run"] is True
    assert auto_mail.get_history("socios")[0]["dry_run"] is True

    # Y con la bandera en false vuelve a enviar de verdad.
    monkeypatch.setattr(auto_mail.settings, "mail_dry_run", False)
    await auto_mail.run_report(rep, test_to="flor@b.com")
    assert visto["dry_run"] is False
    assert auto_mail.get_history("socios")[0]["dry_run"] is False
    assert out["run_id"] != auto_mail.get_history("socios")[0]["run_id"]


async def test_run_report_registra_el_fallo(store, monkeypatch):
    async def recs(rep):
        raise RuntimeError("Koha no responde")
    monkeypatch.setattr(auto_mail, "_socios_recipients", recs)

    rep = {"id": "socios", "nombre": "R", "tipo": "socios", "subject": "s", "body": "b", "footer": ""}
    with pytest.raises(RuntimeError):
        await auto_mail.run_report(rep, trigger="auto")

    h = auto_mail.get_history("socios")
    assert len(h) == 1 and h[0]["ok"] is False and "Koha no responde" in h[0]["error"]


def test_d_formatea_fecha_argentina():
    assert auto_mail._d("2026-06-15") == "15/06/2026"
    assert auto_mail._d("2026-06-15T00:00:00") == "15/06/2026"   # recorta la hora
    assert auto_mail._d("") == "" and auto_mail._d(None) == ""


def test_split_loans():
    rows = [{"dias_atraso": "5"}, {"dias_atraso": "-2"}, {"dias_atraso": "-30"}, {"dias_atraso": None}]
    venc, porv = auto_mail._split_loans(rows, dias_antes=3, umbral_atraso=1)
    assert len(venc) == 1            # solo el de +5
    assert len(porv) == 1            # solo el de -2 (dentro de 3 días); -30 queda fuera


def test_split_loans_respeta_umbral():
    # Con umbral 30, solo entran los vencidos hace 30 días o más.
    rows = [{"dias_atraso": "5"}, {"dias_atraso": "40"}, {"dias_atraso": "31"}]
    venc, _ = auto_mail._split_loans(rows, dias_antes=3, umbral_atraso=30)
    assert len(venc) == 2            # 40 y 31; el de 5 días queda afuera


async def test_interno_usa_umbral_atraso(monkeypatch):
    async def loans():
        return [{"cardnumber": "1", "surname": "A", "firstname": "x", "title": "L1", "date_due": "2026-01-01", "dias_atraso": "5"},
                {"cardnumber": "2", "surname": "B", "firstname": "y", "title": "L2", "date_due": "2025-11-01", "dias_atraso": "60"}]
    monkeypatch.setattr(auto_mail, "_all_loans", loans)
    rep = {"tipo": "interno", "dias_antes": 7, "umbral_atraso": 30,
           "incluir_vencidos": True, "incluir_por_vencer": False, "incluir_cuotas": False,
           "subject": "s", "body": "{{lista_vencidos}}", "footer": ""}
    d = await auto_mail.build_interno(rep)
    assert d["stats"]["vencidos"] == 1          # solo el de 60 días (umbral 30)
    assert "L1" not in d["body"] and "L2" in d["body"]


async def test_socios_excluye_bajas_y_becados(monkeypatch):
    async def loans():
        return [
            {"cardnumber": "1", "surname": "Activo", "firstname": "A", "email": "a@x.com", "dias_atraso": "10"},
            {"cardnumber": "2", "surname": "Baja", "firstname": "B", "email": "b@x.com", "dias_atraso": "10"},
        ]
    async def members():
        return {"1": {"surname": "Activo", "firstname": "A", "email": "a@x.com", "categorycode": "AD"},
                "2": {"surname": "Baja", "firstname": "B", "email": "b@x.com", "categorycode": "B"},
                "3": {"surname": "Becado", "firstname": "C", "email": "c@x.com", "categorycode": "BEC."}}
    async def cmap():
        return {"3": {"matricula": "3", "apellido": "Becado", "nombre": "C", "debe": 5, "impagos": ["Ene"]}}
    monkeypatch.setattr(auto_mail, "_all_loans", loans)
    monkeypatch.setattr(auto_mail, "_members_map", members)
    monkeypatch.setattr(auto_mail, "_cuota_map", cmap)

    rep = {"tipo": "socios", "dias_antes": 3, "umbral_atraso": 1,
           "incluir_vencidos": True, "incluir_por_vencer": False,
           "incluir_cuotas": True, "umbral_cuota": 1, "excluidos": []}
    data = await auto_mail._socios_recipients(rep)
    carnets = {r["_carnet"] for r in data["recipients"]}
    assert "1" in carnets            # activo con vencidos: entra
    assert "2" not in carnets        # de baja: NO recibe
    assert "3" not in carnets        # becado: no cuenta como deuda de cuota


async def test_vars_del_socio_vencidos_sin_fecha_activos_y_prestamos(monkeypatch):
    async def loans():
        return [
            {"cardnumber": "1", "surname": "P", "firstname": "Ana", "email": "a@x.com",
             "title": "El Aleph", "date_due": "2026-05-12", "dias_atraso": "20"},
            {"cardnumber": "1", "surname": "P", "firstname": "Ana", "email": "a@x.com",
             "title": "Rayuela", "date_due": "2026-08-10", "dias_atraso": "-2"},
        ]
    async def members():
        return {"1": {"categorycode": "AD", "email": "a@x.com", "surname": "P", "firstname": "Ana"}}
    async def cmap():
        return {}
    monkeypatch.setattr(auto_mail, "_all_loans", loans)
    monkeypatch.setattr(auto_mail, "_members_map", members)
    monkeypatch.setattr(auto_mail, "_cuota_map", cmap)

    rep = {"tipo": "socios", "dias_antes": 7, "umbral_atraso": 1, "incluir_vencidos": True,
           "incluir_por_vencer": True, "incluir_cuotas": False, "excluidos": []}
    v = (await auto_mail._socios_recipients(rep))["recipients"][0]

    # Vencidos: solo el título, sin fecha.
    assert v["vars"]["vencidos"] == "• El Aleph"
    assert "venció" not in v["vars"]["vencidos"] and "2026" not in v["vars"]["vencidos"]
    assert "Vencimiento" not in v["html"]["vencidos"]      # la tabla tampoco lleva la columna

    # Activos: título + cuándo vence.
    assert v["vars"]["activos"] == "• Rayuela (vence 10/08/2026)"
    assert v["vars"]["cantidad_vencidos"] == "1" and v["vars"]["cantidad_activos"] == "1"

    # Todos los préstamos: vencidos + activos juntos.
    assert v["vars"]["cantidad_prestamos"] == "2"
    assert "El Aleph" in v["vars"]["prestamos"] and "Rayuela" in v["vars"]["prestamos"]

    # Alias del nombre viejo, para las plantillas ya escritas.
    assert v["vars"]["por_vencer"] == v["vars"]["activos"]
    assert v["vars"]["cantidad_por_vencer"] == v["vars"]["cantidad_activos"]
    assert v["html"]["por_vencer"] == v["html"]["activos"]


async def test_interno_conserva_la_fecha_en_vencidos(monkeypatch):
    """El resumen interno lo leen las bibliotecarias: ahí la fecha y el atraso sí van."""
    async def loans():
        return [{"cardnumber": "1", "surname": "P", "firstname": "Ana", "title": "El Aleph",
                 "date_due": "2026-05-12", "dias_atraso": "20"}]
    monkeypatch.setattr(auto_mail, "_all_loans", loans)
    rep = {"tipo": "interno", "dias_antes": 7, "umbral_atraso": 1, "incluir_vencidos": True,
           "incluir_por_vencer": False, "incluir_cuotas": False,
           "subject": "s", "body": "{{lista_vencidos}} | {{total_activos}}", "footer": ""}
    d = await auto_mail.build_interno(rep)
    assert "venció 12/05/2026" in d["body"] and "20 días" in d["body"]
    assert d["vars"]["total_por_vencer"] == d["vars"]["total_activos"]   # alias


async def test_socios_respeta_excluidos(monkeypatch):
    async def loans():
        return [{"cardnumber": "1", "surname": "X", "firstname": "Y", "email": "a@x.com", "dias_atraso": "10"}]
    async def members():
        return {"1": {"categorycode": "AD", "email": "a@x.com", "surname": "X", "firstname": "Y"}}
    async def cmap():
        return {}
    monkeypatch.setattr(auto_mail, "_all_loans", loans)
    monkeypatch.setattr(auto_mail, "_members_map", members)
    monkeypatch.setattr(auto_mail, "_cuota_map", cmap)
    rep = {"tipo": "socios", "dias_antes": 3, "umbral_atraso": 1,
           "incluir_vencidos": True, "incluir_por_vencer": False,
           "incluir_cuotas": False, "excluidos": ["1"]}
    data = await auto_mail._socios_recipients(rep)
    assert data["recipients"] == []   # el único candidato está excluido


async def test_interno_cuota_excluye_bajas_y_becados(monkeypatch):
    async def loans():
        return []
    async def cmap():
        return {"1": {"matricula": "1", "apellido": "A", "nombre": "a", "debe": 3, "impagos": ["Ene"]},
                "2": {"matricula": "2", "apellido": "Baja", "nombre": "b", "debe": 5, "impagos": ["Ene"]},
                "3": {"matricula": "3", "apellido": "Bec", "nombre": "c", "debe": 2, "impagos": ["Feb"]}}
    async def members():
        return {"1": {"categorycode": "AD"}, "2": {"categorycode": "B"}, "3": {"categorycode": "BEC."}}
    monkeypatch.setattr(auto_mail, "_all_loans", loans)
    monkeypatch.setattr(auto_mail, "_cuota_map", cmap)
    monkeypatch.setattr(auto_mail, "_members_map", members)
    rep = {"tipo": "interno", "dias_antes": 7, "umbral_atraso": 1,
           "incluir_vencidos": False, "incluir_por_vencer": False,
           "incluir_cuotas": True, "umbral_cuota": 1, "subject": "s", "body": "{{lista_cuotas}}", "footer": ""}
    d = await auto_mail.build_interno(rep)
    assert d["stats"]["deudores_cuota"] == 1     # solo el activo; baja y becado excluidos
