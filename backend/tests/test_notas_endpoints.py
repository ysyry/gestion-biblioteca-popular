"""Tests HTTP de las notas de socios: quién las ve, filtros y marca de resuelta."""
from datetime import date, timedelta

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app import cache, permisos
from app.auth import Sesion, get_current_client, get_current_username, get_session
from app.main import app


def _hex(texto: str) -> str:
    return texto.encode("utf-8").hex().upper()


def _dia(dias_atras: int) -> str:
    return (date.today() - timedelta(days=dias_atras)).isoformat() + " 10:00:00"


NOTAS_KOHA = [
    {"id": "30", "fecha": _dia(1), "tipo_koha": "L", "sede": "3169",
     "texto_hex": _hex("Dice que lo devuelve en noviembre"),
     "cardnumber": "0042", "surname": "Paz", "firstname": "Ana"},
    {"id": "29", "fecha": _dia(3), "tipo_koha": "L", "sede": "3169",
     "texto_hex": _hex("Cuotas hasta SEPTIEMBRE 2026"),
     "cardnumber": "0042", "surname": "Paz", "firstname": "Ana"},
    {"id": "28", "fecha": _dia(5), "tipo_koha": "L", "sede": "3169",
     "texto_hex": _hex("Reclamé libros x wp"),
     "cardnumber": "0077", "surname": "Sur", "firstname": "Leo"},
]


class KohaFalso:
    """Contesta las consultas de notas como lo haría el export de Koha."""
    def __init__(self):
        self.sql = []
        self.falla_notas = False

    async def run_sql(self, sql):
        self.sql.append(sql)
        if "FROM messages" in sql:
            if self.falla_notas:
                raise RuntimeError("Koha no responde")
            filas = NOTAS_KOHA
            if "b.cardnumber = '0042'" in sql:
                filas = [{k: v for k, v in f.items() if k not in ("cardnumber", "surname", "firstname")}
                         for f in NOTAS_KOHA if f["cardnumber"] == "0042"]
            if "LIKE '%noviembre%'" in sql:
                filas = [NOTAS_KOHA[0]]
            return filas
        return []

    async def run_report(self, report_id, params=None):
        return [{"cardnumber": "0042", "surname": "Paz", "firstname": "Ana"}]


@pytest.fixture
def koha():
    return KohaFalso()


@pytest.fixture
def como(store, koha, monkeypatch):
    from app.koha import reports
    monkeypatch.setattr(reports.ReportSpec, "report_id", lambda self: 1)
    cache.invalidate("notas")
    def _entrar(rol="bibliotecaria", usuario="flor"):
        s = Sesion(sid="t", usuario=usuario, nombre=usuario.title(), rol=rol,
                   permisos=permisos.permisos_de(rol))

        # Igual que la dependencia real: sin permiso de Koha no hay cliente.
        def cliente(ses: Sesion = Depends(get_session)):
            permisos.exigir(ses.rol, permisos.KOHA)
            return koha

        app.dependency_overrides[get_session] = lambda: s
        app.dependency_overrides[get_current_username] = lambda: s.usuario
        app.dependency_overrides[get_current_client] = cliente
        return TestClient(app)
    yield _entrar
    app.dependency_overrides.clear()
    cache.invalidate("notas")


def test_la_subcomision_no_ve_notas(como):
    c = como("subcomision")
    assert c.get("/api/notas").status_code == 403
    assert c.get("/api/notas/avisos").status_code == 403
    assert c.post("/api/notas/30/resuelta", json={}).status_code == 403


def test_listar_con_conteo_por_tipo(como):
    d = como("comision").get("/api/notas?dias=30").json()
    assert [n["id"] for n in d["items"]] == ["30", "29", "28"]
    assert d["conteo"] == {"cuotas": 1, "reclamo": 1, "novedad": 1}
    assert d["novedades_pendientes"] == 1
    assert d["items"][0]["surname"] == "Paz"


def test_filtrar_por_tipo_mantiene_el_conteo_total(como):
    d = como().get("/api/notas?tipo=novedad").json()
    assert [n["id"] for n in d["items"]] == ["30"]
    assert d["conteo"]["cuotas"] == 1


def test_busqueda_va_a_koha_escapada(como, koha):
    d = como().get("/api/notas", params={"q": "noviembre", "dias": 0}).json()
    assert [n["id"] for n in d["items"]] == ["30"]
    assert "LIKE '%noviembre%'" in koha.sql[-1]
    assert "message_date >=" not in koha.sql[-1]          # dias=0 = todo el historial


def test_marcar_resuelta_saca_la_novedad_de_pendientes(como):
    c = como()
    r = c.post("/api/notas/30/resuelta", json={"resuelta": True}).json()
    assert r["resuelta"] is True and r["resuelta_por"] == "Flor"
    d = c.get("/api/notas?pendientes=true").json()
    assert d["items"] == [] and d["novedades_pendientes"] == 0
    c.post("/api/notas/30/resuelta", json={"resuelta": False})
    assert [n["id"] for n in c.get("/api/notas?pendientes=true").json()["items"]] == ["30"]


def test_marcar_id_invalido_da_400(como):
    assert como().post("/api/notas/abc/resuelta", json={}).status_code == 400


def test_avisos_por_carnet(como):
    d = como().get("/api/notas/avisos?fresh=true").json()
    assert list(d["items"]) == ["0042"]
    assert d["items"]["0042"]["notas"][0]["texto"] == "Dice que lo devuelve en noviembre"


def test_la_ficha_trae_las_notas(como):
    d = como().get("/api/members/0042/profile").json()
    assert [n["tipo"] for n in d["notas"]] == ["novedad", "cuotas"]
    assert d["notas_error"] is None


def test_si_fallan_las_notas_la_ficha_se_muestra_igual(como, koha):
    koha.falla_notas = True
    r = como().get("/api/members/0042/profile")
    assert r.status_code == 200
    d = r.json()
    assert d["socio"]["surname"] == "Paz"
    assert d["notas"] is None and "Koha" in d["notas_error"]
