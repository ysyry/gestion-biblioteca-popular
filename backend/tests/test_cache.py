"""Tests del caché TTL en memoria."""
import asyncio

from app import cache


async def test_cached_reusa_dentro_del_ttl():
    cache.invalidate("t_reuse")
    n = {"c": 0}
    async def factory():
        n["c"] += 1
        return "v"
    a = await cache.cached("t_reuse", 100, factory)
    b = await cache.cached("t_reuse", 100, factory)
    assert a == b == "v"
    assert n["c"] == 1                     # solo se calculó una vez


async def test_cached_recalcula_tras_vencer():
    cache.invalidate("t_ttl")
    n = {"c": 0}
    async def factory():
        n["c"] += 1
        return n["c"]
    await cache.cached("t_ttl", 0, factory)   # ttl 0 → siempre vencido
    await cache.cached("t_ttl", 0, factory)
    assert n["c"] == 2


async def test_swr_devuelve_viejo_y_refresca_en_segundo_plano():
    cache.invalidate("t_swr")
    n = {"c": 0}
    async def factory():
        n["c"] += 1
        return n["c"]
    v1 = await cache.cached("t_swr", 100, factory)        # primera vez: calcula (1)
    assert v1 == 1
    # ttl 0 → vencido, pero con swr devuelve el viejo al instante y refresca por detrás
    v2 = await cache.cached("t_swr", 0, factory, swr=True)
    assert v2 == 1                                        # sirvió el valor viejo, sin esperar
    await asyncio.sleep(0)                                # deja correr la tarea de refresco
    await asyncio.sleep(0)
    assert n["c"] == 2                                    # se recalculó en segundo plano
    assert cache._store["t_swr"][0] == 2                 # el caché ya tiene el valor nuevo


async def test_swr_sin_valor_previo_calcula_sincrono():
    cache.invalidate("t_swr_cold")
    n = {"c": 0}
    async def factory():
        n["c"] += 1
        return "x"
    # Sin valor previo, swr no aplica: se calcula en el momento (no devuelve None).
    v = await cache.cached("t_swr_cold", 0, factory, swr=True)
    assert v == "x" and n["c"] == 1


async def test_invalidate_borra_por_prefijo():
    async def f():
        return 1
    await cache.cached("pre:a", 100, f)
    await cache.cached("pre:b", 100, f)
    await cache.cached("otro", 100, f)
    borradas = cache.invalidate("pre:")
    assert borradas == 2
    assert cache.age("pre:a") is None and cache.age("otro") is not None
