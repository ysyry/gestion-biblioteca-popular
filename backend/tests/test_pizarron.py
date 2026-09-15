"""Tests del pizarrón semanal: armado de la semana, arrastre de tareas y permisos."""
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import permisos, pizarron
from app.auth import Sesion, get_current_username, get_session
from app.main import app


class Reloj:
    """Hora de la biblioteca controlada por el test."""
    def __init__(self):
        self.ahora = datetime(2026, 9, 16, 12, 0, tzinfo=pizarron.TZ)     # miércoles

    def ir(self, *args):
        self.ahora = datetime(*args, 12, 0, tzinfo=pizarron.TZ)


@pytest.fixture
def reloj(store, monkeypatch):
    r = Reloj()
    monkeypatch.setattr(pizarron, "hoy", lambda: r.ahora.date())
    monkeypatch.setattr(pizarron, "ahora_iso",
                        lambda: r.ahora.astimezone(timezone.utc).isoformat(timespec="seconds"))
    return r


def _ids(grupo):
    return [n["texto"] for n in grupo]


def _crear(texto, usuario="laura", **kw):
    return pizarron.crear({"texto": texto, **kw}, usuario, usuario.title())


# ── Crear y validar ─────────────────────────────────────────────────────────
def test_crear_toma_la_semana_de_hoy_o_del_dia(reloj, store):
    a = _crear("Falta papel")
    b = _crear("Viene la escuela 45", dia="2026-09-24")
    assert a["semana"] == "2026-09-14" and a["id"].startswith("2026-")
    assert b["semana"] == "2026-09-21" and b["dia"] == "2026-09-24"
    assert len(store["pizarron_2026"]) == 2


@pytest.mark.parametrize("datos, error", [
    ({"texto": "  "}, "vacía"),
    ({"texto": "x", "tipo": "urgente"}, "Tipo"),
    ({"texto": "x", "color": "fucsia"}, "Color"),
    ({"texto": "x" * 2001}, "larga"),
])
def test_crear_valida(reloj, datos, error):
    with pytest.raises(pizarron.ErrorPizarron, match=error):
        pizarron.crear(datos, "laura", "Laura")


def test_el_dia_tiene_que_estar_en_la_semana_de_la_nota(reloj):
    n = _crear("Reunión", dia="2026-09-17")
    with pytest.raises(pizarron.ErrorPizarron, match="dentro de la semana"):
        pizarron.editar(n["id"], {"dia": "2026-09-22"}, "laura")


# ── La semana ───────────────────────────────────────────────────────────────
def test_semana_agrupa_fijadas_general_y_dias(reloj):
    _crear("Horario de verano", fijada=True)
    _crear("Falta papel")
    _crear("Jueves viene la escuela", tipo="recordatorio", dia="2026-09-17")
    _crear("Otra semana", dia="2026-09-22")
    d = pizarron.semana("2026-09-16", "laura")
    assert d["semana"] == "2026-09-14" and d["hasta"] == "2026-09-20"
    assert _ids(d["fijadas"]) == ["Horario de verano"]
    assert _ids(d["general"]) == ["Falta papel"]
    assert d["dias"] == [{"fecha": "2026-09-17", "items": d["dias"][0]["items"]}]
    assert _ids(d["dias"][0]["items"]) == ["Jueves viene la escuela"]


def test_tarea_no_hecha_pasa_sola_a_la_semana_siguiente(reloj):
    _crear("Revisar planilla de cuotas", tipo="tarea")
    _crear("Aviso de esta semana")
    reloj.ir(2026, 9, 23)
    d = pizarron.semana(None, "laura")
    assert d["semana"] == "2026-09-21"
    assert _ids(d["general"]) == ["Revisar planilla de cuotas"]      # el aviso no pasa
    assert d["general"][0]["viene_de"] == "2026-09-14"


def test_tarea_hecha_queda_en_la_semana_en_que_se_tildo(reloj):
    t = _crear("Comprar papel", tipo="tarea")
    reloj.ir(2026, 9, 23)                                  # la semana siguiente
    pizarron.marcar_hecha(t["id"], True, "flor", "Flor")

    semana_1 = pizarron.semana("2026-09-14", "laura")      # cuando se escribió: todavía pendiente
    assert _ids(semana_1["general"]) == ["Comprar papel"] and semana_1["hechas"] == []
    semana_2 = pizarron.semana("2026-09-21", "laura")      # cuando se tildó
    assert _ids(semana_2["hechas"]) == ["Comprar papel"] and semana_2["general"] == []
    assert semana_2["hechas"][0]["hecha"]["nombre"] == "Flor"
    semana_3 = pizarron.semana("2026-09-28", "laura")      # después: ya no aparece
    assert semana_3["general"] == [] and semana_3["hechas"] == []


def test_tarea_pendiente_cruza_el_fin_de_anio(reloj):
    reloj.ir(2026, 12, 30)
    _crear("Inventario de fin de año", tipo="tarea")
    reloj.ir(2027, 1, 6)
    d = pizarron.semana(None, "laura")
    assert _ids(d["general"]) == ["Inventario de fin de año"]


def test_fijada_sigue_arriba_en_semanas_siguientes(reloj):
    _crear("Sábados cerrado en enero", fijada=True)
    assert _ids(pizarron.semana("2026-10-05", "laura")["fijadas"]) == ["Sábados cerrado en enero"]
    assert pizarron.semana("2026-09-07", "laura")["fijadas"] == []     # antes de escribirla, no


def test_para_mi_y_puede_editar(reloj):
    _crear("Llamar a la imprenta", usuario="flor", para="LAURA")
    n = pizarron.semana(None, "laura", "Laura")["general"][0]
    assert n["para_mi"] is True and n["puede_editar"] is False
    assert pizarron.semana(None, "flor", "Flor")["general"][0]["puede_editar"] is True


