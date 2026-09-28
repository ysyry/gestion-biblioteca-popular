"""Los datos de mentira con los que corre la app en las pruebas de punta a punta.

Cuatro socios, pensados para que cada etiqueta de los mails tenga un valor distinto
y reconocible (así un {{meses_debe}} que sale 0 o un nombre cruzado salta enseguida):

  · 100 Ana Pérez    — con mail · 1 libro vencido · debe los últimos 3 meses de cuota
  · 200 Bruno Gómez  — con mail · 1 libro en préstamo · cuota al día
  · 300 Carla López  — sin mail · 1 libro vencido · nunca pagó
  · 400 Diego Díaz   — dado de baja en Koha (categoría B) · no recibe nada

Las fechas se arman a partir de hoy, así los vencidos siguen vencidos siempre.
"""
from __future__ import annotations

import datetime as dt

HOY = dt.date.today()


def _dia(delta: int) -> str:
    return (HOY + dt.timedelta(days=delta)).isoformat()


SOCIOS = [
    {"cardnumber": "100", "surname": "Pérez", "firstname": "Ana", "email": "ana@example.org",
     "phone": "11-1111-1111", "categorycode": "A", "category": "Activo", "l12": 5},
    {"cardnumber": "200", "surname": "Gómez", "firstname": "Bruno", "email": "bruno@example.org",
     "phone": "11-2222-2222", "categorycode": "A", "category": "Activo", "l12": 3},
    {"cardnumber": "300", "surname": "López", "firstname": "Carla", "email": "",
     "phone": "11-3333-3333", "categorycode": "A", "category": "Activo", "l12": 1},
    {"cardnumber": "400", "surname": "Díaz", "firstname": "Diego", "email": "diego@example.org",
     "phone": "11-4444-4444", "categorycode": "B", "category": "Baja", "l12": 0},
]

# Préstamos vigentes: (carnet, código de barras, título, días hasta el vencimiento).
# Negativo = vencido hace esos días.
PRESTAMOS = [
    ("100", "B-001", "Rayuela", -10),
    ("200", "B-002", "Ficciones", 3),
    ("300", "B-003", "Operación masacre", -4),
]

TITULO_VENCIDO_ANA = "Rayuela"
TITULO_ACTIVO_BRUNO = "Ficciones"


def socio(carnet: str) -> dict:
    return next(s for s in SOCIOS if s["cardnumber"] == carnet)


def _prestamo(carnet, barcode, titulo, delta) -> dict:
    s = socio(carnet)
    return {"cardnumber": carnet, "surname": s["surname"], "firstname": s["firstname"],
            "email": s["email"], "phone": s["phone"], "barcode": barcode, "title": titulo,
            "author": "", "issuedate": _dia(delta - 15), "date_due": _dia(delta),
            "dias_atraso": -delta}


def prestamos() -> list[dict]:
    return [_prestamo(*p) for p in PRESTAMOS]


# ── Reportes guardados de Koha (por id, ver REPORT_*_ID en servidor.py) ─────────
def reporte(report_id: int, params: list[str] | None) -> list[dict]:
    p = (params or [""])[0]
    if report_id == 1:                                   # member_search: %término%
        q = p.strip("%").lower()
        return [{k: s[k] for k in ("cardnumber", "surname", "firstname", "email", "phone",
                                   "category")} | {"dateexpiry": _dia(365)}
                for s in SOCIOS
                if q in f"{s['cardnumber']} {s['surname']} {s['firstname']}".lower()]
    if report_id == 2:                                   # member_loans
        return [x for x in prestamos() if x["cardnumber"] == p]
    if report_id == 3:                                   # loans_active
        return prestamos()
    if report_id == 4:                                   # loans_overdue
        return [x for x in prestamos() if x["dias_atraso"] > 0]
    if report_id == 5:                                   # member_profile
        s = next((s for s in SOCIOS if s["cardnumber"] == p), None)
        if not s:
            return []
        return [{"cardnumber": s["cardnumber"], "surname": s["surname"],
                 "firstname": s["firstname"], "email": s["email"], "phone": s["phone"],
                 "mobile": "", "address": "Calle Falsa 123", "city": "Buenos Aires",
                 "category": s["category"], "dateenrolled": "2020-03-01",
                 "dateexpiry": _dia(365), "debarred": None, "deuda": "0"}]
    if report_id == 6:                                   # member_account (sin uso)
        return []
    if report_id == 7:                                   # member_history
        return [{"barcode": "B-900", "title": "El Aleph", "author": "Borges",
                 "issuedate": "2026-01-10", "returndate": "2026-01-24"}] if p == "100" else []
    if report_id == 8:                                   # loans_contact
        return prestamos()
    return []


