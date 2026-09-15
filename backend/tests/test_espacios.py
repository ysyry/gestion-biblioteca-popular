"""Tests de los espacios de la biblioteca."""
import pytest

from app import espacios


def test_la_primera_vez_siembra_ejemplos(store):
    """El módulo tiene que ser usable desde el minuto cero, pero sin mentir."""
    lista = espacios.listar()
    assert lista, "tendría que haber sembrado algo"
    assert all(e["de_ejemplo"] for e in lista)      # marcados como lo que son
    assert store["espacios"], "la siembra tiene que quedar guardada"


def test_la_semilla_sale_de_la_variable_de_entorno(store, monkeypatch):
    monkeypatch.setenv("ESPACIOS", "Sala grande, Depósito")
    lista = espacios.listar()
    assert [e["nombre"] for e in lista] == ["Depósito", "Sala grande"]   # ordenados
    assert not any(e["de_ejemplo"] for e in lista)   # si los cargó la biblioteca, son reales


def test_no_vuelve_a_sembrar(store):
    espacios.listar()
    espacios.borrar(espacios.listar()[0]["id"])
    cuantos = len(espacios.listar())
    assert len(espacios.listar()) == cuantos        # no repuebla lo borrado


def test_crear_y_editar(store):
    store["espacios"] = []
    e = espacios.crear("Patio", capacidad=60, notas="Solo si no llueve")
    assert e["capacidad"] == 60 and e["de_ejemplo"] is False
    r = espacios.actualizar(e["id"], {"capacidad": 80})
    assert r["capacidad"] == 80


def test_editar_un_ejemplo_lo_vuelve_real(store):
    """Cuando la biblioteca lo corrige, deja de ser un nombre de ejemplo."""
    e = espacios.listar()[0]
    assert e["de_ejemplo"]
    assert espacios.actualizar(e["id"], {"nombre": "Sala Osvaldo Bayer"})["de_ejemplo"] is False


def test_no_se_repiten_los_nombres(store):
    store["espacios"] = []
    espacios.crear("Patio")
    with pytest.raises(espacios.ErrorEspacio, match="Ya existe"):
        espacios.crear("  patio  ")


def test_editar_a_un_nombre_ya_usado(store):
    store["espacios"] = []
    espacios.crear("Patio")
    otro = espacios.crear("Sala")
    with pytest.raises(espacios.ErrorEspacio, match="Ya existe"):
        espacios.actualizar(otro["id"], {"nombre": "Patio"})


def test_puedo_editar_un_espacio_sin_cambiarle_el_nombre(store):
    store["espacios"] = []
    e = espacios.crear("Patio")
    assert espacios.actualizar(e["id"], {"nombre": "Patio", "notas": "ok"})["notas"] == "ok"


def test_sin_nombre_no_va(store):
    store["espacios"] = []
    with pytest.raises(espacios.ErrorEspacio, match="Falta el nombre"):
        espacios.crear("   ")


def test_desactivado_no_aparece_pero_sigue_estando(store):
    store["espacios"] = []
    e = espacios.crear("Patio")
    espacios.actualizar(e["id"], {"activo": False})
    assert espacios.listar() == []
    assert len(espacios.listar(incluir_inactivos=True)) == 1


def test_exigir(store):
    store["espacios"] = []
    e = espacios.crear("Patio")
    assert espacios.exigir(e["id"])["nombre"] == "Patio"
    with pytest.raises(espacios.ErrorEspacio, match="no existe"):
        espacios.exigir("fantasma")
    espacios.actualizar(e["id"], {"activo": False})
    with pytest.raises(espacios.ErrorEspacio, match="de baja"):
        espacios.exigir(e["id"])


def test_nombre_de_un_espacio_borrado_no_rompe(store):
    store["espacios"] = []
    assert "borrado" in espacios.nombre_de("fantasma")


def test_borrar(store):
    store["espacios"] = []
    e = espacios.crear("Patio")
    espacios.borrar(e["id"])
    assert espacios.listar() == []
    with pytest.raises(espacios.ErrorEspacio, match="no existe"):
        espacios.borrar(e["id"])
