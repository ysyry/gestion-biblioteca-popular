"""Tests del registro de actividades: validación, links, duplicados y exportación."""
import pytest

from app import espacios, registro


def _actividad(**kw):
    return {"titulo": "Charla sobre huerta", "tipo": "charla", "fecha": "2026-09-10",
            "hora_inicio": "18:00", "hora_fin": "20:00", "personas_total": 24,
            "quien_completa": {"nombre": "Flor", "contacto": "flor@bpob.ar"}, **kw}


def _taller(**kw):
    return {"titulo": "Taller de radio", "mes": "2026-08", "encuentros": 4, "participantes": 12,
            "quien_completa": {"nombre": "Caro"}, **kw}


# ── Validación de una actividad ─────────────────────────────────────────────
def test_actividad_valida(store):
    d = registro.limpiar_actividad(_actividad(tematicas=["literatura", "comunidad"],
                                              difusion=["redes", "cartel"], valoracion=4))
    assert d["titulo"] == "Charla sobre huerta" and d["personas_total"] == 24
    assert d["tematicas"] == ["literatura", "comunidad"] and d["valoracion"] == 4
    assert d["acceso"] == "gratuita" and d["modalidad"] == "presencial"
    assert d["quien_completa"] == {"nombre": "Flor", "contacto": "flor@bpob.ar"}


@pytest.mark.parametrize("cambio, error", [
    ({"titulo": " "}, "nombre de la actividad"),
    ({"tipo": ""}, "tipo"),
    ({"tipo": "fiesta"}, "desconocida"),
    ({"fecha": "10/09/2026"}, "fecha"),
    ({"hora_inicio": ""}, "hora de inicio"),
    ({"hora_inicio": "25:00"}, "hora"),
    ({"hora_fin": "17:00"}, "anterior"),
    ({"personas_total": None}, "cantidad de personas"),
    ({"personas_total": "muchas"}, "número"),
    ({"quien_completa": {"nombre": ""}}, "tu nombre"),
    ({"tematicas": ["ninguna"]}, "desconocida"),
])
def test_actividad_invalida(store, cambio, error):
    with pytest.raises(registro.ErrorRegistro, match=error):
        registro.limpiar_actividad(_actividad(**cambio))


def test_el_total_sale_de_las_edades_si_no_lo_ponen(store):
    d = registro.limpiar_actividad(_actividad(personas_total=None,
                                              franjas={"6_12": 8, "30_59": 5, "60": 0}))
    assert d["personas_total"] == 13
    assert d["franjas"] == {"6_12": 8, "30_59": 5}          # el cero no se guarda


def test_espacio_inexistente_no_rompe(store):
    store["espacios"] = []
    sala = espacios.crear("Sala principal")
    assert registro.limpiar_actividad(_actividad(espacio_id=sala["id"]))["espacio_id"] == sala["id"]
    assert registro.limpiar_actividad(_actividad(espacio_id="inventado"))["espacio_id"] == ""


def test_incidente_que_requiere_atencion(store):
    d = registro.limpiar_actividad(_actividad(incidente="Se rompió el proyector", incidente_atencion=True))
    assert d["incidente"] == {"texto": "Se rompió el proyector", "requiere_atencion": True}
    vacio = registro.limpiar_actividad(_actividad(incidente="", incidente_atencion=True))
    assert vacio["incidente"]["requiere_atencion"] is False     # sin texto no hay nada que atender


# ── Resumen mensual de un taller ────────────────────────────────────────────
def test_taller_valido(store):
    d = registro.limpiar_taller(_taller(suspendidos=1, motivo_suspension="feriado", altas=2, bajas=1))
    assert d["mes"] == "2026-08" and d["encuentros"] == 4 and d["participantes"] == 12
    assert d["suspendidos"] == 1 and d["altas"] == 2


@pytest.mark.parametrize("cambio, error", [
    ({"mes": "agosto"}, "mes"),
    ({"mes": "2026-13"}, "mes"),
    ({"encuentros": None}, "encuentros"),
    ({"participantes": None}, "personas participaron"),
    ({"titulo": ""}, "nombre del taller"),
])
def test_taller_invalido(store, cambio, error):
    with pytest.raises(registro.ErrorRegistro, match=error):
        registro.limpiar_taller(_taller(**cambio))


# ── Links ───────────────────────────────────────────────────────────────────
def test_link_general_y_de_actividad(store):
    g = registro.crear_link({"clase": "general"}, "Flor")
    a = registro.crear_link({"clase": "actividad", "titulo": "Charla de huerta",
                             "precarga": {"titulo": "Charla de huerta", "fecha": "2026-09-10",
                                          "hora_inicio": "18:00", "tipo": "charla"}}, "Flor")
    assert len(g["token"]) > 20 and g["abierto"] is True
    assert a["precarga"]["fecha"] == "2026-09-10"
    assert registro.por_token(g["token"])["id"] == g["id"]


def test_el_link_general_no_precarga_nada(store):
    """Su nombre es para que la biblioteca lo reconozca, no el de una actividad:
    si se precargara, todas las actividades entrarían con el mismo título."""
    l = registro.crear_link({"clase": "general", "titulo": "Cartel de la sala",
                             "precarga": {"titulo": "Cartel de la sala"}}, "Flor")
    assert l["precarga"] == {}


def test_link_sin_nombre_solo_vale_para_el_general(store):
    with pytest.raises(registro.ErrorRegistro, match="nombre"):
        registro.crear_link({"clase": "taller"}, "Flor")


