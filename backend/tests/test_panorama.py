"""Tests de "Lo que anda pasando en la Bayer": cuentas, períodos y que no se filtre nada sensible."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import espacios, panorama, permisos, registro
from app.auth import Sesion, get_current_username, get_session
from app.main import app

HOY = date(2026, 9, 16)


def _act(fecha, personas, titulo="Charla", **kw):
    datos = {"titulo": titulo, "tipo": kw.pop("tipo", "charla"), "fecha": fecha,
             "hora_inicio": kw.pop("hora_inicio", "18:00"), "hora_fin": kw.pop("hora_fin", "20:00"),
             "personas_total": personas, "quien_completa": {"nombre": "Caro", "contacto": "caro@x.ar"}, **kw}
    return registro.guardar("actividad", datos, cargado_por="Laura")


def _taller(mes, participantes, titulo="Taller de radio", **kw):
    datos = {"titulo": titulo, "mes": mes, "encuentros": kw.pop("encuentros", 4),
             "participantes": participantes, "quien_completa": {"nombre": "Caro"}, **kw}
    return registro.guardar("taller", datos, cargado_por="Laura")


# ── Períodos ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("clave, desde, hasta", [
    ("mes", date(2026, 9, 1), HOY),
    ("3m", date(2026, 7, 1), HOY),
    ("12m", date(2025, 10, 1), HOY),
    ("anio", date(2026, 1, 1), HOY),
    ("anio_anterior", date(2025, 1, 1), date(2025, 12, 31)),
])
def test_periodos(clave, desde, hasta):
    assert panorama.periodo(clave, HOY) == (desde, hasta)


def test_periodo_anterior_del_mismo_largo():
    assert panorama.anterior(date(2026, 7, 1), date(2026, 9, 16)) == (date(2026, 4, 14), date(2026, 6, 30))   # 78 días cada uno


def test_periodo_desconocido():
    with pytest.raises(ValueError):
        panorama.periodo("siglo", HOY)


# ── Derivados que se guardan igual para todos ───────────────────────────────
def test_derivados_dia_momento_y_duracion(store):
    r = _act("2026-09-12", 20, hora_inicio="10:30", hora_fin="12:00")      # sábado a la mañana
    assert registro.derivados(r) == {"dia_semana": 5, "dia_nombre": "Sábado", "momento": "manana",
                                     "momento_nombre": "Mañana", "duracion_min": 90}
    noche = _act("2026-09-10", 5, titulo="Cine", hora_inicio="19:30", hora_fin="")
    assert registro.derivados(noche)["momento"] == "noche"
    assert registro.derivados(noche)["duracion_min"] is None
    assert r["esquema"] == registro.ESQUEMA


# ── Cuentas ─────────────────────────────────────────────────────────────────
def test_sin_datos(store):
    d = panorama.armar("3m", HOY)
    assert d["hay_datos"] is False
    assert d["numeros"]["actividades"] == {"valor": 0, "anterior": 0}
    assert [m["etiqueta"] for m in d["por_mes"]] == ["jul 26", "ago 26", "sep 26"]
    assert d["destacados"] == []


def test_solo_cuenta_lo_validado(store):
    link = registro.crear_link({"clase": "general"}, "Laura")
    registro.guardar("actividad", {"titulo": "Sin validar", "tipo": "charla", "fecha": "2026-09-01",
                                   "hora_inicio": "18:00", "personas_total": 99,
                                   "quien_completa": {"nombre": "X"}}, link=link)
    _act("2026-09-02", 10)
    d = panorama.armar("mes", HOY)
    assert d["numeros"]["actividades"]["valor"] == 1 and d["numeros"]["personas"]["valor"] == 10


def test_numeros_y_comparacion_con_el_periodo_anterior(store):
    _act("2026-07-05", 30, tematicas=["literatura"], primera_vez=4)
    _act("2026-08-10", 20, titulo="Otra", tematicas=["literatura", "infancias"], primera_vez=2)
    _act("2026-05-01", 12, titulo="Antes")                       # período anterior
    _act("2025-01-01", 500, titulo="Muy vieja")                  # fuera de todo
    d = panorama.armar("3m", HOY)
    n = d["numeros"]
    assert n["actividades"] == {"valor": 2, "anterior": 1}
    assert n["personas"] == {"valor": 50, "anterior": 12}
    assert n["primera_vez"]["valor"] == 6
    assert n["horas"]["valor"] == 4                              # 2 h + 2 h
    julio = next(m for m in d["por_mes"] if m["mes"] == "2026-07")
    assert julio["actividades"] == 1 and julio["personas"] == 30


def test_tipos_y_tematicas_con_promedio(store):
    _act("2026-09-01", 40, tipo="presentacion", tematicas=["literatura"])
    _act("2026-09-02", 20, titulo="B", tipo="charla", tematicas=["literatura"])
    _act("2026-09-03", 10, titulo="C", tipo="charla", tematicas=["ambiente"])
    d = panorama.armar("mes", HOY)
    assert d["tipos"][0] == {"clave": "charla", "etiqueta": "Charla / conversatorio",
                             "actividades": 2, "personas": 30, "promedio": 15}
    lit = next(t for t in d["tematicas"] if t["clave"] == "literatura")
    assert lit["actividades"] == 2 and lit["promedio"] == 30
    assert any("Literatura" in x["texto"] and "30 personas" in x["texto"] for x in d["destacados"])


def test_edades_dias_lugares_y_difusion(store):
    store["espacios"] = []
    sala = espacios.crear("Sala principal")
    _act("2026-09-12", 15, franjas={"6_12": 10, "30_59": 5}, espacio_id=sala["id"],
         difusion=["redes", "cartel"], hora_inicio="16:00", hora_fin="18:00")
    _act("2026-09-05", 25, titulo="B", difusion=["redes"], hora_inicio="16:30", hora_fin="18:00")
    d = panorama.armar("mes", HOY)
    assert {e["clave"]: e["personas"] for e in d["edades"]}["6_12"] == 10
    assert d["edades_con_dato"] == 1
    sabado_tarde = next(c for c in d["dias"] if c["dia"] == 5 and c["momento"] == "tarde")
    assert sabado_tarde["actividades"] == 2 and sabado_tarde["personas"] == 40
    assert d["lugares"][0]["etiqueta"] in ("Sala principal", "Otro lugar")
    assert d["difusion"][0] == {"clave": "redes", "etiqueta": "Redes", "actividades": 2}
    assert d["difusion_con_dato"] == 2
    assert any("sábado a la tarde" in x["texto"] for x in d["destacados"])


def test_talleres_mes_a_mes_y_alerta_si_se_vacian(store):
    _taller("2026-07", 15)
    _taller("2026-08", 12)
    _taller("2026-09", 9)
    _taller("2026-08", 8, titulo="Huerta")
    _taller("2026-09", 11, titulo="Huerta")
    d = panorama.armar("3m", HOY)
    radio = next(t for t in d["talleres"] if t["titulo"] == "Taller de radio")
    assert [m["participantes"] for m in radio["meses"]] == [15, 12, 9]
    assert radio["tendencia"] == "baja" and radio["encuentros"] == 12
    assert next(t for t in d["talleres"] if t["titulo"] == "Huerta")["tendencia"] == "crece"
    assert d["numeros"]["talleres"]["valor"] == 2
    assert d["numeros"]["participantes_talleres"]["valor"] == 15 + 11     # el mejor mes de cada uno
    alerta = [x for x in d["destacados"] if x.get("alerta")]
    assert len(alerta) == 1 and "de 15 participantes en julio 2026 a 9" in alerta[0]["texto"]


def test_no_expone_datos_sensibles(store):
    _act("2026-09-01", 10, titulo="Con problemas", incidente="Se rompió el proyector",
         incidente_atencion=True, valoracion=2, mejorar="Faltó calefacción",
         recaudacion="$ 50000", acceso="arancelada", monto="$ 3000")
    texto = json.dumps(panorama.armar("mes", HOY), ensure_ascii=False)
    for sensible in ("proyector", "calefacción", "caro@x.ar", "50000", "3000", "valoracion"):
        assert sensible not in texto, f"se filtró '{sensible}'"


# ── Endpoint ────────────────────────────────────────────────────────────────
@pytest.fixture
def como(store):
    def _entrar(rol):
        s = Sesion(sid="t", usuario="quien", nombre="Quien", rol=rol, permisos=permisos.permisos_de(rol))
        app.dependency_overrides[get_session] = lambda: s
        app.dependency_overrides[get_current_username] = lambda: s.usuario
        return TestClient(app)
    yield _entrar
    app.dependency_overrides.clear()


@pytest.mark.parametrize("rol", ["bibliotecaria", "comision", "subcomision"])
def test_la_ven_todos_los_roles(como, rol):
    r = como(rol).get("/api/panorama?periodo=anio")
    assert r.status_code == 200
    assert r.json()["periodo"]["etiqueta"] == "Este año" and "periodos" in r.json()


def test_periodo_invalido_da_422(como):
    assert como("bibliotecaria").get("/api/panorama?periodo=siempre").status_code == 422
