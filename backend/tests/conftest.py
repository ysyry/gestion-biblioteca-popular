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


@pytest.fixture(autouse=True)
def sin_google_calendar(monkeypatch):
    """Ningún test escribe en el Google Calendar real de la biblioteca.

    Con el .env de desarrollo la app encuentra el calendario y la credencial, así que
    aprobar una solicitud en un test publicaría de verdad. Los tests que prueban la
    publicación lo vuelven a encender con un Google de mentira.
    """
    from app import calendario_google

    monkeypatch.setattr(calendario_google, "configurado", lambda: False)
