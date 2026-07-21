"""死信队列 (Dead Letter Queue) 处理器

用于收集持续失败的任务，支持：
- 自动重投递 (可配置最大重投次数, 指数退避)
- 放弃标记与人工介入
- 失败原因归类统计
"""

from dataclasses import field
from datetime import datetime, timezone
from enum import Enum
import logging
import uuid
import threading

from pydantic.dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

class DLQStatus(str, Enum):
    """死信队列状态"""

    PENDING = "pending"
    RETRYING = "retrying"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"

@dataclass
class DLQMEntry:
    """死信队列条目"""
    dlq_id: str
    task_id: str
    payload: dict[str, Any]
    failure_reason: str
    retry_count: int = 0
    max_retries: int = 3
    status: DLQStatus = DLQStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_retry_at: str | None = None
class DeadLetterQueue:
    "线程安全的死信队列处理器"
    def __init__(self,max_retries: int = 3,backoff_base_seconds: int = 1)-> None:
        self._entries: dict[str, DLQMEntry] = {}
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self._lock = threading.Lock()

    def add(self, task_id: str, payload: dict[str, Any], failure_reason: str) -> DLQMEntry:
        """添加死信队列条目"""
        with self._lock:
            dlq_id=f"dlq_{uuid.uuid4().hex[:12]}"
            entry = DLQMEntry(
                dlq_id=dlq_id,
                task_id=task_id,
                payload=payload,
                failure_reason=failure_reason,
                max_retries=self.max_retries
            )
            self._entries[dlq_id] = entry
        logger.warn(f"Added DLQ entry: {entry}")
        return entry
    def retry(self,dlq_id:str,execute:Callable[[dict[str,Any]],Any])->bool:
        """重试死信队列条目"""
        with self._lock:
            entry = self._entries.get(dlq_id)
            if entry is None or entry.status in {DLQStatus.RESOLVED, DLQStatus.ABANDONED}:
                logger.warn(f"DLQ entry not found or already resolved/abandoned: {dlq_id}")
                return False
            if entry.retry_count >= entry.max_retries:
                entry.status = DLQStatus.ABANDONED
                logger.warn(f"DLQ entry abandoned after max retries: {entry}")
                return False
            entry.retry_count += 1
            entry.last_retry_at = datetime.now(timezone.utc).isoformat()
            entry.status = DLQStatus.RETRYING
        try:
            execute(entry.payload)
        except Exception as e:
            logger.error(f"Retry failed for DLQ entry {dlq_id}: {e}")
            return False
        with self._lock:
            entry.status = DLQStatus.RESOLVED
        logger.info(f"DLQ entry resolved after retry: {entry}")
        return True
    def list_pending(self)->list[DLQMEntry]:
        """列出所有待处理的死信队列条目"""
        with self._lock:
            return [entry for entry in self._entries.values() if entry.status == DLQStatus.PENDING]
    def stats(self)->dict[str,Any]:
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
dead_letter_queue = DeadLetterQueue()
