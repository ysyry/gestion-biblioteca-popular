"""Tests del caché TTL en memoria."""
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


async def test_invalidate_borra_por_prefijo():
    async def f():
        return 1
    await cache.cached("pre:a", 100, f)
    await cache.cached("pre:b", 100, f)
    await cache.cached("otro", 100, f)
    borradas = cache.invalidate("pre:")
    assert borradas == 2
    assert cache.age("pre:a") is None and cache.age("otro") is not None
