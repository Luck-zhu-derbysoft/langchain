import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["action"] = self.action.value
        data["result"] = self.result.value
        return data
class AuditLogger:
        def __init__(self, log_file: str = "./data/audit/audit.jsonl", max_memory_entries: int = 10000) -> None:
            self._entrys: list[AuditLogEntry] = []
            self._max_memory_entries = max_memory_entries
            self._lock = threading.Lock()
            self._log_file = Path(log_file)
            self._log_file.parent.mkdir(parents=True, exist_ok=True)
        def log(
                  self,
                  action: AuditAction,
                  result: AuditResult,
                  user_id: str,
                  tenant_id: str,
                  resource: str,
                  detail: dict[str, Any] = {},
                  ip_address: str | None = None,
                  request_id: str | None = None,
                ) -> AuditLogEntry:
            entry = AuditLogEntry(
                audit_id=f"audit_{uuid.uuid4().hex[:12]}",
                timestamp=datetime.now(timezone.utc).isoformat(),
                action=action,
                result=result,
                user_id=user_id,
                tenant_id=tenant_id,
                resource=resource,
                detail=detail,
                ip_address=ip_address,
                request_id=request_id,
            )
            with self._lock:
                self._entrys.append(entry)
                if len(self._entrys) > self._max_memory_entries:
                    self._entrys = self._entrys[-self._max_memory_entries :]
                    with open(self._log_file, "a") as f:
                        f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
                        f.write("\n")
            return entry
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
audit_logger = AuditLogger()
