from fastapi.testclient import TestClient

from app.main import app
from datetime import datetime
from zoneinfo import ZoneInfo

client = TestClient(app)

def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"

def test_chat_validation() -> None:
    resp = client.post("/chat", json={"query": ""})
    assert resp.status_code == 422


def test_retrieval_hit_after_ingest() -> None:
    source_id = "eval-doc-001"
    content = "值班告警升级策略：连续3次失败后必须升级到二线。"

    r1 = client.post("/ingest/text", json={
        "content": content,
        "source_id": source_id,
        "metadata": {"category": "eval"}
    })
    assert r1.status_code == 200

    r2 = client.post("/chat", json={"query": "连续失败几次需要升级到二线？"})
    assert r2.status_code == 200
    body = r2.json()
    assert len(body["citations"]) > 0
    assert any(c["source_id"] == source_id for c in body["citations"])


def test_time_query_returns_today() -> None:
    tz = "Asia/Shanghai"
    today = datetime.now(ZoneInfo(tz))
    expect_date = f"{today.year} 年 {today.month} 月 {today.day} 日"

    resp = client.post("/chat", json={"query": "今天是什么日子", "thread_id": "t-time-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert expect_date in body["answer"]
