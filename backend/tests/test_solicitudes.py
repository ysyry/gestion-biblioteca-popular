"""Tests de solicitudes de espacio: repeticiones, superposiciones y estados."""
import pytest

from app import espacios, solicitudes as sol


@pytest.fixture
def sala(store):
    """Un espacio donde reservar, sobre una lista vacía.

    Se vacía primero porque la primera lectura siembra espacios de ejemplo, y acá
    queremos controlar exactamente qué hay.
    """
    store["espacios"] = []
    return espacios.crear("Sala principal", capacidad=40)


@pytest.fixture
def quien():
    return {"uid": "u1", "usuario": "prensa", "nombre": "Ana Paz", "subcomision": "Prensa"}


def _pedido(sala, **kw):
    base = {"titulo": "Taller de radio", "espacio_id": sala["id"],
            "inicio": "2026-03-10T18:00", "fin": "2026-03-10T20:00"}
    return {**base, **kw}


# ── Repeticiones ────────────────────────────────────────────────────────────
def test_una_sola_vez():
    f = sol.ocurrencias("2026-03-10T18:00", "2026-03-10T20:00")
    assert f == [{"inicio": "2026-03-10T18:00", "fin": "2026-03-10T20:00"}]


def test_semanal_respeta_el_tope():
    f = sol.ocurrencias("2026-03-10T18:00", "2026-03-10T20:00", "semanal", "2026-03-31")
    assert [x["inicio"][:10] for x in f] == ["2026-03-10", "2026-03-17", "2026-03-24", "2026-03-31"]


def test_quincenal():
    f = sol.ocurrencias("2026-03-10T18:00", "2026-03-10T20:00", "quincenal", "2026-04-30")
    assert [x["inicio"][:10] for x in f] == ["2026-03-10", "2026-03-24", "2026-04-07", "2026-04-21"]


def test_mensual_conserva_el_dia():
    f = sol.ocurrencias("2026-03-15T18:00", "2026-03-15T20:00", "mensual", "2026-06-30")
    assert [x["inicio"][:10] for x in f] == ["2026-03-15", "2026-04-15", "2026-05-15", "2026-06-15"]


def test_mensual_desde_un_31_cae_en_el_ultimo_dia():
    """31 de enero + 1 mes no existe: se usa el último día de febrero, no se saltea."""
    f = sol.ocurrencias("2026-01-31T10:00", "2026-01-31T11:00", "mensual", "2026-04-30")
    assert [x["inicio"][:10] for x in f] == ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"]


def test_la_duracion_se_mantiene_en_cada_repeticion():
    f = sol.ocurrencias("2026-03-10T18:00", "2026-03-10T20:30", "semanal", "2026-03-24")
    assert all(x["inicio"][11:] == "18:00" and x["fin"][11:] == "20:30" for x in f)


def test_repeticion_cruzando_fin_de_anio():
    f = sol.ocurrencias("2026-12-28T18:00", "2026-12-28T19:00", "semanal", "2027-01-11")
    assert [x["inicio"][:10] for x in f] == ["2026-12-28", "2027-01-04", "2027-01-11"]


def test_fin_antes_que_inicio_no_va():
    with pytest.raises(sol.ErrorSolicitud, match="posterior"):
        sol.ocurrencias("2026-03-10T20:00", "2026-03-10T18:00")


def test_repeticion_sin_hasta_no_va():
    with pytest.raises(sol.ErrorSolicitud, match="hasta cuándo"):
        sol.ocurrencias("2026-03-10T18:00", "2026-03-10T20:00", "semanal")


def test_hasta_anterior_al_inicio_no_va():
    with pytest.raises(sol.ErrorSolicitud, match="anterior"):
        sol.ocurrencias("2026-03-10T18:00", "2026-03-10T20:00", "semanal", "2026-01-01")


def test_repeticion_desconocida():
    with pytest.raises(sol.ErrorSolicitud, match="Repetición desconocida"):
        sol.ocurrencias("2026-03-10T18:00", "2026-03-10T20:00", "cada tanto", "2026-04-01")


def test_hay_un_tope_de_ocurrencias():
    """Un 'hasta' lejísimos no puede generar miles de fechas."""
    f = sol.ocurrencias("2026-01-01T10:00", "2026-01-01T11:00", "semanal", "2099-12-31")
    assert len(f) == sol.MAX_OCURRENCIAS


@pytest.mark.parametrize("fecha", ["", "no es fecha", "2026-13-45T99:99", None])
def test_fechas_invalidas(fecha):
    with pytest.raises(sol.ErrorSolicitud):
        sol.ocurrencias(fecha, "2026-03-10T20:00")


# ── Superposiciones ─────────────────────────────────────────────────────────
def test_no_hay_conflicto_si_no_hay_nada(sala, quien):
    assert sol.conflictos(sala["id"], [{"inicio": "2026-03-10T18:00", "fin": "2026-03-10T20:00"}]) == []


