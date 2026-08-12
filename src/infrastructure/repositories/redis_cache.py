import json
import redis.asyncio as aioredis
from typing import Any, Optional
from application.interfaces.cache_repository import CacheRepository
from src.config import settings

class RedisCacheRepository(CacheRepository):
    """
    Redis-based cache repository.
    If REDIS_URL is not set, it falls back to an in‑memory dictionary.
    """
    def __init__(self):
        self.redis_url = settings.REDIS_URL
        if self.redis_url:
            self.redis = aioredis.from_url(self.redis_url, decode_responses=True)
            self._in_memory = None
        else:
            self.redis = None
            self._in_memory = {}

    async def get(self, key: str) -> Optional[Any]:
        """Retrieve a value from cache by key."""
        if self.redis:
            data = await self.redis.get(key)
            if data:
                return json.loads(data)
            return None
        else:
            return self._in_memory.get(key)

    async def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        """Store a value in cache with a TTL (in seconds)."""
        if self.redis:
            await self.redis.setex(key, ttl, json.dumps(value))
        else:
            self._in_memory[key] = value

    async def delete(self, key: str) -> None:
        """Remove a key from cache."""
        if self.redis:
            await self.redis.delete(key)
        else:
            self._in_memory.pop(key, None)

    async def clear(self) -> None:
        """Clear all cache entries (only for in‑memory fallback)."""
        if self.redis:
            # Redis does not have a clear-all method by default;
            # you could use FLUSHDB, but it's dangerous. We'll skip it.
            pass
        else:
            self._in_memory.clear()