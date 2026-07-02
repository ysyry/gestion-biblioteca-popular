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
