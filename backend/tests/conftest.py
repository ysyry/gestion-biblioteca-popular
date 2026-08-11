import sys
from pathlib import Path

import pytest

# Permite importar el paquete `app` al correr pytest desde backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def store(monkeypatch):
    """Storage en memoria (no toca archivo ni Postgres). Devuelve el dict de fondo."""
    from app import storage

    mem: dict = {}
    monkeypatch.setattr(storage, "get", lambda k: mem.get(k))
    monkeypatch.setattr(storage, "set", lambda k, v: mem.__setitem__(k, v))
    monkeypatch.setattr(storage, "delete", lambda k: mem.pop(k, None))
    return mem
