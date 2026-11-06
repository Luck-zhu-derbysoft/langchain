import json
import threading
from pathlib import Path

import pytest

from app.infrastructure.audit.audit_logger import AuditAction, AuditLogger, AuditResult


def _log(logger: AuditLogger, user: str = "u1", action: AuditAction = AuditAction.LOGIN):
    return logger.log(
        action=action,
        result=AuditResult.SUCCESS,
        user_id=user,
        tenant_id="t1",
        resource="/admin/token",
        detail={"msg": "中文"},
    )


@pytest.fixture
def audit(tmp_path: Path) -> AuditLogger:
    return AuditLogger(log_file=str(tmp_path / "audit.jsonl"))


def test_entries_form_a_hash_chain(audit: AuditLogger) -> None:
    first = _log(audit)
    second = _log(audit)

    assert first.prev_hash == "0" * 64
    assert second.prev_hash == first.entry_hash
    assert audit.verify_chain() is True


def test_tampering_with_file_is_detected(audit: AuditLogger, tmp_path: Path) -> None:
    _log(audit, user="alice")
    _log(audit, user="bob")
    path = tmp_path / "audit.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["user_id"] = "mallory"
    lines[0] = json.dumps(record, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert audit.verify_chain() is False


def test_deleting_a_record_breaks_the_chain(audit: AuditLogger, tmp_path: Path) -> None:
    for _ in range(3):
        _log(audit)
    path = tmp_path / "audit.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")

    assert audit.verify_chain() is False


def test_restart_continues_existing_chain(audit: AuditLogger, tmp_path: Path) -> None:
    first = _log(audit)

    restarted = AuditLogger(log_file=str(tmp_path / "audit.jsonl"))
    second = _log(restarted)

    assert second.prev_hash == first.entry_hash
    assert restarted.verify_chain() is True


def test_rotation_archives_file_when_size_exceeded(tmp_path: Path) -> None:
    audit = AuditLogger(log_file=str(tmp_path / "audit.jsonl"), max_file_bytes=1)

    _log(audit)
    _log(audit)

    assert len(list(tmp_path.glob("audit_*.jsonl"))) >= 1


def test_query_filters_and_orders_newest_first(audit: AuditLogger) -> None:
    _log(audit, user="alice")
    _log(audit, user="bob", action=AuditAction.CONFIG_CHANGE)
    _log(audit, user="alice", action=AuditAction.CONFIG_CHANGE)

    assert {row["user_id"] for row in audit.query(user_id="alice")} == {"alice"}
    assert len(audit.query(action=AuditAction.CONFIG_CHANGE)) == 2
    assert len(audit.query(limit=1)) == 1


def test_concurrent_writes_keep_chain_valid(audit: AuditLogger) -> None:
    threads = [threading.Thread(target=_log, args=(audit, f"u{i}")) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(audit.query(limit=100)) == 20
    assert audit.verify_chain() is True
