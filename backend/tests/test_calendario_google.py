"""Tests de la publicación de reservas en Google Calendar, contra un Google de mentira."""
import pytest

from app import agenda, calendario_google as gcal, espacios, solicitudes as sol


class _Resp:
    def __init__(self, status_code, cuerpo=None):
        self.status_code = status_code
        self._cuerpo = cuerpo or {}
        self.text = str(self._cuerpo)

    def json(self):
        return self._cuerpo


class GoogleFalso:
    """Se porta como la API de Google Calendar en lo que la app usa.

    Como Google, un evento borrado no desaparece: queda "cancelled" y ocupa su id.
    """

    def __init__(self, error_desde: int | None = None, status_error: int = 500):
        self.eventos: dict[str, dict] = {}
        self.llamadas: list[tuple[str, str]] = []
        self.error_desde = error_desde          # a partir de qué llamada falla
        self.status_error = status_error

    def _falla(self):
        return self.error_desde is not None and len(self.llamadas) > self.error_desde

    def put(self, url, json, timeout):
        eid = url.rsplit("/", 1)[1]
        self.llamadas.append(("PUT", eid))
        if self._falla():
            return _Resp(self.status_error, {"error": {"message": "algo salió mal"}})
        if eid not in self.eventos:
            return _Resp(404)
        self.eventos[eid] = dict(json)          # también revive uno cancelado
        return _Resp(200)

    def post(self, url, json, timeout):
        self.llamadas.append(("POST", json["id"]))
        if self._falla():
            return _Resp(self.status_error, {"error": {"message": "algo salió mal"}})
        if json["id"] in self.eventos:
            return _Resp(409)
        self.eventos[json["id"]] = dict(json)
        return _Resp(200)

    def delete(self, url, timeout):
        eid = url.rsplit("/", 1)[1]
        self.llamadas.append(("DELETE", eid))
        if self._falla():
            return _Resp(self.status_error, {"error": {"message": "algo salió mal"}})
        ev = self.eventos.get(eid)
        if ev is None or ev.get("status") == "cancelled":
            return _Resp(410)
        ev["status"] = "cancelled"
        return _Resp(204)

    def vivos(self) -> list[dict]:
        return sorted((e for e in self.eventos.values() if e["status"] == "confirmed"),
                      key=lambda e: e["id"])


@pytest.fixture
def google(monkeypatch):
    g = GoogleFalso()
    monkeypatch.setattr(gcal, "configurado", lambda: True)
    monkeypatch.setattr(gcal, "calendario_id", lambda: "biblio@gmail.com")
    monkeypatch.setattr(gcal, "_sesion", lambda: g)
    return g


@pytest.fixture
def sala(store):
    store["espacios"] = []
    return espacios.crear("Sala principal", capacidad=40)


@pytest.fixture
def quien():
    return {"uid": "u1", "usuario": "prensa", "nombre": "Ana Paz", "subcomision": "Prensa"}


def _pedido(sala, **kw):
    return {"titulo": "Taller de radio", "espacio_id": sala["id"], "descripcion": "Radio abierta",
            "inicio": "2026-03-10T18:00", "fin": "2026-03-10T20:00", **kw}


def _semanal(sala, hasta="2026-03-31"):
    return _pedido(sala, repeticion="semanal", hasta=hasta)


# ── A qué calendario ────────────────────────────────────────────────────────
def test_el_calendario_sale_de_la_direccion_ical(monkeypatch):
    monkeypatch.setenv("CALENDAR_1_NAME", "Talleres")
    monkeypatch.setenv("CALENDAR_1_URL", "https://calendar.google.com/calendar/ical/abc%40group.calendar.google.com/private-x/basic.ics")
    monkeypatch.setenv("CALENDAR_2_NAME", "Bayer Band")
    monkeypatch.setenv("CALENDAR_2_URL", "https://calendar.google.com/calendar/ical/biblio%40gmail.com/private-y/basic.ics")
    assert agenda.calendario_id("bayer band") == "biblio@gmail.com"
    assert agenda.calendario_id("Talleres") == "abc@group.calendar.google.com"
    assert agenda.calendario_id("No existe") == ""


