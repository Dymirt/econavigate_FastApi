import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from diskcache import Cache

T = TypeVar("T")
_MISSING = object()


class PersistentTTLCache:
    """Disk-backed TTL cache with per-process request coalescing."""

    def __init__(self, directory: Path, size_limit: int) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._cache = Cache(str(directory), size_limit=size_limit)
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_or_load(
        self,
        key: str,
        ttl_seconds: int,
        loader: Callable[[], Awaitable[T]],
    ) -> T:
        cached = await self._get(key)
        if cached is not _MISSING:
            return cached

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = await self._get(key)
            if cached is not _MISSING:
                return cached

            value = await loader()
            await asyncio.to_thread(
                self._cache.set,
                key,
                value,
                expire=ttl_seconds,
                retry=True,
            )
            return value

    async def _get(self, key: str) -> Any:
        return await asyncio.to_thread(
            self._cache.get,
            key,
            _MISSING,
            retry=True,
        )

    async def info(self) -> dict[str, int | str]:
        entries, volume = await asyncio.gather(
            asyncio.to_thread(len, self._cache),
            asyncio.to_thread(self._cache.volume),
        )
        return {
            "backend": "diskcache",
            "entries": entries,
            "volumeBytes": volume,
        }

    async def close(self) -> None:
        await asyncio.to_thread(self._cache.close)
