import asyncio

import pytest

from econavigate.cache import PersistentTTLCache


@pytest.mark.asyncio
async def test_cache_persists_and_coalesces_loads(tmp_path):
    cache_path = tmp_path / "cache"
    first_cache = PersistentTTLCache(cache_path, 10_000_000)
    calls = 0

    async def load():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"value": 42}

    results = await asyncio.gather(
        first_cache.get_or_load("answer", 60, load),
        first_cache.get_or_load("answer", 60, load),
    )
    assert results == [{"value": 42}, {"value": 42}]
    assert calls == 1
    await first_cache.close()

    second_cache = PersistentTTLCache(cache_path, 10_000_000)
    assert await second_cache.get_or_load("answer", 60, load) == {"value": 42}
    assert calls == 1
    await second_cache.close()