def test_el_destino_por_defecto_es_bayer_band_y_se_puede_cambiar(monkeypatch):
    monkeypatch.delenv("RESERVAS_CALENDARIO", raising=False)
    assert gcal.nombre_calendario() == "Bayer Band"
    monkeypatch.setenv("RESERVAS_CALENDARIO", "otro@group.calendar.google.com")
    assert gcal.calendario_id() == "otro@group.calendar.google.com"      # un id va directo


# ── Armado de los eventos ───────────────────────────────────────────────────
def test_los_ids_son_fijos_y_validos_para_google():
    eid = gcal.evento_id("a1b2c3d4e5f60718", 3)
    assert eid == "bpoba1b2c3d4e5f60718n003"
    assert gcal.evento_id("a1b2c3d4e5f60718", 3) == eid                 # siempre el mismo
    raro = gcal.evento_id("Con Mayúsculas!", 0)
    assert set(raro) <= set("0123456789abcdefghijklmnopqrstuv")


def test_el_evento_lleva_lo_que_se_aprobo(sala, quien):
    s = sol.crear(_pedido(sala, personas=25, necesidades=["Proyector"]), quien)
    ev = gcal.armar_evento(s, 0)
    assert ev["summary"] == "Taller de radio"
    assert ev["location"] == "Sala principal"
    assert ev["start"] == {"dateTime": "2026-03-10T18:00:00", "timeZone": gcal.TZ}
    assert ev["end"]["dateTime"] == "2026-03-10T20:00:00"
    assert "Responsable: Prensa" in ev["description"]
    assert "Proyector" in ev["description"]
    assert ev["extendedProperties"]["private"]["solicitud_id"] == s["id"]


