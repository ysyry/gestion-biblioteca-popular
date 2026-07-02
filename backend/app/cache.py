"""Caché TTL en memoria para respuestas costosas (estadísticas, cruce, agenda…).

- `cached(key, ttl, factory, swr=False)`: devuelve el valor guardado si no venció;
  si no, lo calcula con `factory` (corutina) y lo guarda. Usa un lock por clave para
  que dos pedidos simultáneos con caché vencido no dupliquen el trabajo (stampede).
- Con `swr=True` (stale-while-revalidate): si hay un valor viejo aunque haya vencido,
  lo devuelve YA y dispara el recálculo en segundo plano. Así el usuario casi nunca
  espera la recarga; solo la PRIMERA vez (sin valor previo) se calcula en el momento.
- `invalidate(prefix)`: borra las entradas cuya clave empieza con `prefix`
  (sirve para el botón "Actualizar": fuerza recálculo).

Es por proceso (en Railway hay una sola instancia). Se reinicia en cada redeploy;
para amortiguar eso, el arranque precalienta los cachés más usados (ver routes.warmup).
"""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger("cache")

_store: dict[str, tuple] = {}
_locks: dict[str, asyncio.Lock] = {}
_refreshing: set[str] = set()   # claves con un refresco en segundo plano en curso


def _refresh_bg(key: str, factory) -> None:
    """Dispara el recálculo de `key` en segundo plano (sin bloquear al que pidió).

    Si ya hay un refresco en curso para esa clave, no encola otro. Ante error,
    conserva el valor viejo (no se pisa) y lo registra.
    """
    if key in _refreshing:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _run():
        try:
            val = await factory()
            _store[key] = (val, time.time())
        except Exception as exc:  # noqa: BLE001
            logger.warning("refresco en segundo plano de %s falló (se mantiene el valor viejo): %s", key, exc)
        finally:
            _refreshing.discard(key)

    _refreshing.add(key)
    loop.create_task(_run())


async def cached(key: str, ttl: float, factory, swr: bool = False):
    now = time.time()
    e = _store.get(key)
    if e and now - e[1] < ttl:
        return e[0]
    if swr and e is not None:
        # Valor vencido pero disponible: lo devolvemos al instante y refrescamos por detrás.
        _refresh_bg(key, factory)
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