# ── Permisos sobre las notas ────────────────────────────────────────────────
def test_solo_quien_escribio_edita_o_borra(reloj):
    n = _crear("Mía", usuario="laura")
    with pytest.raises(pizarron.SinPermiso):
        pizarron.editar(n["id"], {"texto": "Pisada"}, "flor")
    with pytest.raises(pizarron.SinPermiso):
        pizarron.borrar(n["id"], "flor")
    assert pizarron.editar(n["id"], {"texto": "Corregida"}, "Laura")["texto"] == "Corregida"   # sin distinguir mayúsculas
    pizarron.borrar(n["id"], "laura")
    with pytest.raises(pizarron.NoEncontrada):
        pizarron.editar(n["id"], {"texto": "x"}, "laura")


def test_solo_las_tareas_se_tildan(reloj):
    n = _crear("Aviso")
    with pytest.raises(pizarron.ErrorPizarron, match="tareas"):
        pizarron.marcar_hecha(n["id"], True, "flor", "Flor")


def test_si_deja_de_ser_tarea_pierde_el_tilde(reloj):
    t = _crear("Algo", tipo="tarea")
    pizarron.marcar_hecha(t["id"], True, "flor", "Flor")
    assert pizarron.editar(t["id"], {"tipo": "aviso"}, "laura")["hecha"] is None


def test_respuestas(reloj):
    n = _crear("¿Alguien sabe dónde está la llave?")
    n = pizarron.responder(n["id"], "La tiene Flor", "flor", "Flor")
    r = n["respuestas"][0]
    with pytest.raises(pizarron.SinPermiso):
        pizarron.borrar_respuesta(n["id"], r["id"], "laura")
    assert pizarron.borrar_respuesta(n["id"], r["id"], "flor")["respuestas"] == []
    with pytest.raises(pizarron.ErrorPizarron, match="vacía"):
        pizarron.responder(n["id"], " ", "flor", "Flor")


def test_buscar_en_notas_y_respuestas(reloj):
    a = _crear("Falta papel para la impresora")
    _crear("Otra cosa")
    pizarron.responder(a["id"], "Compré tóner también", "flor", "Flor")
    assert _ids(pizarron.buscar("PAPEL", "laura")) == ["Falta papel para la impresora"]
    assert _ids(pizarron.buscar("tóner", "laura")) == ["Falta papel para la impresora"]
    assert pizarron.buscar("  ", "laura") == []


def test_novedades_desde_la_ultima_visita(reloj):
    pizarron.registrar_visita("laura", "Laura")
    reloj.ir(2026, 9, 17)
    n = _crear("Nota de Flor", usuario="flor")
    _crear("Nota mía", usuario="laura")
    pizarron.responder(n["id"], "Respuesta de Caro", "caro", "Caro")
    assert pizarron.novedades("laura") == 2                # la nota de Flor y la respuesta de Caro
    pizarron.registrar_visita("Laura", "Laura")
    assert pizarron.novedades("laura") == 0
    assert "Laura" in pizarron.personas()


# ── Endpoints ───────────────────────────────────────────────────────────────
@pytest.fixture
def como(reloj):
    def _entrar(rol="bibliotecaria", usuario="laura"):
        s = Sesion(sid="t", usuario=usuario, nombre=usuario.title(), rol=rol,
                   permisos=permisos.permisos_de(rol))
        app.dependency_overrides[get_session] = lambda: s
        app.dependency_overrides[get_current_username] = lambda: s.usuario
        return TestClient(app)
    yield _entrar
    app.dependency_overrides.clear()


def test_la_comision_y_las_subcomisiones_no_ven_el_pizarron(como):
    for rol in ("comision", "subcomision"):
        c = como(rol)
        assert c.get("/api/pizarron").status_code == 403
        assert c.post("/api/pizarron", json={"texto": "x"}).status_code == 403
        assert "pizarron" not in [s["id"] for s in permisos.secciones_de(rol)]
    assert "pizarron" in [s["id"] for s in permisos.secciones_de("bibliotecaria")]


def test_endpoints_de_punta_a_punta(como):
    laura, flor = como(usuario="laura"), None
    n = laura.post("/api/pizarron", json={"texto": "Revisar goteras", "tipo": "tarea"}).json()
    assert laura.post("/api/pizarron", json={"texto": ""}).status_code == 400

    flor = como(usuario="flor")
    assert flor.put(f"/api/pizarron/{n['id']}", json={"texto": "x"}).status_code == 403
    assert flor.delete(f"/api/pizarron/{n['id']}").status_code == 403
    assert flor.post(f"/api/pizarron/{n['id']}/hecha", json={"hecha": True}).json()["hecha"]["nombre"] == "Flor"
    assert flor.post(f"/api/pizarron/{n['id']}/respuestas", json={"texto": "Listo"}).status_code == 200
    assert flor.get("/api/pizarron/buscar?q=goteras").json()["items"][0]["id"] == n["id"]
    d = flor.get("/api/pizarron?semana=2026-09-18").json()
    assert d["semana"] == "2026-09-14" and d["hechas"][0]["id"] == n["id"]

    laura = como(usuario="laura")
    assert laura.get("/api/pizarron/novedades").json() == {"nuevas": 1}     # la respuesta de Flor
    assert laura.delete(f"/api/pizarron/{n['id']}").json() == {"ok": True}
    assert laura.delete(f"/api/pizarron/{n['id']}").status_code == 404
    assert laura.get("/api/pizarron?semana=nada").status_code == 400