# ── Sincronizar ─────────────────────────────────────────────────────────────
async def test_al_aprobar_se_publica_una_fecha_por_evento(google, sala, quien):
    s = sol.crear(_semanal(sala), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    await gcal.sincronizar(s["id"])

    vivos = google.vivos()
    assert [e["start"]["dateTime"][:10] for e in vivos] == \
        ["2026-03-10", "2026-03-17", "2026-03-24", "2026-03-31"]
    r = sol.obtener(s["id"])
    assert r["google_pendiente"] is False
    assert r["google_eventos"] == 4 and r["google_error"] == ""
    assert r["google_publicada"]


async def test_publicar_dos_veces_no_duplica(google, sala, quien):
    s = sol.crear(_pedido(sala), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    await gcal.sincronizar(s["id"])
    gcal.aplicar(sol.obtener(s["id"]), ses=google)       # otra pasada, igual
    assert len(google.vivos()) == 1


async def test_reprogramar_con_menos_fechas_borra_las_que_sobran(google, sala, quien):
    s = sol.crear(_semanal(sala), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    await gcal.sincronizar(s["id"])
    # Se reprograma: ahora arranca una semana después, con el mismo tope → 3 fechas.
    sol.resolver(s["id"], "aprobar", por="flor",
                 cambios={"inicio": "2026-03-17T18:00", "fin": "2026-03-17T20:00"})
    await gcal.sincronizar(s["id"])

    assert [e["start"]["dateTime"][:10] for e in google.vivos()] == \
        ["2026-03-17", "2026-03-24", "2026-03-31"]
    assert sol.obtener(s["id"])["google_eventos"] == 3


async def test_cancelar_saca_todo_de_google(google, sala, quien):
    s = sol.crear(_semanal(sala), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    await gcal.sincronizar(s["id"])
    sol.cancelar(s["id"], por="flor", motivo="Se suspende")
    await gcal.sincronizar(s["id"])

    assert google.vivos() == []
    r = sol.obtener(s["id"])
    assert r["google_eventos"] == 0 and r["google_pendiente"] is False


async def test_pedir_cambios_a_una_aprobada_la_saca_de_google(google, sala, quien):
    s = sol.crear(_pedido(sala), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    await gcal.sincronizar(s["id"])
    sol.resolver(s["id"], "observar", por="flor", motivo="Cambió el horario de la sala")
    assert sol.obtener(s["id"])["google_pendiente"] is True
    await gcal.sincronizar(s["id"])
    assert google.vivos() == []


async def test_volver_a_aprobar_revive_el_evento_borrado(google, sala, quien):
    """Google no deja crear un evento con el id de uno borrado: se actualiza y revive."""
    s = sol.crear(_pedido(sala), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    await gcal.sincronizar(s["id"])
    sol.resolver(s["id"], "observar", por="flor", motivo="Revisar")
    await gcal.sincronizar(s["id"])
    sol.resolver(s["id"], "aprobar", por="flor")
    await gcal.sincronizar(s["id"])
    assert len(google.vivos()) == 1


def test_lo_rechazado_o_pendiente_no_va_a_google(sala, quien):
    s = sol.crear(_pedido(sala), quien)
    assert s["google_pendiente"] is False
    r = sol.resolver(s["id"], "rechazar", por="flor", motivo="No hay lugar")
    assert r["google_pendiente"] is False


# ── Cuando Google falla ─────────────────────────────────────────────────────
async def test_si_google_falla_la_aprobacion_queda_y_se_anota_el_error(google, sala, quien):
    google.error_desde = 0
    s = sol.crear(_pedido(sala), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    await gcal.sincronizar(s["id"])               # no levanta

    r = sol.obtener(s["id"])
    assert r["estado"] == sol.APROBADA
    assert r["google_pendiente"] is True
    assert "500" in r["google_error"]


async def test_sin_permiso_explica_con_quien_compartir(google, sala, quien, monkeypatch):
    monkeypatch.setattr(gcal.cuenta_google, "email", lambda: "app@proyecto.iam.gserviceaccount.com")
    google.error_desde, google.status_error = 0, 403
    s = sol.crear(_pedido(sala), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    await gcal.sincronizar(s["id"])
    err = sol.obtener(s["id"])["google_error"]
    assert "app@proyecto.iam.gserviceaccount.com" in err and "compartir" in err


async def test_un_corte_a_mitad_de_camino_se_arregla_en_el_reintento(google, sala, quien):
    """Se publicaron 2 de 4 fechas y Google se cayó. Mientras, se reprograma a 1 fecha.

    El reintento tiene que dejar una sola: sabe que pudo haber quedado basura hasta la 4.
    """
    s = sol.crear(_semanal(sala), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    google.error_desde = 4                        # cada evento nuevo: PUT (no está) + POST
    await gcal.sincronizar(s["id"])
    assert len(google.vivos()) == 2 and sol.obtener(s["id"])["google_pendiente"] is True

    sol.resolver(s["id"], "aprobar", por="flor",
                 cambios={"repeticion": "unica", "hasta": None})
    google.error_desde = None
    await gcal.reintentar_pendientes()

    assert len(google.vivos()) == 1
    r = sol.obtener(s["id"])
    assert r["google_pendiente"] is False and r["google_error"] == ""


async def test_si_la_solicitud_cambia_mientras_se_publica_queda_pendiente(google, sala, quien, monkeypatch):
    s = sol.crear(_pedido(sala), quien)
    sol.resolver(s["id"], "aprobar", por="flor")

    aplicar_real = gcal.aplicar

    def aplicar_y_cambiar(x, **kw):
        n = aplicar_real(x, **kw)
        sol.resolver(s["id"], "aprobar", por="flor",       # justo la reprograman
                     cambios={"inicio": "2026-03-12T18:00", "fin": "2026-03-12T20:00"})
        return n

    monkeypatch.setattr(gcal, "aplicar", aplicar_y_cambiar)
    await gcal.sincronizar(s["id"])
    assert sol.obtener(s["id"])["google_pendiente"] is True

    monkeypatch.setattr(gcal, "aplicar", aplicar_real)
    await gcal.sincronizar(s["id"])
    assert [e["start"]["dateTime"][:10] for e in google.vivos()] == ["2026-03-12"]


async def test_sin_configurar_no_hace_nada(sala, quien):
    s = sol.crear(_pedido(sala), quien)
    sol.resolver(s["id"], "aprobar", por="flor")
    await gcal.sincronizar(s["id"])               # conftest lo deja sin configurar
    assert sol.obtener(s["id"])["google_pendiente"] is True
