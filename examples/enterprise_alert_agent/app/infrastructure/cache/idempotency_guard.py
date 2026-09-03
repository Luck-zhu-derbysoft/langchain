"""基于 Redis SETNX 的幂等守卫：防止重复请求重复摄入/重复消费。"""

from typing import Any

from redis import asyncio as redis_asyncio

_PROCESSING_MARK = "PROCESSING"


class IdempotencyGuard:
    """幂等守卫。"""
    def __init__(self, redis_client: redis_asyncio.Redis|None, ttl=600):
        self._redis_client = redis_client
        self.ttl = ttl

    async def acquire(self, key, ttl=None) -> tuple[bool, Any]:
        """尝试获取幂等锁。"""
        if ttl is None:
            ttl = self.ttl
        if self._redis_client is None:
            return False, "Redis client is not initialized"

        success = await self._redis_client.set(key, _PROCESSING_MARK, nx=True, ex=ttl)
        if success:
            return True, None
        cached_value = await self._redis_client.get(key)
        if cached_value == _PROCESSING_MARK:
            return False, "Processing"
        return False, cached_value

    async def release_on_failure(self, key):
        await self._redis_client.delete(key)

    async def store(self, key, result_json: str):
        """存储幂等值。"""
        await self._redis_client.set(key, result_json)