# ── Notas de socios (mensajes internos de Koha) ─────────────────────────────────
NOTA_ANA = "Dejó el carnet en la biblioteca, pasa a buscarlo el viernes"


def _nota(i: int, carnet: str, texto: str, delta: int) -> dict:
    s = socio(carnet)
    return {"id": str(i), "fecha": f"{_dia(delta)} 10:00:00", "tipo_koha": "L", "sede": "BAYER",
            "texto_hex": texto.encode("utf-8").hex().upper(), "cardnumber": carnet,
            "surname": s["surname"], "firstname": s["firstname"]}


NOTAS = [_nota(1, "100", NOTA_ANA, -1)]


def sql(consulta: str) -> list[dict]:
    """Respuesta a las consultas SQL sueltas que arma la app."""
    c = " ".join(consulta.split()).lower()
    if "from messages" in c:
        if "b.cardnumber = '" in c:
            carnet = c.split("b.cardnumber = '")[1].split("'")[0]
            return [n for n in NOTAS if n["cardnumber"] == carnet]
        return list(NOTAS)
    if "from borrowers" in c:
        return [dict(s) for s in SOCIOS]
    return []   # estadísticas de catálogo: sin datos (los tableros muestran "sin datos")


# ── Planilla de cuotas (Google Sheets) ──────────────────────────────────────────
# La planilla trae 2024-2026 (ver cuotas.col_de). Ana pagó hasta 3 meses antes del
# actual, así siempre debe algo.
PAGOS_ANA = max(HOY.month - 3, 0)

# Bloques de 12 meses por año desde la col 11 (2024), 23 (2025) y 35 (2026). 'P' = pagó.
def filas_planilla() -> list[list[str]]:
    def fila(carnet, apellido, nombre, pagos_por_anio: dict[int, int]):
        r = [""] * 53
        r[1], r[2], r[3], r[4] = carnet, apellido, nombre, "Activo"
        for anio, hasta in pagos_por_anio.items():
            col0 = {2024: 11, 2025: 23, 2026: 35}[anio]
            for m in range(hasta):
                r[col0 + m] = "P"
        return r

    return [
        [""] * 53,
        ["", "# Matrícula"] + [""] * 51,
        fila("100", "Pérez", "Ana", {2025: 12, 2026: PAGOS_ANA}),  # debe los últimos 3
        fila("200", "Gómez", "Bruno", {2025: 12, 2026: HOY.month}),  # al día
        fila("300", "López", "Carla", {}),                          # nunca pagó
        fila("400", "Díaz", "Diego", {2025: 3}),
    ]


MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
MESES_LARGO = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
               "septiembre", "octubre", "noviembre", "diciembre"]


def deuda_ana() -> tuple[str, str]:
    """(meses_debe, meses_impagos) esperados para Ana: los meses vencidos que no pagó."""
    impagos = MESES[PAGOS_ANA:HOY.month]
    return str(len(impagos)), (", ".join(impagos) or "—")


ULTIMO_PAGO_ANA = (f"{MESES_LARGO[PAGOS_ANA - 1]} 2026" if PAGOS_ANA else "diciembre 2025")