def test_detecta_superposicion_con_una_aprobada(sala, quien):
    s = sol.crear(_pedido(sala), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    ch = sol.conflictos(sala["id"], [{"inicio": "2026-03-10T19:00", "fin": "2026-03-10T21:00"}])
    assert len(ch) == 1
    assert ch[0]["titulo"] == "Taller de radio"


def test_pendiente_no_ocupa_el_espacio(sala, quien):
    """Solo lo aprobado reserva. Dos pedidos pueden competir por el mismo horario."""
    sol.crear(_pedido(sala), quien)
    assert sol.conflictos(sala["id"], [{"inicio": "2026-03-10T18:00", "fin": "2026-03-10T20:00"}]) == []


def test_rechazada_y_cancelada_liberan_el_espacio(sala, quien):
    s = sol.crear(_pedido(sala), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    sol.cancelar(s["id"], por="flor")
    assert sol.conflictos(sala["id"], [{"inicio": "2026-03-10T18:00", "fin": "2026-03-10T20:00"}]) == []


def test_pegadas_no_se_pisan(sala, quien):
    """Una termina 20:00 y la otra empieza 20:00: conviven."""
    s = sol.crear(_pedido(sala), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    assert sol.conflictos(sala["id"], [{"inicio": "2026-03-10T20:00", "fin": "2026-03-10T22:00"}]) == []


def test_otro_espacio_no_es_conflicto(sala, quien):
    otra = espacios.crear("Patio")
    s = sol.crear(_pedido(sala), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    assert sol.conflictos(otra["id"], [{"inicio": "2026-03-10T18:00", "fin": "2026-03-10T20:00"}]) == []


def test_conflicto_con_una_de_las_repeticiones(sala, quien):
    s = sol.crear(_pedido(sala, repeticion="semanal", hasta="2026-03-31"), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    ch = sol.conflictos(sala["id"], [{"inicio": "2026-03-24T19:00", "fin": "2026-03-24T21:00"}])
    assert len(ch) == 1 and ch[0]["inicio"][:10] == "2026-03-24"


def test_excluir_la_propia_al_reprogramar(sala, quien):
    s = sol.crear(_pedido(sala), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    mismas = [{"inicio": "2026-03-10T18:00", "fin": "2026-03-10T20:00"}]
    assert sol.conflictos(sala["id"], mismas, excluir_id=s["id"]) == []


# ── Alta ────────────────────────────────────────────────────────────────────
def test_crear_deja_la_solicitud_pendiente(sala, quien):
    s = sol.crear(_pedido(sala, personas="25", necesidades=["proyector", " ", "sillas"]), quien)
    assert s["estado"] == sol.PENDIENTE
    assert s["personas"] == 25
    assert s["necesidades"] == ["proyector", "sillas"]       # los vacíos se descartan
    assert s["solicitante"]["subcomision"] == "Prensa"
    assert len(s["historial"]) == 1


def test_sin_titulo_no_va(sala, quien):
    with pytest.raises(sol.ErrorSolicitud, match="qué actividad"):
        sol.crear(_pedido(sala, titulo="  "), quien)


def test_espacio_inexistente_no_va(sala, quien):
    with pytest.raises(espacios.ErrorEspacio, match="no existe"):
        sol.crear(_pedido(sala, espacio_id="fantasma"), quien)


def test_espacio_dado_de_baja_no_va(sala, quien):
    espacios.actualizar(sala["id"], {"activo": False})
    with pytest.raises(espacios.ErrorEspacio, match="de baja"):
        sol.crear(_pedido(sala), quien)


def test_personas_no_numerica(sala, quien):
    with pytest.raises(sol.ErrorSolicitud, match="número"):
        sol.crear(_pedido(sala, personas="muchas"), quien)


# ── Estados ─────────────────────────────────────────────────────────────────
def test_aprobar_sin_cambios(sala, quien):
    s = sol.crear(_pedido(sala), quien)
    r = sol.resolver(s["id"], "aprobar", por="flor")
    assert r["estado"] == sol.APROBADA
    assert r["pedido_original"] is None       # no se movió nada
    assert r["google_pendiente"] is True      # queda por publicar


def test_aprobar_moviendo_la_fecha_guarda_lo_que_se_pidio(sala, quien):
    """El caso normal: se aprueba, pero en otro horario."""
    s = sol.crear(_pedido(sala), quien)
    r = sol.resolver(s["id"], "aprobar", por="flor",
                     cambios={"inicio": "2026-03-11T18:00", "fin": "2026-03-11T20:00"})
    assert r["estado"] == sol.APROBADA
    assert r["inicio"] == "2026-03-11T18:00"
    assert r["pedido_original"]["inicio"] == "2026-03-10T18:00"
    assert r["fechas"][0]["inicio"] == "2026-03-11T18:00"
    assert "Ajustó el pedido al aprobar" in r["historial"][-2]["que"]


def test_aprobar_cambiando_de_espacio(sala, quien):
    patio = espacios.crear("Patio")
    s = sol.crear(_pedido(sala), quien)
    r = sol.resolver(s["id"], "aprobar", por="flor", cambios={"espacio_id": patio["id"]})
    assert r["espacio_id"] == patio["id"]
    assert r["pedido_original"]["espacio_id"] == sala["id"]


def test_rechazar_exige_motivo(sala, quien):
    s = sol.crear(_pedido(sala), quien)
    with pytest.raises(sol.ErrorSolicitud, match="motivo"):
        sol.resolver(s["id"], "rechazar", por="flor")
    r = sol.resolver(s["id"], "rechazar", por="flor", motivo="Ese día está el cineclub")
    assert r["estado"] == sol.RECHAZADA
    assert "cineclub" in r["resolucion"]["motivo"]


def test_observar_devuelve_al_solicitante_y_puede_editar(sala, quien):
    s = sol.crear(_pedido(sala), quien)
    sol.resolver(s["id"], "observar", por="flor", motivo="¿Puede ser más temprano?")
    assert sol.obtener(s["id"])["estado"] == sol.OBSERVACIONES
    r = sol.editar(s["id"], _pedido(sala, inicio="2026-03-10T16:00", fin="2026-03-10T18:00"),
                   por="prensa")
    assert r["estado"] == sol.PENDIENTE        # vuelve a la cola
    assert r["inicio"] == "2026-03-10T16:00"


def test_una_aprobada_ya_no_se_edita(sala, quien):
    s = sol.crear(_pedido(sala), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    with pytest.raises(sol.ErrorSolicitud, match="no se puede editar"):
        sol.editar(s["id"], _pedido(sala), por="prensa")


def test_una_rechazada_no_se_vuelve_a_resolver(sala, quien):
    s = sol.crear(_pedido(sala), quien)
    sol.resolver(s["id"], "rechazar", por="flor", motivo="no")
    with pytest.raises(sol.ErrorSolicitud, match="ya está"):
        sol.resolver(s["id"], "aprobar", por="flor")


def test_decision_desconocida(sala, quien):
    s = sol.crear(_pedido(sala), quien)
    with pytest.raises(sol.ErrorSolicitud, match="Decisión desconocida"):
        sol.resolver(s["id"], "tal vez", por="flor")


def test_cancelar_una_aprobada(sala, quien):
    s = sol.crear(_pedido(sala), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    r = sol.cancelar(s["id"], por="flor", motivo="Se suspendió")
    assert r["estado"] == sol.CANCELADA
    with pytest.raises(sol.ErrorSolicitud, match="ya estaba cancelada"):
        sol.cancelar(s["id"], por="flor")


def test_solicitud_inexistente(sala, quien):
    with pytest.raises(sol.ErrorSolicitud, match="no existe"):
        sol.resolver("fantasma", "aprobar", por="flor")


# ── Listados ────────────────────────────────────────────────────────────────
def test_listar_pone_primero_lo_que_espera_respuesta(sala, quien):
    a = sol.crear(_pedido(sala, titulo="A"), quien)
    b = sol.crear(_pedido(sala, titulo="B", inicio="2026-03-11T18:00", fin="2026-03-11T20:00"), quien)
    sol.resolver(a["id"], "aprobar", por="flor")
    assert [s["titulo"] for s in sol.listar()] == ["B", "A"]


def test_listar_filtrando_por_subcomision(sala, quien):
    sol.crear(_pedido(sala, titulo="De prensa"), quien)
    sol.crear(_pedido(sala, titulo="De cultura"),
              {"uid": "u2", "usuario": "cultura", "nombre": "Beto", "subcomision": "Cultura"})
    assert [s["titulo"] for s in sol.listar(subcomision="Prensa")] == ["De prensa"]


def test_contador_de_pendientes(sala, quien):
    a = sol.crear(_pedido(sala, titulo="A"), quien)
    sol.crear(_pedido(sala, titulo="B"), quien)
    assert sol.pendientes() == 2
    sol.resolver(a["id"], "aprobar", por="flor")
    assert sol.pendientes() == 1


# ── Vista de calendario ─────────────────────────────────────────────────────
def test_eventos_solo_trae_lo_aprobado(sala, quien):
    import datetime as dt
    a = sol.crear(_pedido(sala, titulo="Aprobada"), quien)
    sol.crear(_pedido(sala, titulo="Pendiente"), quien)
    sol.resolver(a["id"], "aprobar", por="flor")
    ev = sol.eventos(dt.date(2026, 3, 1), dt.date(2026, 3, 31))
    assert [e["titulo"] for e in ev] == ["Aprobada"]
    assert ev[0]["lugar"] == "Sala principal"
    assert ev[0]["responsable"] == "Prensa"


def test_eventos_expande_las_repeticiones(sala, quien):
    import datetime as dt
    s = sol.crear(_pedido(sala, repeticion="semanal", hasta="2026-03-31"), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    assert len(sol.eventos(dt.date(2026, 3, 1), dt.date(2026, 3, 31))) == 4


def test_eventos_respeta_el_rango(sala, quien):
    import datetime as dt
    s = sol.crear(_pedido(sala, repeticion="semanal", hasta="2026-03-31"), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    assert len(sol.eventos(dt.date(2026, 3, 1), dt.date(2026, 3, 15))) == 1
    assert sol.eventos(dt.date(2026, 5, 1), dt.date(2026, 5, 31)) == []
