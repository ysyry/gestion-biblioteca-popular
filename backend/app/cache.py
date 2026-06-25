"""Caché TTL en memoria para respuestas costosas (estadísticas, cruce, agenda…).

- `cached(key, ttl, factory)`: devuelve el valor guardado si no venció; si no, lo
  calcula con `factory` (corutina) y lo guarda. Usa un lock por clave para que dos
  pedidos simultáneos con caché vencido no dupliquen el trabajo (stampede).
- `invalidate(prefix)`: borra las entradas cuya clave empieza con `prefix`
  (sirve para el botón "Actualizar": fuerza recálculo).

Es por proceso (en Railway hay una sola instancia). Se reinicia en cada redeploy,
lo cual es aceptable: a lo sumo la primera carga vuelve a ser lenta.
"""
from __future__ import annotations

import asyncio
import time

_store: dict[str, tuple] = {}
_locks: dict[str, asyncio.Lock] = {}


async def cached(key: str, ttl: float, factory):
    now = time.time()
    e = _store.get(key)
    if e and now - e[1] < ttl:
        return e[0]
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        e = _store.get(key)                       # re-chequea tras tomar el lock
        if e and time.time() - e[1] < ttl:
            return e[0]
        val = await factory()
        _store[key] = (val, time.time())
        return val


def invalidate(prefix: str = "") -> int:
    n = 0
    for k in list(_store):
        if k.startswith(prefix):
            _store.pop(k, None)
            n += 1
    return n


def age(key: str) -> float | None:
    """Segundos desde que se cacheó esa clave (o None si no está)."""
    e = _store.get(key)
    return (time.time() - e[1]) if e else None
