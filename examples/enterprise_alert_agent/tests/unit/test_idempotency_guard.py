# tests/unit/test_idempotency_guard.py
from typing import Any

import pytest

from app.infrastructure.cache.idempotency_guard import IdempotencyGuard


class FakeRedis:
    """仅实现 IdempotencyGuard 用到的 SET NX / GET / DEL 语义。"""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.last_ttl: int | None = None

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> Any:
        if nx and key in self.data:
            return None
        self.data[key] = value
        self.last_ttl = ex
        return True

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.mark.asyncio
async def test_first_acquire_wins_and_duplicate_sees_processing(redis: FakeRedis) -> None:
    guard = IdempotencyGuard(redis, ttl=30)  # type: ignore[arg-type]

    assert await guard.acquire("k1") == (True, None)
    assert redis.last_ttl == 30
    assert await guard.acquire("k1") == (False, "Processing")


@pytest.mark.asyncio
async def test_completed_request_returns_cached_result(redis: FakeRedis) -> None:
    guard = IdempotencyGuard(redis)  # type: ignore[arg-type]
    await guard.acquire("k1")
    await guard.store("k1", '{"chunks": 3}')

    assert await guard.acquire("k1") == (False, '{"chunks": 3}')


@pytest.mark.asyncio
async def test_release_on_failure_allows_retry(redis: FakeRedis) -> None:
    guard = IdempotencyGuard(redis)  # type: ignore[arg-type]
    await guard.acquire("k1")

    await guard.release_on_failure("k1")

    assert await guard.acquire("k1") == (True, None)


@pytest.mark.asyncio
async def test_without_redis_never_acquires() -> None:
    guard = IdempotencyGuard(None)

    acquired, reason = await guard.acquire("k1")

    assert acquired is False
    assert "not initialized" in str(reason)
    await guard.store("k1", "x")
    await guard.release_on_failure("k1")
