"""死信队列 (Dead Letter Queue) 处理器

用于收集持续失败的任务，支持：
- 自动重投递 (可配置最大重投次数, 指数退避)
- 放弃标记与人工介入
- 失败原因归类统计
"""

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, cast

import redis
from pydantic.dataclasses import dataclass

from app.config.settings import settings

logger = logging.getLogger(__name__)


class DLQStatus(str, Enum):
    """死信队列状态"""

    PENDING = "pending"
    RETRYING = "retrying"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


@dataclass
class DLQEntry:
    """死信队列条目"""

    dlq_id: str
    task_id: str
    payload: dict[str, Any]
    failure_reason: str
    retry_count: int = 0
    max_retries: int = 3
    status: DLQStatus = DLQStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_retry_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "DLQEntry":
        return DLQEntry(
            dlq_id=data["dlq_id"],
            task_id=data["task_id"],
            payload=data["payload"],
            failure_reason=data["failure_reason"],
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 3),
            status=DLQStatus(data["status"]),
            created_at=data["created_at"],
            last_retry_at=data.get("last_retry_at"),
        )


class DeadLetterQueue:
    "线程安全的死信队列处理器"

    def __init__(self, max_retries: int = 3, backoff_base_seconds: int = 1) -> None:
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self._lock = threading.Lock()
        self._entries: dict[str, DLQEntry] = {}
        self.redis = self._build_redis_client()
        self._redis_index_key = "dlq:index"
        self._load_existing_entries()
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="DLQWorker"
        )
        self._worker_thread.start()

    @staticmethod
    def _build_redis_client() -> redis.Redis | None:
        """创建Redis客户端"""
        try:
            return redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=settings.redis_password,
                decode_responses=True,
                socket_timeout=2,
            )
        except ImportError:
            logger.warning("Redis module not found, please install it with `pip install redis`")
            return None

    def _entry_keys(self, dlq_id: str) -> str:
        """生成Redis键"""
        return f"dlq:entry:{dlq_id}"

    def _persist_entry(self, entry: DLQEntry) -> None:
        """将条目持久化到Redis"""
        if not self.redis:
            return
        try:
            ttl_seconds = 7 * 24 * 3600  # 条目最多保留 7 天
            self.redis.set(
                self._entry_keys(entry.dlq_id), json.dumps(entry.to_dict(), ensure_ascii=False)
            )
            self.redis.sadd(self._redis_index_key, ttl_seconds, entry.dlq_id)
        except Exception as e:
            logger.error(f"Failed to persist DLQ entry: {e}")

    def _load_existing_entries(self) -> None:
        """从Redis加载现有条目"""
        if not self.redis:
            return
        try:
            if self.redis:
                dlq_ids = self.redis.smembers(self._redis_index_key)
                for dlq_id in dlq_ids:
                    str_dlq_id = cast(str, dlq_id)
                    entry_data = self.redis.get(self._entry_keys(str_dlq_id))
                    if entry_data:
                        entry = DLQEntry.from_dict(json.loads(entry_data))
                        self._entries[str_dlq_id] = entry
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")

    def add(self, task_id: str, payload: dict[str, Any], failure_reason: str) -> DLQEntry:
        """添加死信队列条目"""
        with self._lock:
            dlq_id = f"dlq_{uuid.uuid4().hex[:12]}"
            entry = DLQEntry(
                dlq_id=dlq_id,
                task_id=task_id,
                payload=payload,
                failure_reason=failure_reason,
                max_retries=self.max_retries,
            )
            self._entries[dlq_id] = entry
            self._persist_entry(entry)
        logger.warning(f"Added DLQ entry: {entry}")
        return entry

    def retry(self, dlq_id: str, execute: Callable[[dict[str, Any]], Any]) -> bool:
        """重试死信队列条目"""
        with self._lock:
            entry = self._entries.get(dlq_id)
            if entry is None or entry.status in {DLQStatus.RESOLVED, DLQStatus.ABANDONED}:
                logger.warning(f"DLQ entry not found or already resolved/abandoned: {dlq_id}")
                return False
            if entry.retry_count >= entry.max_retries:
                entry.status = DLQStatus.ABANDONED
                self._persist_entry(entry)
                logger.warning(f"DLQ entry abandoned after max retries: {entry}")
                return False
            entry.retry_count += 1
            entry.last_retry_at = datetime.now(UTC).isoformat()
            entry.status = DLQStatus.RETRYING
            self._persist_entry(entry)
        time.sleep(self.backoff_base_seconds * (2 ** max(entry.retry_count - 1, 0)))
        try:
            execute(entry.payload)
        except Exception as e:
            logger.error(f"Retry failed for DLQ entry {dlq_id}: {e}")
            with self._lock:
                entry = self._entries.get(dlq_id)
                if entry:
                    entry.status = DLQStatus.PENDING
                    self._persist_entry(entry)
            return False
        with self._lock:
            entry.status = DLQStatus.RESOLVED
            self._persist_entry(entry)
        logger.info(f"DLQ entry resolved after retry: {entry}")
        return True

    def list_pending(self) -> list[DLQEntry]:
        """列出所有待处理的死信队列条目"""
        with self._lock:
            return [entry for entry in self._entries.values() if entry.status == DLQStatus.PENDING]

    def stats(self) -> dict[str, Any]:
        """统计死信队列条目状态"""
        with self._lock:
            entries = list(self._entries.values())
            return {
                "total": len(entries),
                "pending": sum(1 for e in entries if e.status == DLQStatus.PENDING),
                "retrying": sum(1 for e in entries if e.status == DLQStatus.RETRYING),
                "resolved": sum(1 for e in entries if e.status == DLQStatus.RESOLVED),
                "abandoned": sum(1 for e in entries if e.status == DLQStatus.ABANDONED),
            }

    def _worker_loop(self) -> None:
        """后台线程循环处理死信队列"""
        while True:
            try:
                pending_entries = self.list_pending()
                for entry in pending_entries:
                    if entry.retry_count > entry.max_retries:
                        logger.info(f"Abandoning DLQ entry: {entry}")
                        entry.status = DLQStatus.ABANDONED
                        self._persist_entry(entry)
                        continue
                    logger.info(f"Retrying DLQ entry: {entry}")
                    self.retry(entry.dlq_id, lambda payload: payload)
            except Exception as e:
                logger.error(f"Error in DLQ worker loop: {e}")
            time.sleep(5)  # 每 5 秒检查一次


dead_letter_queue = DeadLetterQueue()
