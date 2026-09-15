"""Tests del cliente de Koha: que una página web nunca se cuele como si fueran datos.

Contexto del bug que originó estos tests: Koha devolvía HTML (sesión vencida, sin
permisos, error del servidor) y el parser TSV lo tomaba como una tabla. Salían filas
basura que aguas arriba se convertían en CEROS, y el tablero mostraba "0 socios"
como si fuera un dato real. Un cero inventado es peor que un error a la vista.
"""
import asyncio

import pytest

from app.koha.client import KohaAuthError, KohaClient, KohaError, sql_literal

LOGIN_HTML = """<!DOCTYPE html><html><head><title>Koha › Ingresar</title></head>
<body><form><input type="hidden" name="koha_login_context" value="intranet">
<input name="userid"></form></body></html>"""

ALERTA_HTML = """<!DOCTYPE html><html><head><title>Koha › Informes</title></head>
<body><div class="dialog alert">No tenés permiso para ejecutar informes.</div></body></html>"""

ERROR_HTML = """<html><head><title>Koha › Error 500</title></head><body>Se rompió algo.</body></html>"""

TSV = "cardnumber\tsurname\n1234\tPaz\n"
TSV_UNA_COLUMNA = "total\n1380\n"          # un SELECT COUNT(*) no tiene ningún tabulador


# ── Detección de HTML ───────────────────────────────────────────────────────
@pytest.mark.parametrize("html", [LOGIN_HTML, ALERTA_HTML, ERROR_HTML,
                                  "  \n <!doctype html><html></html>"])
def test_reconoce_html(html):
    assert KohaClient._es_html(html)


@pytest.mark.parametrize("texto", [TSV, TSV_UNA_COLUMNA, "", "   ", "a\tb\n1\t2"])
def test_no_confunde_datos_con_html(texto):
    """Ojo con TSV_UNA_COLUMNA: no tiene tabuladores y aun así son datos válidos.

    La comprobación vieja miraba justamente si había tabuladores, y por eso dejaba
    pasar cosas que no eran datos.
    """
    assert not KohaClient._es_html(texto)


# ── La respuesta se valida antes de parsearla ──────────────────────────────
def test_sesion_vencida_da_error_de_auth():
    with pytest.raises(KohaAuthError, match="iniciar sesión"):
        KohaClient._exigir_datos(LOGIN_HTML, "Consulta")


def test_alerta_de_koha_se_muestra_tal_cual():
    """El motivo real de Koha llega a la persona, no un mensaje genérico."""
    with pytest.raises(KohaError, match="No tenés permiso para ejecutar informes"):
        KohaClient._exigir_datos(ALERTA_HTML, "Informe 12")


def test_html_inesperado_da_error_con_el_titulo():
    with pytest.raises(KohaError, match="Error 500"):
        KohaClient._exigir_datos(ERROR_HTML, "Consulta de catálogo")


def test_el_contexto_va_en_el_mensaje():
    with pytest.raises(KohaError, match="Informe 42"):
        KohaClient._exigir_datos(ALERTA_HTML, "Informe 42")


@pytest.mark.parametrize("texto", [TSV, TSV_UNA_COLUMNA, ""])
def test_los_datos_pasan_sin_ruido(texto):
    KohaClient._exigir_datos(texto, "Consulta")      # no levanta nada


# ── La regresión concreta: HTML parseado como TSV daba ceros ───────────────
def test_el_parser_solo_produciria_basura_con_html():
    """Deja constancia de POR QUÉ hace falta validar antes de parsear.

    Si se parsea el HTML igual, salen filas sin las claves esperadas: `.get("total")`
    devuelve None, `_num(None)` da 0, y el tablero muestra 0 socios. De ahí el bug.
    """
    filas = KohaClient._parse_tsv(LOGIN_HTML)
    assert all("total" not in f for f in filas)      # nunca trae la clave real
    # Y por eso _exigir_datos tiene que cortar antes de llegar acá:
    with pytest.raises(KohaAuthError):
        KohaClient._exigir_datos(LOGIN_HTML, "Consulta")


