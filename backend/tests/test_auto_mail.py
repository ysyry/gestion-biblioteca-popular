"""Tests de los reportes automáticos: config, split de préstamos y exclusión de bajas/becados."""
import pytest

from app import auto_mail, storage


@pytest.fixture
def store(monkeypatch):
    """Storage en memoria para los tests de config (no toca archivo ni Postgres)."""
    mem = {}
    monkeypatch.setattr(storage, "get", lambda k: mem.get(k))
    monkeypatch.setattr(storage, "set", lambda k, v: mem.__setitem__(k, v))
    return mem


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
    assert "Por vencer" not in cfg["reports"][0]["body"]
    assert "En préstamo" in cfg["reports"][0]["body"]  # wording actualizado
    assert cfg["_ver"] == auto_mail._CONFIG_VER

    # Idempotente: correrla de nuevo no duplica "Lectura" ni vuelve a tocar nada.
    cfg2 = auto_mail.load_config()
    assert [r.get("nombre") for r in cfg2["reports"]].count("Lectura") == 1
    lectura = next(r for r in cfg2["reports"] if r["nombre"] == "Lectura")
    assert lectura["tipo"] == "socios" and "{{por_vencer}}" in lectura["body"]


"""Tests de la lógica de préstamos y exclusiones."""


def test_historial_guarda_mas_nuevo_primero_y_filtra(store):
    auto_mail.add_history({"report_id": "a", "ok": True, "enviados": 2})
    auto_mail.add_history({"report_id": "b", "ok": True})
    auto_mail.add_history({"report_id": "a", "ok": False, "error": "x"})
    ha = auto_mail.get_history("a")
    assert len(ha) == 2 and ha[0]["ok"] is False        # el último queda primero
    assert len(auto_mail.get_history()) == 3            # sin filtro: todos


async def test_run_report_registra_en_historial(store, monkeypatch):
    async def recs(rep):
        return {"recipients": [{"email": "ana@b.com", "vars": {"nombre": "Ana"}}],
                "con_email": 1, "sin_email": 0}
    async def fake_send(subject, body, recipients, dry_run, test_to=None):
        return {"total": 1, "enviados": 1, "resultados": [{"email": "ana@b.com", "status": "sent"}]}
    monkeypatch.setattr(auto_mail, "_socios_recipients", recs)
    monkeypatch.setattr(auto_mail.mail, "send_campaign", fake_send)

    rep = {"id": "socios", "tipo": "socios", "subject": "s", "body": "b", "footer": ""}
    await auto_mail.run_report(rep, trigger="auto")

    h = auto_mail.get_history("socios")
    assert len(h) == 1 and h[0]["trigger"] == "auto" and h[0]["enviados"] == 1
    assert h[0]["destinatarios"][0]["nombre"] == "Ana"   # se enriquece con el nombre


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
