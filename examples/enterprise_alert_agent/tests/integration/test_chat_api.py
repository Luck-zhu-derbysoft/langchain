import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _post_stream(payload: dict) -> tuple[int, list[dict]]:
    """POST /chat/stream 并解析 SSE 事件流，返回 (status_code, events)。"""
    with client.stream("POST", "/chat/stream", json=payload) as resp:
        status_code = resp.status_code
        raw = resp.read().decode("utf-8")
    events: list[dict] = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if block.startswith("data: "):
            events.append(json.loads(block[len("data: ") :]))
    return status_code, events


def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


def test_chat_validation() -> None:
    status_code, _ = _post_stream({"query": ""})
    assert status_code == 422


def test_retrieval_hit_after_ingest() -> None:
    source_id = "eval-doc-001"
    content = "值班告警升级策略：连续3次失败后必须升级到二线。"

    r1 = client.post(
        "/ingest/text",
        json={"content": content, "source_id": source_id, "metadata": {"category": "eval"}},
    )
    assert r1.status_code == 200

    status_code, events = _post_stream({"query": "连续失败几次需要升级到二线？"})
    assert status_code == 200
    done = next(e for e in events if e.get("type") == "done")
    citations = done.get("citations") or []
    assert len(citations) > 0
    assert any(c["source_id"] == source_id for c in citations)


def test_time_query_returns_today() -> None:
    tz = "Asia/Shanghai"
    today = datetime.now(ZoneInfo(tz))
    expect_date = f"{today.year} 年 {today.month} 月 {today.day} 日"

    status_code, events = _post_stream({"query": "今天是什么日子", "thread_id": "t-time-1"})
    assert status_code == 200
    done = next(e for e in events if e.get("type") == "done")
    assert expect_date in done.get("answer", "")