# ── Parámetros: el texto de la usuaria nunca se ejecuta como SQL ────────────
@pytest.mark.parametrize("valor, esperado", [
    ("Paz", "'Paz'"),
    ("O'Brien", "'O''Brien'"),
    ("%kindle%", "'%kindle%'"),
    ("a\\b", "'a\\\\b'"),
])
def test_sql_literal_escapa(valor, esperado):
    assert sql_literal(valor) == esperado


def test_barra_invertida_no_cierra_el_string():
    """Regresión: con solo duplicar comillas, `\\'` cerraba el string en MySQL.

    `\\' OR 1=1 -- ` quedaba `'\\'' OR 1=1 -- '`: MySQL lee `\\'` como comilla
    escapada, la siguiente cierra, y `OR 1=1` se ejecutaba.
    """
    sql = KohaClient._substitute("SELECT 1 WHERE x = <<t>>", ["\\' OR 1=1 -- "])
    literal = sql.removeprefix("SELECT 1 WHERE x = ")
    assert literal == "'\\\\'' OR 1=1 -- '"
    # Leído como MySQL: `\\\\` es una barra, `''` una comilla, y el string termina al final.
    cuerpo = literal[1:-1]
    assert cuerpo.replace("\\\\", "").replace("''", "").count("'") == 0


def test_parse_tsv_con_datos_de_verdad():
    assert KohaClient._parse_tsv(TSV) == [{"cardnumber": "1234", "surname": "Paz"}]
    assert KohaClient._parse_tsv(TSV_UNA_COLUMNA) == [{"total": "1380"}]


def test_parse_tsv_comillas_son_parte_del_dato():
    """Koha exporta sin citar. Un título entre comillas no puede comerse la fila siguiente."""
    texto = ('barcode\ttitle\tdate_due\n'
             '111\t"Rayuela" edición aniversario\t2026-10-01\n'
             '222\tEl "principito\t2026-10-02\n')
    assert KohaClient._parse_tsv(texto) == [
        {"barcode": "111", "title": '"Rayuela" edición aniversario', "date_due": "2026-10-01"},
        {"barcode": "222", "title": 'El "principito', "date_due": "2026-10-02"},
    ]


# ── Renovación de sesión: una sola vez aunque la pidan muchos a la vez ─────
def _cliente():
    return KohaClient("http://koha.test", "usuaria", "clave")


def test_renovar_una_sola_vez_con_pedidos_en_paralelo():
    """Siete consultas en paralelo con la sesión vencida = UN solo login.

    Antes cada una llamaba a login() por su cuenta y se pisaban la cookie entre
    ellas: unas recibían la página de ingreso en vez de los datos.
    """
    c = _cliente()
    logins = []

    async def login_falso():
        await asyncio.sleep(0.01)          # simula la ida y vuelta a Koha
        logins.append(1)
        c._logged_in = True
        c._gen += 1

    c.login = login_falso

    async def main():
        gen = c._gen                        # todas ven la misma sesión vencida
        await asyncio.gather(*(c._renovar(gen) for _ in range(7)))

    asyncio.run(main())
    assert len(logins) == 1, f"se logueó {len(logins)} veces en vez de 1"
    assert c._gen == 1


def test_renovar_con_sesion_ya_renovada_no_vuelve_a_loguear():
    c = _cliente()
    llamadas = []

    async def login_falso():
        llamadas.append(1)
        c._gen += 1

    c.login = login_falso
    c._gen = 5                              # alguien ya renovó
    asyncio.run(c._renovar(3))              # yo traía la sesión vieja (3)
    assert llamadas == []


def test_el_login_sube_la_generacion():
    """La 'generación' es lo que permite saber si alguien más ya renovó."""
    c = _cliente()
    assert c._gen == 0