def test_link_cerrado_o_vencido_no_se_puede_usar(store):
    l = registro.crear_link({"clase": "general"}, "Flor")
    registro.actualizar_link(l["id"], {"abierto": False})
    with pytest.raises(registro.LinkCerrado, match="cerrado"):
        registro.por_token(l["token"])
    registro.actualizar_link(l["id"], {"abierto": True, "vence": "2020-01-01"})
    with pytest.raises(registro.LinkCerrado, match="venció"):
        registro.por_token(l["token"])


def test_token_inventado(store):
    with pytest.raises(registro.NoEncontrado):
        registro.por_token("cualquier-cosa")


# ── Guardar, corregir y resolver ────────────────────────────────────────────
def test_lo_que_entra_por_link_queda_recibido(store):
    l = registro.crear_link({"clase": "general"}, "Flor")
    r = registro.guardar("actividad", _actividad(), link=l)
    assert r["estado"] == registro.RECIBIDO and r["id"].startswith("2026-")
    assert store["registros_actividad_2026"][0]["id"] == r["id"]
    assert registro.links()[0]["usos"] == 1
    assert registro.pendientes() == 1


def test_lo_que_carga_la_biblioteca_ya_queda_validado(store):
    r = registro.guardar("actividad", _actividad(), cargado_por="Laura")
    assert r["estado"] == registro.VALIDADO and r["validado_por"] == "Laura"
    assert registro.pendientes() == 0


def test_avisa_si_parece_repetido(store):
    registro.guardar("actividad", _actividad(), cargado_por="Laura")
    otra = registro.guardar("actividad", _actividad(titulo="charla sobre  HUERTA"), cargado_por="Laura")
    assert len(otra["posibles_duplicados"]) == 1
    distinta = registro.guardar("actividad", _actividad(fecha="2026-09-11"), cargado_por="Laura")
    assert distinta["posibles_duplicados"] == []


def test_corregir_deja_constancia(store):
    r = registro.guardar("actividad", _actividad(), cargado_por="Laura")
    r = registro.corregir(r["id"], {"personas_total": 30}, "Laura")
    assert r["datos"]["personas_total"] == 30
    assert r["historial"][0]["cambios"] == ["personas_total"]
    assert r["historial"][0]["antes"]["personas_total"] == 24


def test_corregir_la_fecha_a_otro_anio_muda_el_registro(store):
    r = registro.guardar("actividad", _actividad(), cargado_por="Laura")
    r = registro.corregir(r["id"], {"fecha": "2025-12-30"}, "Laura")
    assert store["registros_actividad_2026"] == []
    assert [x["id"] for x in store["registros_actividad_2025"]] == [r["id"]]
    assert registro.obtener(r["id"])["datos"]["fecha"] == "2025-12-30"


def test_validar_y_descartar(store):
    l = registro.crear_link({"clase": "general"}, "Flor")
    r = registro.guardar("actividad", _actividad(), link=l)
    validado = registro.resolver(r["id"], registro.VALIDADO, "Laura")
    assert validado["estado"] == registro.VALIDADO and validado["validado_por"] == "Laura"
    with pytest.raises(registro.ErrorRegistro, match="por qué"):
        registro.resolver(r["id"], registro.DESCARTADO, "Laura")
    d = registro.resolver(r["id"], registro.DESCARTADO, "Laura", "prueba duplicada")
    assert d["estado"] == registro.DESCARTADO and d["motivo"] == "prueba duplicada"
    with pytest.raises(registro.ErrorRegistro, match="Estado"):
        registro.resolver(r["id"], "archivado", "Laura")


def test_listar_filtra_y_ordena(store):
    registro.guardar("actividad", _actividad(fecha="2026-09-01"), cargado_por="Laura")
    nueva = registro.guardar("actividad", _actividad(fecha="2026-09-20"), cargado_por="Laura")
    taller = registro.guardar("taller", _taller(), cargado_por="Laura")
    assert [r["id"] for r in registro.listar()][0] == nueva["id"]     # la más nueva primero
    assert [r["id"] for r in registro.listar(clase="taller")] == [taller["id"]]
    assert len(registro.listar(desde="2026-09-15")) == 1
    assert registro.cuando_de(taller) == "2026-08-28"                 # el mes ordena junto a las fechas


def test_lo_que_requiere_atencion(store):
    registro.guardar("actividad", _actividad(incidente="Se rompió una silla", incidente_atencion=True),
                     cargado_por="Laura")
    r2 = registro.guardar("actividad", _actividad(titulo="Otra", incidente="Se rompió el proyector",
                                                  incidente_atencion=True), cargado_por="Laura")
    assert len(registro.con_atencion()) == 2
    registro.resolver(r2["id"], registro.DESCARTADO, "Laura", "duplicado")
    assert len(registro.con_atencion()) == 1                          # lo descartado no molesta más


# ── Exportación ─────────────────────────────────────────────────────────────
def test_export_csv_tiene_las_dos_clases(store):
    registro.guardar("actividad", _actividad(franjas={"6_12": 10}, tematicas=["infancias"]), cargado_por="Laura")
    registro.guardar("taller", _taller(), cargado_por="Laura")
    csv = registro.exportar_csv(registro.listar())
    lineas = csv.strip().splitlines()
    assert lineas[0].startswith("id,clase,estado,fecha_o_mes,titulo")
    assert "edad_6_12" in lineas[0]
    assert any("Charla sobre huerta" in l and "Infancias" in l for l in lineas[1:])
    assert any("Taller de radio" in l and "2026-08" in l for l in lineas[1:])
