"""Tests del almacenamiento clave-valor (fallback a archivo, sin Postgres)."""
from app import storage


def test_roundtrip_en_archivo(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATABASE_URL", None)
    monkeypatch.setattr(storage, "_DATA_DIR", tmp_path)
    assert storage.using_db() is False
    storage.set("cfg", {"a": 1, "lista": [1, 2, 3]})
    assert storage.get("cfg") == {"a": 1, "lista": [1, 2, 3]}


def test_get_inexistente_devuelve_none(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATABASE_URL", None)
    monkeypatch.setattr(storage, "_DATA_DIR", tmp_path)
    assert storage.get("no_existe") is None
