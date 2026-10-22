import hashlib
import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any


class AuditAction(str, Enum):
    CHAT_REQUEST = "chat_request"
    CONFIG_CHANGE = "config_change"
    INTERVENTION = "intervention"
    LOGIN = "login"
    PERMISSION_DENIED = "permission_denied"


class AuditResult(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


@dataclass
class AuditLogEntry:
    audit_id: str
    timestamp: str
    action: AuditAction
    result: AuditResult
    user_id: str
    tenant_id: str
    resource: str
    detail: dict[str, Any] = field(default_factory=dict)
    ip_address: str | None = None
    request_id: str | None = None
    prev_hash: str = ""  # 链接上一条记录的 hash
    entry_hash: str = ""  # 本条记录内容 + prev_hash 的 sha256

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["action"] = self.action.value
        data["result"] = self.result.value
        return data


class AuditLogger:
    _GENESIS_HASH = "0" * 64

    def __init__(
        self,
        log_file: str = "./data/audit/audit.jsonl",
        max_memory_entries: int = 10000,
        max_file_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self._entrys: list[AuditLogEntry] = []
        self._max_memory_entries = max_memory_entries
        self._lock = threading.Lock()
        self._log_file = Path(log_file)
        self._log_file.parent.mkdir(parents=True, exist_ok=True)
        self._max_file_bytes = max_file_bytes
        self._last_hash = self._load_last_hash()  # 重启后续链，而不是每次从 0 开始

    def _load_last_hash(self) -> str:
        if not self._log_file.exists():
            return self._GENESIS_HASH
        with open(self._log_file, "r", encoding="utf-8") as f:
            last_line = None
            for line in f:
                last_line = line
            if not last_line:
                return self._GENESIS_HASH
            try:
                last_entry = json.loads(last_line)
                return last_entry.get("entry_hash", self._GENESIS_HASH)
            except json.JSONDecodeError:
                return self._GENESIS_HASH

    def _rotate_if_needed(self) -> None:
        if self._log_file.exists() and self._log_file.stat().st_size >= self._max_file_bytes:
            rotated_file = self._log_file.with_name(
                f"{self._log_file.stem}_{datetime.now(UTC):%Y%m%dT%H%M%S}{self._log_file.suffix}"
            )
            self._log_file.rename(rotated_file)

    def log(
        self,
        action: AuditAction,
        result: AuditResult,
        user_id: str,
        tenant_id: str,
        resource: str,
        detail: dict[str, Any] | None = None,
        ip_address: str | None = None,
        request_id: str | None = None,
    ) -> AuditLogEntry:
        entry = AuditLogEntry(
            audit_id=f"audit_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.now(UTC).isoformat(),
            action=action,
            result=result,
            user_id=user_id,
            tenant_id=tenant_id,
            resource=resource,
            detail=detail or {},
            ip_address=ip_address,
            request_id=request_id,
        )
        with self._lock:
            entry.prev_hash = self._last_hash
            entry.entry_hash = self._compute_entry_hash(entry)
            self._last_hash = entry.entry_hash
            self._append_to_file(entry)
            self._entrys.append(entry)
            if len(self._entrys) > self._max_memory_entries:
                self._entrys = self._entrys[-self._max_memory_entries :]
        return entry

    def _append_to_file(self, entry: AuditLogEntry) -> None:
        self._rotate_if_needed()
        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _compute_entry_hash(self, entry: AuditLogEntry) -> str:
        payload = entry.to_dict()
        payload.pop("entry_hash", None)  # 不将 entry_hash 本身纳入哈希计算
        entry_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(entry_json.encode("utf-8")).hexdigest()

    def query(
        self,
        user_id: str | None = None,
        action: AuditAction | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._entrys)
        if user_id:
            rows = [row for row in rows if row.user_id == user_id]
        if action:
            rows = [row for row in rows if row.action == action]
        rows.sort(key=lambda x: x.timestamp, reverse=True)
        return [row.to_dict() for row in rows[:limit]]

    def verify_chain(self) -> bool:
        """从文件重算哈希链，检测历史记录是否被篡改/删除。可配一个定时任务或管理端点调用。"""
        if not self._log_file.exists():
            return True
        prev_hash = self._GENESIS_HASH
        with open(self._log_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                record = json.loads(line)
                recorded_hash = record.pop("entry_hash", None)
                if record.get("prev_hash") != prev_hash:
                    return False
                expected = hashlib.sha256(
                    json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")
                ).hexdigest()
                if recorded_hash != expected:
                    return False
                prev_hash = recorded_hash
        return True

    def flush_to_file(self) -> None:
        return


audit_logger = AuditLogger()
