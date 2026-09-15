"""Tests de las notas de socios: clasificación, lectura del HEX de Koha y resueltas."""
from datetime import date

import pytest

from app import notas


def _hex(texto: str) -> str:
    return texto.encode("utf-8").hex().upper()


# ── Clasificación ───────────────────────────────────────────────────────────
# Ejemplos sacados del relevamiento real (sin datos de socios).
@pytest.mark.parametrize("texto", [
    "Cuotas hasta JUNIO/2025",
    "cuotas hasta DICIEMBRE 2026",
    "Cuaotas hasta SEPTIEMBRE 2026",          # errores de tipeo reales
    "Cuatas Septiembre 2026",
    "Ctas pagas h/ Dic'17",
    "ctaspagas h/ Mayo '19",
    "Cta Oct'20",
    "Inscripción y cuota FEBRERO",
    "Reinscripción y cuotas DICIEMBRE 25",
    "DEBITO AUTOMATICO",
    "ADHESION AL DEBITO AUTOMATICO",
    "Adheesión al DEBITO AUTOMATICO",
    "Pagos hasta FEBRERO 2024",
    "Pago Mayo'18",
    "octubre 2023",
])
def test_registros_de_pago_son_cuotas(texto):
    assert notas.clasificar(texto) == notas.CUOTAS


@pytest.mark.parametrize("texto", [
    "Reclamé libros x wp",
    "reclame libro por wp",
    "Volvi a reclamar x mail",
    "Solicitamos libro por wsp",
    "Pedido de libro por wp",
    "Mensaje por libros por wp",
    "Recordatorio de cuotas",
])
def test_avisos_de_rutina_son_reclamos(texto):
    assert notas.clasificar(texto) == notas.RECLAMO


@pytest.mark.parametrize("texto", [
    "Dice que lo devuelve en noviembre",
    "El libro 2 de percy jackson está duplicado. Cambiar el número",
    "Plantea que no puede pagar la cuota, por un tiempo",
    "Socia Becada",
    "DEBE el libro 24263 desde 10/23, dado x PERDIDO",
    # Empiezan como rutina pero agregan información: no se pueden esconder.
    "Cuotas hasta DICIEMBRE/2022 / AÑO 2021 LO PAGARÁ EN CUOTAS",
    "Cuota hasta marzo 2026. Y dejo $7000 a cuenta de abril 2026",
    "Recordé cuotas. Las paga cuando vuelve del viaje",
    "Reclamé libros x wp. Dice que los trae el lunes",
    "KINDLE n° 4",
])
def test_lo_que_agrega_informacion_es_novedad(texto):
    assert notas.clasificar(texto) == notas.NOVEDAD


def test_clasificar_texto_vacio_no_rompe():
    assert notas.clasificar("") == notas.NOVEDAD


# ── Lectura del HEX ─────────────────────────────────────────────────────────
def test_decodificar_respeta_tildes_y_saltos():
    crudo = "Reclamé libros\r\nDADOS POR PERDIDOS:\r\n\tEpaminondas 24411   \r\n"
    assert notas.decodificar(_hex(crudo)) == "Reclamé libros\nDADOS POR PERDIDOS:\n\tEpaminondas 24411"


@pytest.mark.parametrize("malo", [None, "", "ZZ", "123"])
def test_decodificar_hex_invalido_da_vacio(malo):
    assert notas.decodificar(malo) == ""


# ── SQL ─────────────────────────────────────────────────────────────────────
def test_sql_de_socio_escapa_el_carnet():
    sql = notas.sql_de_socio("12' OR '1'='1")
    assert "b.cardnumber = '12'' OR ''1''=''1'" in sql
    assert "HEX(m.message)" in sql


def test_sql_recientes_con_fecha_busqueda_y_limite():
    sql = notas.sql_recientes(date(2026, 8, 1), "kindle\\", limite=50)
    assert "m.message_date >= '2026-08-01 00:00:00'" in sql
    assert "m.message LIKE '%kindle\\\\%'" in sql
    assert sql.rstrip().endswith("LIMIT 50")


def test_sql_recientes_sin_filtros_no_tiene_where():
    assert "WHERE" not in notas.sql_recientes(None)


# ── Armado y resueltas ──────────────────────────────────────────────────────
FILAS = [
    {"id": "30", "fecha": "2026-09-14 10:00:00", "tipo_koha": "L", "sede": "3169",
     "texto_hex": _hex("Dice que lo devuelve en noviembre"), "cardnumber": "0042",
     "surname": "Paz", "firstname": "Ana"},
    {"id": "29", "fecha": "2026-09-10 10:00:00", "tipo_koha": "L", "sede": "3169",
     "texto_hex": _hex("Cuotas hasta SEPTIEMBRE 2026"), "cardnumber": "0042",
     "surname": "Paz", "firstname": "Ana"},
    {"id": "28", "fecha": "2026-05-01 10:00:00", "tipo_koha": "B", "sede": "3169",
     "texto_hex": _hex("Libro roto, lo reponen"), "cardnumber": "0077",
     "surname": "Sur", "firstname": "Leo"},
    {"id": "27", "fecha": "2026-09-01", "tipo_koha": "L", "sede": "", "texto_hex": "",
     "cardnumber": "0077", "surname": "Sur", "firstname": "Leo"},       # nota vacía
]


def test_armar_decodifica_clasifica_y_descarta_vacias(store):
    items = notas.armar(FILAS)
    assert [n["id"] for n in items] == ["30", "29", "28"]
    assert [n["tipo"] for n in items] == ["novedad", "cuotas", "novedad"]
    assert items[0]["texto"] == "Dice que lo devuelve en noviembre"
    assert items[0]["cardnumber"] == "0042"
    assert items[2]["para_socio"] is True
    assert not any(n["resuelta"] for n in items)


def test_marcar_resuelta_y_reabrir(store):
    marca = notas.marcar("30", True, "Flor")
    assert marca["por"] == "Flor" and marca["cuando"]
    assert notas.armar(FILAS)[0]["resuelta"] is True
    assert notas.marcar("30", False, "Flor") == {}
    assert notas.armar(FILAS)[0]["resuelta"] is False


@pytest.mark.parametrize("malo", ["", "abc", "1 OR 1"])
def test_marcar_id_invalido(store, malo):
    with pytest.raises(ValueError):
        notas.marcar(malo, True, "Flor")


def test_avisos_solo_novedades_pendientes_y_recientes(store):
    hoy = date(2026, 9, 15)
    items = notas.armar(FILAS)
    avisos = notas.avisos_por_socio(items, dias=60, hoy=hoy)
    # 0042: la novedad reciente (la de cuotas no cuenta). 0077: su novedad es de mayo.
    assert list(avisos) == ["0042"]
    assert avisos["0042"]["cantidad"] == 1
    assert avisos["0042"]["notas"][0]["texto"] == "Dice que lo devuelve en noviembre"

    notas.marcar("30", True, "Flor")
    assert notas.avisos_por_socio(notas.armar(FILAS), dias=60, hoy=hoy) == {}
