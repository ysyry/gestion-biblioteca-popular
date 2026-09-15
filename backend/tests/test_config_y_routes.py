"""Tests de helpers de config y de routes (funciones puras)."""
from app.config import Settings
from app.api import routes


def test_cors_origins_list():
    s = Settings(cors_origins="http://a.com, http://b.com ,http://c.com")
    assert s.cors_origins_list == ["http://a.com", "http://b.com", "http://c.com"]


def test_norm_id():
    assert routes._norm_id("0075") == "75"     # ceros a la izquierda
    assert routes._norm_id(" 214 ") == "214"   # espacios
    assert routes._norm_id("00") == "00"       # todo ceros: no queda vacío (fallback al original)
    assert routes._norm_id(None) == ""


def test_dias_int():
    assert routes._dias_int({"dias_atraso": "5"}) == 5
    assert routes._dias_int({"dias_atraso": "-3"}) == -3
    assert routes._dias_int({"dias_atraso": "x"}) is None
    assert routes._dias_int({}) is None


def test_num():
    assert routes._num("42") == 42
    assert routes._num(None) == 0
    assert routes._num("abc") == 0


# ── Un fallo de Koha no puede disfrazarse de cero ───────────────────────────
# Regresión: cuando Koha no contestaba, los paneles devolvían [] y todos los
# números salían en 0. El tablero mostraba "0 socios, 0 ejemplares" como si fuera
# un dato real de la biblioteca. Ahora corta con 502 y lo dice.
import pytest
from fastapi import HTTPException


class _RepoRoto:
    """Repositorio que simula a Koha caído."""
    async def run_sql(self, sql):
        raise RuntimeError("Koha no responde")


class _RepoAMedias:
    """Contesta los números principales, pero falla en un gráfico suelto."""
    def __init__(self, falla_si):
        self.falla_si = falla_si

    async def run_sql(self, sql):
        if self.falla_si in sql:
            raise RuntimeError("esa consulta falló")
        return [{"total": "1380", "activos12": "336", "nunca": "79",
                 "total_items": "26334", "nuevos12": "761", "ult5": "5282",
                 "label": "2024", "count": "10",
                 "ejemplares": "26334", "titulos": "20000", "sin_circular": "5",
                 "prestamos": "100", "devoluciones": "90", "renovaciones": "10",
                 "socios_activos": "50"}]


def test_exigir_corta_cuando_falta_un_dato():
    routes._exigir([{"a": 1}], [{"b": 2}])          # todo leído: no levanta nada
    with pytest.raises(HTTPException) as e:
        routes._exigir([{"a": 1}], None)
    assert e.value.status_code == 502
    assert "Koha" in e.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("panel", ["_stats_estrategia", "_stats_catalog"])
async def test_panel_con_koha_caido_da_502_y_no_ceros(panel):
    with pytest.raises(HTTPException) as e:
        await getattr(routes, panel)(_RepoRoto())
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_panel_sigue_andando_si_falla_solo_un_grafico():
    """Un gráfico suelto que falla no tira abajo el panel: avisa y muestra el resto."""
    d = await routes._stats_estrategia(_RepoAMedias("MONTH(datetime)"))
    assert d["socios"]["total"] == 1380          # los números principales, intactos
    assert d["estacionalidad"] == []             # el gráfico que falló, vacío
    assert len(d["avisos"]) == 1                 # y queda dicho que falló
