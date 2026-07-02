"""Tests de la agenda: config de calendarios y parseo de iCal (recurrencia + todo el día)."""
import datetime as dt

from app import agenda

# variables de calendario que podrían venir del .env local: las limpiamos por test
_CAL_ENV = [f"CALENDAR_{n}_URL" for n in range(1, 6)] + \
           [f"CALENDAR_{n}_NAME" for n in range(1, 6)] + ["CALENDAR_ICS_URL", "CALENDAR_ICS_URLS"]


def _limpiar(monkeypatch):
    for k in _CAL_ENV:
        monkeypatch.delenv(k, raising=False)


def test_parse_sources_numerado(monkeypatch):
    _limpiar(monkeypatch)
    monkeypatch.setenv("CALENDAR_1_NAME", "Talleres")
    monkeypatch.setenv("CALENDAR_1_URL", "https://a.ics")
    monkeypatch.setenv("CALENDAR_2_URL", "https://b.ics")   # sin nombre → default
    s = agenda._parse_sources()
    assert len(s) == 2
    assert s[0]["nombre"] == "Talleres" and s[1]["nombre"] == "Calendario 2"
    assert s[0]["color"] != s[1]["color"]                   # colores distintos


def test_parse_sources_una_url(monkeypatch):
    _limpiar(monkeypatch)
    monkeypatch.setenv("CALENDAR_ICS_URL", "https://uno.ics")
    s = agenda._parse_sources()
    assert len(s) == 1 and s[0]["url"] == "https://uno.ics"


def test_no_configurado(monkeypatch):
    _limpiar(monkeypatch)
    assert agenda.configured() is False


ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//ES
BEGIN:VEVENT
UID:1
SUMMARY:Cineclub
LOCATION:Sala
DTSTART:20260626T220000Z
DTEND:20260627T000000Z
RRULE:FREQ=WEEKLY;COUNT=4
END:VEVENT
BEGIN:VEVENT
UID:2
SUMMARY:Feria del Libro
DTSTART;VALUE=DATE:20260705
DTEND;VALUE=DATE:20260706
END:VEVENT
END:VCALENDAR"""


def test_parse_ics_expande_recurrencia_y_todo_el_dia():
    evs = agenda.parse_ics(ICS, dt.date(2026, 6, 20), dt.date(2026, 8, 1),
                           {"nombre": "Cal", "color": "#111"})
    cine = [e for e in evs if e["titulo"] == "Cineclub"]
    feria = [e for e in evs if e["titulo"] == "Feria del Libro"]
    assert len(cine) >= 3                       # recurrencia expandida
    assert cine[0]["todo_el_dia"] is False and cine[0]["calendario"] == "Cal"
    assert feria and feria[0]["todo_el_dia"] is True
