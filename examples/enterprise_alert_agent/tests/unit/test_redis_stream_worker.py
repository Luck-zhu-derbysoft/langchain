import json
from typing import Any

import pytest

from app.infrastructure.queue import redis_stream
from app.infrastructure.queue.redis_stream import RedisStreamWorker


class FakeRedis:
    def __init__(self) -> None:
        self.acked: list[tuple[str, str, str]] = []

    async def xack(self, stream: str, group: str, message_id: str) -> int:
        self.acked.append((stream, group, message_id))
        return 1


class FakeDLQ:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def add(self, task_id: str, payload: dict[str, Any], failure_reason: str) -> None:
        self.entries.append({"task_id": task_id, "payload": payload, "reason": failure_reason})


@pytest.fixture
def dlq(monkeypatch: pytest.MonkeyPatch) -> FakeDLQ:
    fake = FakeDLQ()
    monkeypatch.setattr(redis_stream, "dead_letter_queue", fake)
    return fake


def _worker(redis: FakeRedis, handler) -> RedisStreamWorker:  # type: ignore[no-untyped-def]
    return RedisStreamWorker(redis, handler=handler, consumer_name="test")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_success_acks_and_skips_dlq(dlq: FakeDLQ) -> None:
    redis, received = FakeRedis(), []

    async def handler(payload: dict[str, Any]) -> None:
        received.append(payload)

    await _worker(redis, handler)._handle_message("1-0", {"payload": json.dumps({"a": 1})})

    assert received == [{"a": 1}]
    assert redis.acked == [("tasks:ingest", "ingest-workers", "1-0")]
    assert dlq.entries == []


@pytest.mark.asyncio
async def test_handler_failure_goes_to_dlq_and_is_acked(dlq: FakeDLQ) -> None:
    redis = FakeRedis()

    async def handler(_payload: dict[str, Any]) -> None:
        raise RuntimeError("downstream down")

    await _worker(redis, handler)._handle_message("2-0", {"payload": "{}"})

    assert [entry["task_id"] for entry in dlq.entries] == ["2-0"]
    assert "downstream down" in dlq.entries[0]["reason"]
    assert len(redis.acked) == 1  # 失败后 ACK，避免毒消息被反复投递


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{}, {"payload": "not-json"}])
async def test_malformed_message_goes_to_dlq(dlq: FakeDLQ, fields: dict[str, str]) -> None:
    redis = FakeRedis()

    async def handler(_payload: dict[str, Any]) -> None:
        raise AssertionError("handler must not run for malformed messages")

    await _worker(redis, handler)._handle_message("3-0", fields)

    assert len(dlq.entries) == 1
    assert len(redis.acked) == 1
